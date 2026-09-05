"""Data access for the orders module."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orderflow.modules.orders.models import Order, OrderItem
from orderflow.modules.orders.schemas import OrderFilters
from orderflow.shared.pagination import PageParams


class OrderRepository:
    """Queries over ``orders`` and ``order_items``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, order: Order) -> Order:
        self._session.add(order)
        return order

    async def get_by_id(
        self, order_id: uuid.UUID, *, owner_id: uuid.UUID | None = None
    ) -> Order | None:
        stmt = select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
        if owner_id is not None:
            stmt = stmt.where(Order.user_id == owner_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_update(
        self, order_id: uuid.UUID, *, owner_id: uuid.UUID | None = None
    ) -> Order | None:
        stmt = (
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.id == order_id)
            .with_for_update(of=Order)
        )
        if owner_id is not None:
            stmt = stmt.where(Order.user_id == owner_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_orders(
        self,
        filters: OrderFilters,
        page: PageParams,
        *,
        owner_id: uuid.UUID | None = None,
    ) -> tuple[list[Order], int]:
        base = self._apply_filters(select(Order), filters, owner_id)

        total_result = await self._session.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = int(total_result.scalar_one())

        page_stmt = (
            base.options(selectinload(Order.items))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        result = await self._session.execute(page_stmt)
        return list(result.scalars().unique().all()), total

    @staticmethod
    def _apply_filters(
        stmt: Select[tuple[Order]],
        filters: OrderFilters,
        owner_id: uuid.UUID | None,
    ) -> Select[tuple[Order]]:
        if owner_id is not None:
            stmt = stmt.where(Order.user_id == owner_id)
        if filters.status is not None:
            stmt = stmt.where(Order.status == filters.status)
        return stmt

    async def count_items(self, order_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(OrderItem).where(OrderItem.order_id == order_id)
        )
        return int(result.scalar_one())
