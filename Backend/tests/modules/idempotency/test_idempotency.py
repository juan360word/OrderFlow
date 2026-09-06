"""Idempotency tests (phase 8)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Settings
from orderflow.core.database import Database
from orderflow.core.errors import AppError
from orderflow.modules.auth.models import User
from orderflow.modules.idempotency.service import IdempotencyService
from orderflow.modules.inventory.service import InventoryService
from orderflow.modules.orders.schemas import OrderCreate, OrderItemRequest
from orderflow.modules.orders.service import OrderService
from orderflow.modules.products.service import ProductService
from tests.conftest import TEST_PASSWORD

pytestmark = [pytest.mark.integration, pytest.mark.idempotency]

ORDERS = "/api/v1/orders"
INVENTORY = "/api/v1/inventory"


async def _order_count(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT count(*) FROM orders"))).scalar_one())


async def test_a_retry_returns_the_original_order(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_product: Callable[..., object],
    session: AsyncSession,
) -> None:
    product = await make_product(sku="IDEM-ORD-001", stock=10)
    body = {"items": [{"product_id": str(product.id), "quantity": 2}]}
    headers = {**auth_headers, "Idempotency-Key": "order-key-0001"}

    first = await client.post(ORDERS, headers=headers, json=body)
    second = await client.post(ORDERS, headers=headers, json=body)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["Idempotent-Replay"] == "true"
    assert "Idempotent-Replay" not in first.headers

    assert await _order_count(session) == 1


async def test_a_retry_does_not_reserve_stock_twice(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="IDEM-ORD-002", stock=10)
    body = {"items": [{"product_id": str(product.id), "quantity": 3}]}
    headers = {**auth_headers, "Idempotency-Key": "order-key-0002"}

    for _ in range(4):
        assert (await client.post(ORDERS, headers=headers, json=body)).status_code == 201

    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 7
    assert stock["quantity_reserved"] == 3


async def test_without_a_key_a_retry_creates_a_second_order(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_product: Callable[..., object],
    session: AsyncSession,
) -> None:
    """The problem the header exists to solve, stated as a test."""
    product = await make_product(sku="IDEM-ORD-003", stock=10)
    body = {"items": [{"product_id": str(product.id), "quantity": 1}]}

    await client.post(ORDERS, headers=auth_headers, json=body)
    await client.post(ORDERS, headers=auth_headers, json=body)

    assert await _order_count(session) == 2


async def test_the_same_key_with_a_different_payload_is_rejected(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    """Otherwise a changed basket would silently receive the old response."""
    product = await make_product(sku="IDEM-ORD-004", stock=10)
    headers = {**auth_headers, "Idempotency-Key": "order-key-0004"}

    await client.post(
        ORDERS, headers=headers, json={"items": [{"product_id": str(product.id), "quantity": 1}]}
    )
    response = await client.post(
        ORDERS, headers=headers, json={"items": [{"product_id": str(product.id), "quantity": 9}]}
    )

    assert response.status_code == 422
    assert response.json()["title"] == "idempotency_key_reuse"


async def test_keys_are_scoped_per_user(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: Callable[..., object],
    make_product: Callable[..., object],
    session: AsyncSession,
) -> None:
    """One customer's key must never collide with, or expose, another's order."""
    product = await make_product(sku="IDEM-ORD-005", stock=10)
    body = {"items": [{"product_id": str(product.id), "quantity": 1}]}
    shared_key = "shared-key-0005"

    first = await client.post(
        ORDERS, headers={**auth_headers, "Idempotency-Key": shared_key}, json=body
    )

    await make_user(email="second@example.com")
    tokens = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "second@example.com", "password": TEST_PASSWORD},
        )
    ).json()
    second = await client.post(
        ORDERS,
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Idempotency-Key": shared_key,
        },
        json=body,
    )

    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    assert await _order_count(session) == 2


async def test_a_failed_request_does_not_burn_the_key(
    client: AsyncClient,
    admin_headers: dict[str, str],
    auth_headers: dict[str, str],
    make_product: Callable[..., object],
) -> None:
    """A rolled-back attempt must leave the key free, or a transient error becomes permanent."""
    product = await make_product(sku="IDEM-ORD-006", stock=0)
    body = {"items": [{"product_id": str(product.id), "quantity": 1}]}
    headers = {**auth_headers, "Idempotency-Key": "order-key-0006"}

    failed = await client.post(ORDERS, headers=headers, json=body)
    assert failed.status_code == 409

    await client.post(
        f"{INVENTORY}/{product.id}/adjust",
        headers=admin_headers,
        json={"delta": 5, "reason": "restock"},
    )

    retried = await client.post(ORDERS, headers=headers, json=body)
    assert retried.status_code == 201


@pytest.mark.parametrize("key", ["short", "with spaces here", "bad/char$"])
async def test_malformed_keys_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object], key: str
) -> None:
    product = await make_product(sku="IDEM-ORD-007", stock=10)

    response = await client.post(
        ORDERS,
        headers={**auth_headers, "Idempotency-Key": key},
        json={"items": [{"product_id": str(product.id), "quantity": 1}]},
    )

    assert response.status_code == 422


async def test_an_oversized_key_is_rejected(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="IDEM-ORD-008", stock=10)

    response = await client.post(
        ORDERS,
        headers={**auth_headers, "Idempotency-Key": "a" * 300},
        json={"items": [{"product_id": str(product.id), "quantity": 1}]},
    )

    assert response.status_code == 422


async def test_expired_keys_are_purged(
    session: AsyncSession, settings: Settings, make_user: Callable[..., object]
) -> None:
    user: User = await make_user(email="purge@example.com")
    service = IdempotencyService(session, settings)

    await session.execute(
        text(
            "INSERT INTO idempotency_keys "
            "(id, user_id, key, method, path, request_fingerprint, status, expires_at) "
            "VALUES (:id, :uid, :key, 'POST', '/orders', 'abc', 'processing', "
            "now() - interval '1 day')"
        ),
        {"id": uuid.uuid4(), "uid": user.id, "key": "expired-key-0001"},
    )
    await session.commit()

    removed = await service.purge_expired()
    await session.commit()

    assert removed == 1


class TestConcurrentRetries:
    """Two identical requests arriving at the same time."""

    async def test_only_one_order_is_created(
        self,
        database: Database,
        settings: Settings,
        make_user: Callable[..., object],
        make_product: Callable[..., object],
    ) -> None:
        """The unique index serialises the two claims; the loser never runs the work.

        This is the network-retry scenario at its worst: the client gave up and
        resent while the first request was still in flight.
        """
        buyer: User = await make_user(email="concurrent@example.com")
        product = await make_product(sku="IDEM-RACE-001", stock=10)
        payload = OrderCreate(items=[OrderItemRequest(product_id=product.id, quantity=2)])

        async def place() -> str:
            try:
                async with database.session() as session:
                    inventory = InventoryService(session, settings)
                    products = ProductService(session, inventory)
                    orders = OrderService(session, products, inventory)
                    idempotency = IdempotencyService(session, settings)

                    customer = await session.get(User, buyer.id)
                    assert customer is not None

                    slot = await idempotency.claim(
                        key="race-key-0001",
                        user_id=customer.id,
                        method="POST",
                        path="/orders",
                        payload=payload.model_dump(mode="json"),
                    )
                    if slot.replayed and slot.body is not None:
                        return f"replayed:{slot.body['id']}"

                    order = await orders.create(payload, customer=customer)
                    await idempotency.record(
                        slot,
                        status_code=201,
                        body={"id": str(order.id)},
                        resource_id=order.id,
                    )
                    return f"created:{order.id}"
            except AppError as exc:
                return f"failed:{exc.code}"

        results = await asyncio.gather(place(), place())

        async with database.session() as session:
            orders_created = await _order_count(session)
            stock = (
                await session.execute(
                    text(
                        "SELECT quantity_available, quantity_reserved "
                        "FROM inventory WHERE product_id = :pid"
                    ),
                    {"pid": product.id},
                )
            ).one()

        created = [result for result in results if result.startswith("created")]
        assert len(created) == 1, results
        assert orders_created == 1, "the retry must not have placed a second order"
        assert tuple(stock) == (8, 2), "stock must be reserved exactly once"
