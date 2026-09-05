"""Inventory API and lifecycle tests (phase 6)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient

INVENTORY = "/api/v1/inventory"
RESERVATIONS = f"{INVENTORY}/reservations"


async def test_stock_is_publicly_readable(
    client: AsyncClient, make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="STK-001", stock=8)

    response = await client.get(f"{INVENTORY}/{product.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["quantity_available"] == 8
    assert body["quantity_reserved"] == 0
    assert body["quantity_total"] == 8


async def test_reserving_requires_authentication(
    client: AsyncClient, make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="AUTH-001")

    response = await client.post(
        RESERVATIONS,
        json={"product_id": str(product.id), "quantity": 1, "reference": "order-1"},
    )

    assert response.status_code == 401


async def test_reserve_moves_stock_from_available_to_reserved(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="RES-001", stock=10)

    response = await client.post(
        RESERVATIONS,
        headers=auth_headers,
        json={"product_id": str(product.id), "quantity": 3, "reference": "order-1"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "held"

    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 7
    assert stock["quantity_reserved"] == 3
    assert stock["quantity_total"] == 10  # nothing created or destroyed


async def test_reserving_more_than_available_is_a_conflict(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    """409, not 422: the request is valid, the stock simply is not there."""
    product = await make_product(sku="OVER-001", stock=2)

    response = await client.post(
        RESERVATIONS,
        headers=auth_headers,
        json={"product_id": str(product.id), "quantity": 5, "reference": "order-1"},
    )

    assert response.status_code == 409
    assert response.json()["title"] == "insufficient_stock"


async def test_reserve_is_idempotent(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    """A retried request returns the original hold and reserves nothing extra."""
    product = await make_product(sku="IDEM-API-001", stock=10)
    payload = {"product_id": str(product.id), "quantity": 4, "reference": "order-retry"}

    first = await client.post(RESERVATIONS, headers=auth_headers, json=payload)
    second = await client.post(RESERVATIONS, headers=auth_headers, json=payload)

    assert first.json()["id"] == second.json()["id"]
    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_reserved"] == 4


async def test_confirm_consumes_the_hold(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    """After confirming, the units are gone: reserved drops, available does not."""
    product = await make_product(sku="CONF-001", stock=10)
    await client.post(
        RESERVATIONS,
        headers=auth_headers,
        json={"product_id": str(product.id), "quantity": 3, "reference": "order-c"},
    )

    response = await client.post(
        f"{RESERVATIONS}/order-c/confirm",
        headers=auth_headers,
        params={"product_id": str(product.id)},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"

    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 7
    assert stock["quantity_reserved"] == 0
    assert stock["quantity_total"] == 7


async def test_release_returns_the_stock(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="REL-001", stock=10)
    await client.post(
        RESERVATIONS,
        headers=auth_headers,
        json={"product_id": str(product.id), "quantity": 3, "reference": "order-r"},
    )

    response = await client.post(
        f"{RESERVATIONS}/order-r/release",
        headers=auth_headers,
        params={"product_id": str(product.id)},
    )

    assert response.status_code == 200
    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 10
    assert stock["quantity_reserved"] == 0


async def test_a_reservation_cannot_be_resolved_twice(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="TWICE-001", stock=10)
    await client.post(
        RESERVATIONS,
        headers=auth_headers,
        json={"product_id": str(product.id), "quantity": 2, "reference": "order-t"},
    )
    params = {"product_id": str(product.id)}
    await client.post(f"{RESERVATIONS}/order-t/confirm", headers=auth_headers, params=params)

    response = await client.post(
        f"{RESERVATIONS}/order-t/release", headers=auth_headers, params=params
    )

    assert response.status_code == 409


@pytest.mark.parametrize("quantity", [0, -1, 10_000_000])
async def test_invalid_quantities_are_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_product: Callable[..., object],
    quantity: int,
) -> None:
    product = await make_product(sku="QTY-001", stock=10)

    response = await client.post(
        RESERVATIONS,
        headers=auth_headers,
        json={"product_id": str(product.id), "quantity": quantity, "reference": "order-q"},
    )

    assert response.status_code == 422


async def test_adjust_requires_admin(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ADJ-001", stock=5)

    response = await client.post(
        f"{INVENTORY}/{product.id}/adjust",
        headers=auth_headers,
        json={"delta": 100, "reason": "restock"},
    )

    assert response.status_code == 403


async def test_admin_can_restock(
    client: AsyncClient, admin_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ADJ-002", stock=5)

    response = await client.post(
        f"{INVENTORY}/{product.id}/adjust",
        headers=admin_headers,
        json={"delta": 20, "reason": "supplier delivery #4471"},
    )

    assert response.status_code == 200
    assert response.json()["quantity_available"] == 25


async def test_adjustment_cannot_drive_stock_negative(
    client: AsyncClient, admin_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    """The conditional UPDATE refuses before the CHECK constraint has to."""
    product = await make_product(sku="ADJ-003", stock=5)

    response = await client.post(
        f"{INVENTORY}/{product.id}/adjust",
        headers=admin_headers,
        json={"delta": -10, "reason": "damaged goods"},
    )

    assert response.status_code == 409
    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 5


async def test_stock_for_an_unknown_product_is_not_found(client: AsyncClient) -> None:
    response = await client.get(f"{INVENTORY}/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
