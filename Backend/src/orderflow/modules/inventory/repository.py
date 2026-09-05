"""Data access for inventory — where the concurrency control actually lives.

This module implements the two correct ways to decrement stock under
concurrency, and one incorrect one kept for the tests to demonstrate.

The race being prevented
------------------------
With ``quantity_available = 1`` and two simultaneous buyers:

    T1: SELECT available -> 1        T2: SELECT available -> 1
    T1: 1 >= 1, ok                   T2: 1 >= 1, ok
    T1: UPDATE available = 0         T2: UPDATE available = 0

Both succeed. One unit was sold twice. Note that a transaction alone does *not*
fix this: PostgreSQL's default isolation level, READ COMMITTED, guarantees that
each statement sees a consistent snapshot, not that a value read at the start of
a transaction is still true when you write it. The read and the write must be
made indivisible, and that requires either a lock or a conditional write.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.modules.inventory.models import Inventory, InventoryReservation, ReservationStatus


class InventoryRepository:
    """Stock reads and writes, including the locking strategies."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, product_id: uuid.UUID) -> Inventory | None:
        result = await self._session.execute(
            select(Inventory).where(Inventory.product_id == product_id)
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, product_id: uuid.UUID) -> Inventory | None:
        """Read one inventory row and hold an exclusive row lock on it.

        ``SELECT ... FOR UPDATE`` takes a row-level exclusive lock. A second
        transaction issuing the same statement *blocks* until the first commits
        or rolls back, and then re-reads the committed value. That serialises
        the read-modify-write, which is precisely what the race needs.

        Cost: the second buyer waits. Under heavy contention for one hot
        product, requests queue up on this row. ``lock_timeout`` (set on the
        connection in ``core.database``) bounds that wait so a stuck
        transaction produces an error instead of a hung request.

        ``FOR UPDATE`` is meaningless outside a transaction — SQLAlchemy always
        has one open here, and the lock is released at commit/rollback.
        """
        result = await self._session.execute(
            select(Inventory).where(Inventory.product_id == product_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def reserve_pessimistic(self, product_id: uuid.UUID, quantity: int) -> Inventory | None:
        """Lock the row, verify, then write. Returns None if stock is short.

        Readable and easy to extend: once the row is locked you can run any
        amount of business logic before writing, and it stays correct. That is
        the reason to prefer this when a decision needs more than one column —
        pricing tiers, per-customer limits, multi-warehouse allocation.
        """
        inventory = await self.get_for_update(product_id)
        if inventory is None or inventory.quantity_available < quantity:
            return None

        inventory.quantity_available -= quantity
        inventory.quantity_reserved += quantity
        inventory.version += 1
        await self._session.flush()
        return inventory

    async def reserve_atomic(self, product_id: uuid.UUID, quantity: int) -> Inventory | None:
        """Do the check and the write in one statement.

        ``UPDATE ... WHERE quantity_available >= :quantity`` is evaluated by
        PostgreSQL while it holds the row lock it takes for any UPDATE, so no
        other transaction can slip between the predicate and the write. If the
        row no longer satisfies the condition, zero rows are affected and we
        report the shortfall.

        Faster than strategy A — one round trip instead of two, and the lock is
        held for microseconds instead of for the duration of application logic.
        The trade-off is expressiveness: the entire decision must fit in the
        WHERE clause. This is the default because reserving stock *is* exactly
        that shape.
        """
        result = await self._session.execute(
            update(Inventory)
            .where(
                Inventory.product_id == product_id,
                Inventory.quantity_available >= quantity,
            )
            .values(
                quantity_available=Inventory.quantity_available - quantity,
                quantity_reserved=Inventory.quantity_reserved + quantity,
                version=Inventory.version + 1,
            )
            .returning(Inventory),
            execution_options={"populate_existing": True},
        )
        return result.scalar_one_or_none()

    async def reserve_unsafe(self, product_id: uuid.UUID, quantity: int) -> Inventory | None:
        """Read, decide in Python, then write. **Racy on purpose.**

        This is what the naive implementation looks like, and the concurrency
        test asserts that it actually oversells — proving the race is real and
        that the safe versions are not cargo cult.

        Never call this outside tests.
        """
        inventory = await self.get(product_id)
        if inventory is None or inventory.quantity_available < quantity:
            return None

        new_available = inventory.quantity_available - quantity
        new_reserved = inventory.quantity_reserved + quantity
        await self._session.execute(
            update(Inventory)
            .where(Inventory.product_id == product_id)
            .values(quantity_available=new_available, quantity_reserved=new_reserved)
        )
        await self._session.flush()
        return inventory

    async def release(self, product_id: uuid.UUID, quantity: int) -> bool:
        """Return held stock to the sellable pool.

        Also a conditional update: ``quantity_reserved >= quantity`` prevents a
        double release (a cancel racing an expiry sweeper) from inventing stock
        out of nothing.
        """
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(Inventory)
                .where(
                    Inventory.product_id == product_id,
                    Inventory.quantity_reserved >= quantity,
                )
                .values(
                    quantity_available=Inventory.quantity_available + quantity,
                    quantity_reserved=Inventory.quantity_reserved - quantity,
                    version=Inventory.version + 1,
                )
            ),
        )
        return result.rowcount == 1

    async def confirm(self, product_id: uuid.UUID, quantity: int) -> bool:
        """Consume held stock: the goods have left the warehouse.

        Only ``quantity_reserved`` drops. ``quantity_available`` is untouched,
        because those units were already moved out of it at reservation time —
        decrementing both here would double-count the sale.
        """
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(Inventory)
                .where(
                    Inventory.product_id == product_id,
                    Inventory.quantity_reserved >= quantity,
                )
                .values(
                    quantity_reserved=Inventory.quantity_reserved - quantity,
                    version=Inventory.version + 1,
                )
            ),
        )
        return result.rowcount == 1

    async def adjust_available(self, product_id: uuid.UUID, delta: int) -> Inventory | None:
        """Admin restock or shrinkage correction.

        The ``>= -delta`` guard on a negative delta is what stops an admin
        typo from driving availability below zero — and if it somehow got
        through, the CHECK constraint would abort the transaction anyway.
        """
        stmt = (
            update(Inventory)
            .where(Inventory.product_id == product_id)
            .values(
                quantity_available=Inventory.quantity_available + delta,
                version=Inventory.version + 1,
            )
            .returning(Inventory)
        )
        if delta < 0:
            stmt = stmt.where(Inventory.quantity_available >= -delta)

        result = await self._session.execute(stmt, execution_options={"populate_existing": True})
        return result.scalar_one_or_none()

    def add(self, inventory: Inventory) -> Inventory:
        self._session.add(inventory)
        return inventory

    def add_reservation(self, reservation: InventoryReservation) -> InventoryReservation:
        self._session.add(reservation)
        return reservation

    async def get_reservation(
        self, reference: str, product_id: uuid.UUID
    ) -> InventoryReservation | None:
        result = await self._session.execute(
            select(InventoryReservation).where(
                InventoryReservation.reference == reference,
                InventoryReservation.product_id == product_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_reservation_for_update(
        self, reference: str, product_id: uuid.UUID
    ) -> InventoryReservation | None:
        """Lock a reservation row so confirm and release cannot both win."""
        result = await self._session.execute(
            select(InventoryReservation)
            .where(
                InventoryReservation.reference == reference,
                InventoryReservation.product_id == product_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def resolve_reservation(
        self, reservation: InventoryReservation, status: ReservationStatus
    ) -> None:
        reservation.status = status
        reservation.resolved_at = datetime.now(UTC)
        await self._session.flush()

    async def list_expired_holds(self, *, limit: int = 100) -> list[InventoryReservation]:
        """Holds past their TTL, for the release sweeper."""
        result = await self._session.execute(
            select(InventoryReservation)
            .where(
                InventoryReservation.status == ReservationStatus.HELD,
                InventoryReservation.expires_at <= datetime.now(UTC),
            )
            .order_by(InventoryReservation.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())
