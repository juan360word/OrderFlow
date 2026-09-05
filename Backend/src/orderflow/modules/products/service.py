"""Product catalogue business rules.

Module boundary note: this service calls ``InventoryService`` — never the
``inventory`` table or its repository. The dependency points one way
(products → inventory) and never back; inventory relies on the database
foreign key to know a product exists, so there is no import cycle and either
module could be extracted into its own service later.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.errors import ConflictError, NotFoundError
from orderflow.core.logging import get_logger
from orderflow.modules.inventory.service import InventoryService
from orderflow.modules.products.models import Product
from orderflow.modules.products.repository import ProductRepository
from orderflow.modules.products.schemas import ProductCreate, ProductFilters, ProductUpdate
from orderflow.shared.pagination import PageParams

logger = get_logger(__name__)


class ProductService:
    """Read and write the catalogue."""

    def __init__(self, session: AsyncSession, inventory: InventoryService) -> None:
        self._session = session
        self._repo = ProductRepository(session)
        self._inventory = inventory

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

    async def list(self, filters: ProductFilters, page: PageParams) -> tuple[list[Product], int]:
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
        logger.info(
            "product_updated",
            product_id=str(product.id),
            fields=sorted(changes),
            actor_id=str(updated_by),
        )
        return product

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
        logger.info("product_deactivated", product_id=str(product.id), actor_id=str(deleted_by))
