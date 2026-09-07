"""Concurrency tests for orders (phase 7).

Two distinct hazards, each with its own test:

  * Two orders competing for the last unit — only one may be placed.
  * Two multi-line orders touching the same products in opposite sequence —
    the classic deadlock, prevented by locking in a deterministic order.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import text

from orderflow.core.config import Settings
from orderflow.core.database import Database
from orderflow.core.errors import AppError
from orderflow.modules.auth.models import User
from orderflow.modules.inventory.service import InventoryService
from orderflow.modules.orders.schemas import OrderCreate, OrderItemRequest
from orderflow.modules.orders.service import OrderService
from orderflow.modules.products.service import ProductService
from tests.conftest import SHIPPING_INPUT

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]


def _build_service(session, settings: Settings) -> OrderService:
    inventory = InventoryService(session, settings)
    products = ProductService(session, inventory)
    return OrderService(session, products, inventory)


async def _counters(database: Database, product_id: uuid.UUID) -> tuple[int, int]:
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


async def _order_count(database: Database) -> int:
    async with database.session() as session:
        return int((await session.execute(text("SELECT count(*) FROM orders"))).scalar_one())


async def test_only_one_order_wins_the_last_unit(
    database: Database,
    settings: Settings,
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    """Two customers, one unit: exactly one order is created."""
    buyer_one: User = await make_user(email="buyer1@example.com")
    buyer_two: User = await make_user(email="buyer2@example.com")
    product = await make_product(sku="ORD-RACE-001", stock=1)

    payload = OrderCreate(
        shipping_address=SHIPPING_INPUT, items=[OrderItemRequest(product_id=product.id, quantity=1)]
    )

    async def place(buyer: User) -> bool:
        try:
            async with database.session() as session:
                service = _build_service(session, settings)
                customer = await session.get(User, buyer.id)
                assert customer is not None
                await service.create(payload, customer=customer)
        except AppError:
            return False
        return True

    results = await asyncio.gather(place(buyer_one), place(buyer_two))
    available, reserved = await _counters(database, product.id)

    assert sum(results) == 1
    assert (available, reserved) == (0, 1)
    assert await _order_count(database) == 1


async def test_opposing_multi_line_orders_do_not_deadlock(
    database: Database,
    settings: Settings,
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    """Two orders listing the same products in opposite order must both succeed.

    Sorting lines by product id before reserving is what makes this safe: both
    transactions take the row locks in the same sequence, so neither can hold
    what the other needs.
    """
    buyer_one: User = await make_user(email="dead1@example.com")
    buyer_two: User = await make_user(email="dead2@example.com")
    first = await make_product(sku="ORD-DEAD-A", stock=50)
    second = await make_product(sku="ORD-DEAD-B", stock=50)

    forward = OrderCreate(
        shipping_address=SHIPPING_INPUT,
        items=[
            OrderItemRequest(product_id=first.id, quantity=1),
            OrderItemRequest(product_id=second.id, quantity=1),
        ],
    )
    backward = OrderCreate(
        shipping_address=SHIPPING_INPUT,
        items=[
            OrderItemRequest(product_id=second.id, quantity=1),
            OrderItemRequest(product_id=first.id, quantity=1),
        ],
    )

    async def place(buyer: User, payload: OrderCreate) -> str:
        try:
            async with database.session() as session:
                service = _build_service(session, settings)
                customer = await session.get(User, buyer.id)
                assert customer is not None
                order = await service.create(payload, customer=customer)
        except Exception as exc:
            return f"failed:{type(exc).__name__}"
        return str(order.id)

    results = await asyncio.wait_for(
        asyncio.gather(place(buyer_one, forward), place(buyer_two, backward)),
        timeout=15,
    )

    assert all(not result.startswith("failed") for result in results), results
    for product in (first, second):
        available, reserved = await _counters(database, product.id)
        assert (available, reserved) == (48, 2)


async def test_many_concurrent_orders_never_oversell(
    database: Database,
    settings: Settings,
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    """15 buyers, 4 units, 2 units each: at most 2 orders can succeed."""
    buyers = [await make_user(email=f"crowd{index}@example.com") for index in range(15)]
    product = await make_product(sku="ORD-CROWD-001", stock=4)
    payload = OrderCreate(
        shipping_address=SHIPPING_INPUT, items=[OrderItemRequest(product_id=product.id, quantity=2)]
    )

    async def place(buyer: User) -> bool:
        try:
            async with database.session() as session:
                service = _build_service(session, settings)
                customer = await session.get(User, buyer.id)
                assert customer is not None
                await service.create(payload, customer=customer)
        except AppError:
            return False
        return True

    results = await asyncio.gather(*(place(buyer) for buyer in buyers))
    available, reserved = await _counters(database, product.id)

    assert sum(results) == 2
    assert available == 0
    assert reserved == 4
    assert await _order_count(database) == 2


async def test_two_concurrent_cancels_release_the_stock_only_once(
    database: Database,
    settings: Settings,
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    """The order row lock is what stops a double release.

    Without ``FOR UPDATE`` both transactions would read 'pending', both would
    pass the state check, and both would return the units - creating stock out
    of nothing.
    """
    buyer: User = await make_user(email="doublecancel@example.com")
    product = await make_product(sku="ORD-RACE-CC", stock=10)

    async with database.session() as session:
        service = _build_service(session, settings)
        customer = await session.get(User, buyer.id)
        assert customer is not None
        order = await service.create(
            OrderCreate(
                shipping_address=SHIPPING_INPUT,
                items=[OrderItemRequest(product_id=product.id, quantity=3)],
            ),
            customer=customer,
        )
        order_id = order.id

    async def cancel() -> bool:
        try:
            async with database.session() as session:
                service = _build_service(session, settings)
                actor = await session.get(User, buyer.id)
                assert actor is not None
                await service.cancel(order_id, actor=actor)
        except AppError:
            return False
        return True

    results = await asyncio.gather(cancel(), cancel())
    available, reserved = await _counters(database, product.id)

    assert sum(results) == 1, "the second cancel must be rejected, not applied"
    assert (available, reserved) == (10, 0), "stock returns once, never twice"


async def test_confirm_after_cancel_is_rejected(
    database: Database,
    settings: Settings,
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    """Cancelled is terminal: a confirmation arriving late must not resurrect it."""
    admin: User = await make_user(email="lateconfirm@example.com", role="admin")
    product = await make_product(sku="ORD-LATE-001", stock=10)

    async with database.session() as session:
        service = _build_service(session, settings)
        actor = await session.get(User, admin.id)
        assert actor is not None
        order = await service.create(
            OrderCreate(
                shipping_address=SHIPPING_INPUT,
                items=[OrderItemRequest(product_id=product.id, quantity=2)],
            ),
            customer=actor,
        )
        order_id = order.id
        await service.cancel(order_id, actor=actor)

    with pytest.raises(AppError):
        async with database.session() as session:
            service = _build_service(session, settings)
            actor = await session.get(User, admin.id)
            assert actor is not None
            await service.confirm(order_id, actor=actor)

    available, reserved = await _counters(database, product.id)
    assert (available, reserved) == (10, 0)


async def test_the_total_is_never_a_float(
    database: Database,
    settings: Settings,
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    """Three lines of 0.10 must total exactly 0.30, which float cannot do."""
    buyer: User = await make_user(email="money@example.com")
    product = await make_product(sku="ORD-MONEY-001", stock=10, price="0.10")

    async with database.session() as session:
        service = _build_service(session, settings)
        customer = await session.get(User, buyer.id)
        assert customer is not None
        order = await service.create(
            OrderCreate(
                shipping_address=SHIPPING_INPUT,
                items=[OrderItemRequest(product_id=product.id, quantity=3)],
            ),
            customer=customer,
        )

        assert order.total_amount == Decimal("0.30")
        assert isinstance(order.total_amount, Decimal)
