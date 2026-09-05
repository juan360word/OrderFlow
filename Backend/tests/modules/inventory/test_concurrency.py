"""Concurrency tests (phase 6) — the ones that matter most in this project.

Each test opens **several real database sessions** and runs them with
``asyncio.gather``, so the operations genuinely interleave inside PostgreSQL.
That is the only way to prove the locking works: a single-session test would
serialise the calls and pass no matter how broken the code is.

The suite is deliberately structured as an argument:

  1. ``test_naive_implementation_oversells``  — the race is real.
  2. ``test_atomic_update_prevents_overselling``  — strategy B fixes it.
  3. ``test_pessimistic_lock_prevents_overselling``  — strategy A fixes it too.
  4. ``test_check_constraint_is_the_last_line_of_defence`` — even raw SQL cannot
     break the invariant.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from orderflow.core.config import Settings
from orderflow.core.database import Database
from orderflow.modules.inventory.repository import InventoryRepository
from orderflow.modules.inventory.service import InsufficientStockError, InventoryService

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]


async def _read_counters(database: Database, product_id: uuid.UUID) -> tuple[int, int]:
    """Read the committed counters on a fresh connection."""
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT quantity_available, quantity_reserved "
                    "FROM inventory WHERE product_id = :pid"
                ),
                {"pid": product_id},
            )
        ).one()
    return int(row[0]), int(row[1])


async def test_naive_implementation_oversells(
    database: Database, make_product: Callable[..., object]
) -> None:
    """Demonstrate the race the whole phase exists to prevent.

    Read-check-write in application code, with one unit in stock and two
    concurrent buyers. Both read ``available = 1``, both conclude they may
    proceed, both write. The unit is sold twice.

    This test asserts the bug on purpose. If it ever starts failing, the naive
    path was accidentally made safe — and the safe implementations lost their
    justification.
    """
    product = await make_product(sku="RACE-001", stock=1)
    barrier = asyncio.Barrier(2)

    async def buy() -> bool:
        async with database.session() as session:
            repo = InventoryRepository(session)
            inventory = await repo.get(product.id)
            assert inventory is not None
            can_buy = inventory.quantity_available >= 1
            await barrier.wait()
            if not can_buy:
                return False
            await session.execute(
                text("UPDATE inventory SET quantity_available = :value WHERE product_id = :pid"),
                {"value": inventory.quantity_available - 1, "pid": product.id},
            )
            return True

    results = await asyncio.gather(buy(), buy())
    available, _ = await _read_counters(database, product.id)

    assert results == [True, True], "both buyers believed they succeeded"
    assert available == 0
    assert sum(results) == 2, "this is the oversell the safe strategies prevent"


@pytest.mark.parametrize("strategy", ["atomic_update", "pessimistic"])
async def test_only_one_buyer_wins_the_last_unit(
    database: Database,
    settings: Settings,
    make_product: Callable[..., object],
    strategy: str,
) -> None:
    """With stock = 1 and two concurrent reservations, exactly one succeeds.

    Run against both strategies: they must be behaviourally identical, which is
    what makes the choice between them a pure performance decision.
    """
    product = await make_product(sku=f"LAST-{strategy[:4].upper()}", stock=1)
    tuned = settings.model_copy(update={"inventory_locking_strategy": strategy})

    async def reserve(reference: str) -> bool:
        async with database.session() as session:
            service = InventoryService(session, tuned)
            try:
                await service.reserve(product.id, quantity=1, reference=reference)
            except InsufficientStockError:
                return False
            return True

    results = await asyncio.gather(reserve("order-a"), reserve("order-b"))
    available, reserved = await _read_counters(database, product.id)

    assert sum(results) == 1, f"{strategy}: exactly one reservation must win"
    assert available == 0
    assert reserved == 1


@pytest.mark.parametrize("strategy", ["atomic_update", "pessimistic"])
async def test_stock_never_goes_negative_under_heavy_contention(
    database: Database,
    settings: Settings,
    make_product: Callable[..., object],
    strategy: str,
) -> None:
    """20 concurrent buyers against 5 units: exactly 5 win, availability is 0.

    This is the load-shaped version of the previous test — enough parallelism
    that any missing lock shows up as a negative counter or an extra winner.
    """
    stock = 5
    buyers = 20
    product = await make_product(sku=f"HEAVY-{strategy[:4].upper()}", stock=stock)
    tuned = settings.model_copy(update={"inventory_locking_strategy": strategy})

    async def reserve(index: int) -> bool:
        async with database.session() as session:
            service = InventoryService(session, tuned)
            try:
                await service.reserve(product.id, quantity=1, reference=f"order-{index}")
            except InsufficientStockError:
                return False
            return True

    results = await asyncio.gather(*(reserve(index) for index in range(buyers)))
    available, reserved = await _read_counters(database, product.id)

    assert sum(results) == stock
    assert available == 0, "availability must never go negative"
    assert reserved == stock


async def test_concurrent_reserves_of_different_sizes_conserve_stock(
    database: Database, settings: Settings, make_product: Callable[..., object]
) -> None:
    """Total units never change: available + reserved is invariant."""
    product = await make_product(sku="MIXED-001", stock=10)

    async def reserve(index: int, quantity: int) -> bool:
        async with database.session() as session:
            service = InventoryService(session, settings)
            try:
                await service.reserve(product.id, quantity=quantity, reference=f"mix-{index}")
            except InsufficientStockError:
                return False
            return True

    quantities = [1, 2, 3, 4, 5, 6, 7]
    results = await asyncio.gather(
        *(reserve(index, quantity) for index, quantity in enumerate(quantities))
    )
    available, reserved = await _read_counters(database, product.id)

    granted = sum(quantity for quantity, ok in zip(quantities, results, strict=True) if ok)
    assert available + reserved == 10, "units cannot be created or destroyed"
    assert reserved == granted
    assert available >= 0


async def test_idempotent_reserve_under_concurrency(
    database: Database, settings: Settings, make_product: Callable[..., object]
) -> None:
    """The same reference sent twice at once must reserve stock only once.

    This is the network-retry scenario: the client times out and resends
    before the first request has committed. The UNIQUE (reference, product_id)
    constraint is what makes the second attempt fail instead of double-booking.
    """
    product = await make_product(sku="IDEM-001", stock=10)

    async def reserve() -> str:
        async with database.session() as session:
            service = InventoryService(session, settings)
            try:
                reservation = await service.reserve(product.id, quantity=3, reference="same-order")
            except Exception as exc:
                return type(exc).__name__
            return str(reservation.id)

    results = await asyncio.gather(reserve(), reserve())
    available, reserved = await _read_counters(database, product.id)

    assert reserved == 3, "stock must be held once, not twice"
    assert available == 7
    assert len({r for r in results if "-" in r}) <= 1


async def test_confirm_and_release_cannot_both_win(
    database: Database, settings: Settings, make_product: Callable[..., object]
) -> None:
    """A cancellation racing a confirmation must not do both.

    Without the row lock in ``_load_held_reservation``, the stock would be
    returned to the pool *and* shipped.
    """
    product = await make_product(sku="RACE-CR-001", stock=5)
    async with database.session() as session:
        await InventoryService(session, settings).reserve(
            product.id, quantity=2, reference="order-cr"
        )

    async def confirm() -> str:
        async with database.session() as session:
            try:
                await InventoryService(session, settings).confirm("order-cr", product.id)
            except Exception as exc:
                return f"failed:{type(exc).__name__}"
            return "confirmed"

    async def release() -> str:
        async with database.session() as session:
            try:
                await InventoryService(session, settings).release("order-cr", product.id)
            except Exception as exc:
                return f"failed:{type(exc).__name__}"
            return "released"

    results = await asyncio.gather(confirm(), release())
    available, reserved = await _read_counters(database, product.id)

    succeeded = [result for result in results if not result.startswith("failed")]
    assert len(succeeded) == 1, f"exactly one must win, got {results}"
    assert reserved == 0
    assert available in (3, 5)
    assert available == (3 if succeeded == ["confirmed"] else 5)


async def test_check_constraint_is_the_last_line_of_defence(
    database: Database, make_product: Callable[..., object]
) -> None:
    """Even a raw UPDATE that bypasses every service cannot break the invariant.

    This is why the constraint lives in the database: application code is one
    refactor away from being wrong, a CHECK constraint is not.
    """
    product = await make_product(sku="CHECK-001", stock=1)

    with pytest.raises(IntegrityError) as exc_info:
        async with database.session() as session:
            await session.execute(
                text("UPDATE inventory SET quantity_available = -1 WHERE product_id = :pid"),
                {"pid": product.id},
            )

    assert "ck_inventory_available_non_negative" in str(exc_info.value)

    available, _ = await _read_counters(database, product.id)
    assert available == 1, "the transaction rolled back entirely"
