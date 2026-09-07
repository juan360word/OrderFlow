"""Data access for the products module."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import CursorResult, Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.modules.products.models import Product, ProductImage
from orderflow.modules.products.schemas import ProductFilters
from orderflow.shared.pagination import PageParams


class ProductRepository:
    """Queries over the ``products`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self, product_id: uuid.UUID, *, include_inactive: bool = False
    ) -> Product | None:
        stmt = select(Product).where(Product.id == product_id)
        if not include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_many_active(self, product_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Product]:
        if not product_ids:
            return {}
        result = await self._session.execute(
            select(Product).where(Product.id.in_(list(product_ids)), Product.is_active.is_(True))
        )
        return {product.id: product for product in result.scalars().all()}

    async def get_by_sku(self, sku: str) -> Product | None:
        result = await self._session.execute(select(Product).where(Product.sku == sku))
        return result.scalar_one_or_none()

    async def list_products(
        self, filters: ProductFilters, page: PageParams
    ) -> tuple[list[Product], int]:
        """Return one page plus the total row count.

        The count runs as a separate query over the same filter. It could be
        folded into the page query with a window function, but the separate
        count is easier to read and, more importantly, easy to drop later:
        counting every matching row is the part that stops scaling first.
        """
        base = self._apply_filters(select(Product), filters)

        total_result = await self._session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = int(total_result.scalar_one())

        page_stmt = (
            base.order_by(Product.created_at.desc(), Product.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        result = await self._session.execute(page_stmt)
        return list(result.scalars().all()), total

    @staticmethod
    def _apply_filters(
        stmt: Select[tuple[Product]], filters: ProductFilters
    ) -> Select[tuple[Product]]:
        if not filters.include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        if filters.search:
            pattern = f"%{_escape_like(filters.search.lower())}%"
            stmt = stmt.where(func.lower(Product.name).like(pattern, escape="\\"))
        if filters.min_price is not None:
            stmt = stmt.where(Product.price >= filters.min_price)
        if filters.max_price is not None:
            stmt = stmt.where(Product.price <= filters.max_price)
        return stmt

    def add(self, product: Product) -> Product:
        self._session.add(product)
        return product

    async def exists_active(self, product_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(Product.id).where(Product.id == product_id, Product.is_active.is_(True)).limit(1)
        )
        return result.first() is not None

    async def price_of(self, product_id: uuid.UUID) -> Decimal | None:
        result = await self._session.execute(select(Product.price).where(Product.id == product_id))
        return result.scalar_one_or_none()

    async def delete(self, product: Product) -> None:
        """Really remove the row.

        Only safe for a product no order references. ``order_items`` declares
        ``ON DELETE RESTRICT``, so PostgreSQL refuses the rest - which is the
        guarantee we want: financial history cannot be erased by deleting a
        product, no matter what the application layer believes.
        """
        await self._session.delete(product)

    # -- Images -------------------------------------------------------------
    #
    # Every query above reads `products` and never joins `product_images`.
    # That separation is the reason the catalogue does not slow down as
    # pictures are added.

    async def get_image(self, product_id: uuid.UUID) -> ProductImage | None:
        """Load the bytes. The only place in the codebase that does."""
        return await self._session.get(ProductImage, product_id)

    async def upsert_image(self, product_id: uuid.UUID, *, data: bytes, content_type: str) -> None:
        """Store or replace a product picture.

        One statement rather than "select, then insert or update": a product
        has at most one picture, and two admins uploading at the same moment
        would otherwise both see "no row" and both insert. The primary key
        turns that race into an update of the row that lost.
        """
        stmt = insert(ProductImage).values(product_id=product_id, data=data, byte_size=len(data))
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["product_id"],
                set_={
                    "data": stmt.excluded.data,
                    "byte_size": stmt.excluded.byte_size,
                    "updated_at": func.now(),
                },
            )
        )
        await self._session.execute(
            update(Product).where(Product.id == product_id).values(image_content_type=content_type)
        )

    async def delete_image(self, product_id: uuid.UUID) -> bool:
        """Remove the picture. Returns whether there was one."""
        result = await self._session.execute(
            delete(ProductImage).where(ProductImage.product_id == product_id)
        )
        await self._session.execute(
            update(Product).where(Product.id == product_id).values(image_content_type=None)
        )
        return bool(result.rowcount) if isinstance(result, CursorResult) else False


def _escape_like(value: str) -> str:
    """Neutralise LIKE metacharacters in user input."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
