"""Product catalogue business rules.

Module boundary note: this service calls ``InventoryService`` — never the
``inventory`` table or its repository. The dependency points one way
(products → inventory) and never back; inventory relies on the database
foreign key to know a product exists, so there is no import cycle and either
module could be extracted into its own service later.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.cache import CacheClient
from orderflow.core.errors import ConflictError, NotFoundError, ValidationError
from orderflow.core.logging import get_logger
from orderflow.modules.inventory.service import InventoryService
from orderflow.modules.products.images import detect_image_type
from orderflow.modules.products.models import Product, ProductImage
from orderflow.modules.products.repository import ProductRepository
from orderflow.modules.products.schemas import ProductCreate, ProductFilters, ProductUpdate
from orderflow.shared.pagination import PageParams

logger = get_logger(__name__)


class ProductService:
    """Read and write the catalogue."""

    CACHE_NAMESPACE = "product"

    def __init__(
        self,
        session: AsyncSession,
        inventory: InventoryService,
        cache: CacheClient | None = None,
        cache_ttl_seconds: int = 300,
        image_max_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._session = session
        self._repo = ProductRepository(session)
        self._inventory = inventory
        self._cache = cache
        self._cache_ttl = cache_ttl_seconds
        self._image_max_bytes = image_max_bytes

    async def create(self, payload: ProductCreate, *, created_by: uuid.UUID) -> Product:
        """Create a product and, atomically, its inventory row.

        Both writes share the request's transaction, so a failure in either
        leaves no half-created product with no stock record. That atomicity is
        exactly why both modules live in one deployable — across two services
        this would need a saga.
        """
        product = Product(
            sku=payload.sku,
            name=payload.name,
            description=payload.description,
            price=payload.price,
            currency=payload.currency,
        )
        self._repo.add(product)

        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(
                f"A product with SKU '{payload.sku}' already exists.",
                details={"sku": payload.sku},
            ) from exc

        await self._inventory.initialize_stock(product.id, quantity=payload.initial_stock)

        logger.info(
            "product_created",
            product_id=str(product.id),
            sku=product.sku,
            actor_id=str(created_by),
        )
        return product

    async def get(self, product_id: uuid.UUID, *, include_inactive: bool = False) -> Product:
        product = await self._repo.get_by_id(product_id, include_inactive=include_inactive)
        if product is None:
            raise NotFoundError("Product not found.")
        return product

    async def get_cached(self, product_id: uuid.UUID) -> dict[str, Any]:
        """Read one active product through the cache.

        Only the public catalogue view is cached. A product is read far more
        often than it changes, its shape is small, and a few seconds of
        staleness on a name or description costs nothing. Stock is deliberately
        *not* cached here: it changes on every purchase and a stale count would
        promise availability that no longer exists.
        """
        if self._cache is None or not self._cache.available:
            return self._serialize(await self.get(product_id))

        cache_key = self._cache.key(self.CACHE_NAMESPACE, str(product_id))
        cached = await self._cache.get_json(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        payload = self._serialize(await self.get(product_id))
        if await self._cache.acquire_rebuild_lock(cache_key):
            await self._cache.set_json(cache_key, payload, ttl_seconds=self._cache_ttl)
        return payload

    async def _invalidate(self, product_id: uuid.UUID) -> None:
        """Drop the cached copy after a write.

        Delete, not overwrite: two concurrent updates could otherwise write
        their values in the opposite order to the database and leave the cache
        holding the losing one. A deleted key forces the next reader to read
        the row that actually won.
        """
        if self._cache is None:
            return
        await self._cache.delete(self._cache.key(self.CACHE_NAMESPACE, str(product_id)))

    @staticmethod
    def _serialize(product: Product) -> dict[str, Any]:
        return {
            "id": str(product.id),
            "sku": product.sku,
            "name": product.name,
            "description": product.description,
            "price": str(product.price),
            "currency": product.currency,
            "is_active": product.is_active,
            "created_at": product.created_at.isoformat(),
            "updated_at": product.updated_at.isoformat(),
            # The marker, not the finished URL: the response model builds the
            # address from it, so a cached product and a fresh one go through
            # exactly the same code. Without this a cached copy would answer
            # "no picture" for a whole TTL after one is uploaded.
            "image_content_type": product.image_content_type,
        }

    async def get_many_active(self, product_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Product]:
        """Fetch several active products at once, keyed by id.

        The public batch accessor other modules use, so building an order costs
        one query instead of one per line.
        """
        return await self._repo.get_many_active(product_ids)

    async def list_products(
        self, filters: ProductFilters, page: PageParams
    ) -> tuple[list[Product], int]:
        return await self._repo.list_products(filters, page)

    async def update(
        self, product_id: uuid.UUID, payload: ProductUpdate, *, updated_by: uuid.UUID
    ) -> Product:
        """Apply a partial update.

        ``include_inactive=True`` so an admin can reactivate a withdrawn
        product — the caller here is already known to be an admin.
        """
        product = await self.get(product_id, include_inactive=True)
        changes = payload.changed_fields()
        if not changes:
            return product

        for field, value in changes.items():
            setattr(product, field, value)

        await self._session.flush()
        await self._invalidate(product_id)
        logger.info(
            "product_updated",
            product_id=str(product.id),
            fields=sorted(changes),
            actor_id=str(updated_by),
        )
        return product

    async def set_image(
        self, product_id: uuid.UUID, *, data: bytes, declared_type: str | None, actor_id: uuid.UUID
    ) -> None:
        """Attach a picture to a product.

        The declared content type is a hint from the browser and nothing more -
        a client is free to label an executable as a PNG. What is trusted is
        the file's own leading bytes, so the type served later is the type the
        content actually is.
        """
        await self.get(product_id, include_inactive=True)

        if not data:
            raise ValidationError("The uploaded file is empty.")
        if len(data) > self._image_max_bytes:
            raise ValidationError(
                f"The image must not exceed {self._image_max_bytes // 1024} KB.",
                details={"byte_size": len(data), "limit": self._image_max_bytes},
            )

        content_type = detect_image_type(data)
        if content_type is None:
            raise ValidationError(
                "Unsupported image format. Use JPEG, PNG, WebP or GIF.",
                details={"declared_type": declared_type},
            )

        await self._repo.upsert_image(product_id, data=data, content_type=content_type)
        await self._session.flush()
        await self._invalidate(product_id)
        logger.info(
            "product_image_set",
            product_id=str(product_id),
            content_type=content_type,
            byte_size=len(data),
            actor_id=str(actor_id),
        )

    async def get_image(self, product_id: uuid.UUID) -> tuple[bytes, str]:
        """Return the picture and its content type, for serving."""
        product = await self.get(product_id, include_inactive=True)
        image: ProductImage | None = await self._repo.get_image(product_id)
        if image is None or product.image_content_type is None:
            raise NotFoundError("This product has no image.")
        return image.data, product.image_content_type

    async def remove_image(self, product_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        await self.get(product_id, include_inactive=True)
        removed = await self._repo.delete_image(product_id)
        if not removed:
            raise NotFoundError("This product has no image.")
        await self._session.flush()
        await self._invalidate(product_id)
        logger.info("product_image_removed", product_id=str(product_id), actor_id=str(actor_id))

    async def delete_permanently(self, product_id: uuid.UUID, *, deleted_by: uuid.UUID) -> None:
        """Erase a product that was never sold.

        Whether it was ever sold is not asked here, and deliberately so. This
        module must not read the orders tables - the architecture allows a
        module to reach another only through its service - and a question
        answered before the delete would be stale by the time the delete runs:
        an order can be placed in between. So the delete is simply attempted,
        and ``order_items``' ON DELETE RESTRICT is what answers, atomically.
        The database is the only place that can decide this without a race.

        Inventory rows and the picture cascade away with it. Order history
        cannot, which is precisely why a sold product may only be withdrawn.
        """
        product = await self.get(product_id, include_inactive=True)
        sku = product.sku

        await self._repo.delete(product)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(
                "This product appears in an order and cannot be deleted. "
                "Withdraw it from sale instead, which hides it without "
                "destroying the history that refers to it.",
                details={"sku": sku},
            ) from exc
        await self._invalidate(product_id)
        logger.info(
            "product_deleted", product_id=str(product_id), sku=sku, actor_id=str(deleted_by)
        )

    async def deactivate(self, product_id: uuid.UUID, *, deleted_by: uuid.UUID) -> None:
        """Withdraw a product from sale.

        A soft delete, not a DELETE: order history and inventory ledgers
        reference this row, and destroying it would either cascade away real
        financial records or fail on a foreign key.
        """
        product = await self.get(product_id, include_inactive=True)
        if not product.is_active:
            return
        product.is_active = False
        await self._session.flush()
        await self._invalidate(product_id)
        logger.info("product_deactivated", product_id=str(product.id), actor_id=str(deleted_by))
