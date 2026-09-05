"""Data access for the products module."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.modules.products.models import Product
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

        # Deterministic ordering. Sorting by created_at alone is not stable —
        # two products inserted in the same transaction share a timestamp, and
        # the same row could then appear on two pages, or on none.
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
            # Parameterised bind, never string interpolation: this is the
            # difference between a filter and an SQL injection. The wildcards
            # in the user's own input are escaped so `%` cannot be used to
            # force a full scan.
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


def _escape_like(value: str) -> str:
    """Neutralise LIKE metacharacters in user input."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
