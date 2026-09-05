"""Products API tests (phase 5): CRUD, validation and role-based access."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient

PRODUCTS = "/api/v1/products"


async def test_listing_is_public(client: AsyncClient, make_product: Callable[..., object]) -> None:
    await make_product(sku="PUB-001")

    response = await client.get(PRODUCTS)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["sku"] == "PUB-001"


async def test_listing_hides_inactive_products(
    client: AsyncClient, make_product: Callable[..., object]
) -> None:
    await make_product(sku="LIVE-001")
    await make_product(sku="GONE-001", is_active=False)

    body = (await client.get(PRODUCTS)).json()

    assert [item["sku"] for item in body["items"]] == ["LIVE-001"]


async def test_listing_is_paginated(
    client: AsyncClient, make_product: Callable[..., object]
) -> None:
    for index in range(5):
        await make_product(sku=f"PAGE-{index:03d}")

    body = (await client.get(PRODUCTS, params={"limit": 2, "offset": 0})).json()

    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_page_size_is_capped(client: AsyncClient) -> None:
    """An unbounded ?limit is a one-request denial of service."""
    response = await client.get(PRODUCTS, params={"limit": 100_000})

    assert response.status_code == 422


async def test_search_filter_is_injection_safe(
    client: AsyncClient, make_product: Callable[..., object]
) -> None:
    """A SQL payload in ?search must be treated as literal text."""
    await make_product(sku="SAFE-001")

    response = await client.get(PRODUCTS, params={"search": "'; DROP TABLE products; --"})

    assert response.status_code == 200
    assert response.json()["total"] == 0
    # The table is still there.
    assert (await client.get(PRODUCTS)).json()["total"] == 1


async def test_like_wildcards_in_search_are_escaped(
    client: AsyncClient, make_product: Callable[..., object]
) -> None:
    await make_product(sku="WILD-001")

    body = (await client.get(PRODUCTS, params={"search": "%"})).json()

    assert body["total"] == 0


async def test_create_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(PRODUCTS, json={"sku": "NEW-001", "name": "New", "price": "9.99"})

    assert response.status_code == 401


async def test_create_requires_the_admin_role(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """A logged-in customer is authenticated but not authorized."""
    response = await client.post(
        PRODUCTS,
        headers=auth_headers,
        json={"sku": "NEW-001", "name": "New", "price": "9.99"},
    )

    assert response.status_code == 403


async def test_admin_creates_a_product_with_stock(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        PRODUCTS,
        headers=admin_headers,
        json={"sku": "ADM-001", "name": "Admin Product", "price": "49.90", "initial_stock": 7},
    )

    assert response.status_code == 201
    product_id = response.json()["id"]

    # The inventory row was created in the same transaction.
    stock = await client.get(f"/api/v1/inventory/{product_id}")
    assert stock.status_code == 200
    assert stock.json()["quantity_available"] == 7


async def test_duplicate_sku_is_a_conflict(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    payload = {"sku": "DUP-001", "name": "Dup", "price": "1.00"}
    await client.post(PRODUCTS, headers=admin_headers, json=payload)

    response = await client.post(PRODUCTS, headers=admin_headers, json=payload)

    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"sku": "AB", "name": "Too short SKU", "price": "1.00"},
        {"sku": "NEG-001", "name": "Negative", "price": "-1.00"},
        {"sku": "CUR-001", "name": "Bad currency", "price": "1.00", "currency": "dollars"},
        {"sku": "PRE-001", "name": "Too many decimals", "price": "1.23456"},
        {"sku": "EMP-001", "name": "", "price": "1.00"},
    ],
)
async def test_invalid_payloads_are_rejected(
    client: AsyncClient, admin_headers: dict[str, str], payload: dict[str, object]
) -> None:
    response = await client.post(PRODUCTS, headers=admin_headers, json=payload)

    assert response.status_code == 422


async def test_price_precision_survives_the_round_trip(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """NUMERIC, not FLOAT: 19.99 must come back as exactly 19.99."""
    response = await client.post(
        PRODUCTS,
        headers=admin_headers,
        json={"sku": "MON-001", "name": "Money", "price": "19.99"},
    )

    assert response.json()["price"] == "19.99"


async def test_sku_is_normalised_to_uppercase(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        PRODUCTS,
        headers=admin_headers,
        json={"sku": "low-001", "name": "Lowercase SKU", "price": "1.00"},
    )

    assert response.status_code == 201
    assert response.json()["sku"] == "LOW-001"


async def test_patch_updates_only_the_fields_sent(
    client: AsyncClient, admin_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="UPD-001", price="10.00")

    response = await client.patch(
        f"{PRODUCTS}/{product.id}", headers=admin_headers, json={"price": "12.50"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["price"] == "12.50"
    assert body["name"] == "Product UPD-001"  # untouched


async def test_sku_cannot_be_changed(
    client: AsyncClient, admin_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    """SKU is immutable: external systems reference it."""
    product = await make_product(sku="IMM-001")

    response = await client.patch(
        f"{PRODUCTS}/{product.id}", headers=admin_headers, json={"sku": "OTHER-001"}
    )

    assert response.status_code == 422


async def test_delete_is_a_soft_delete(
    client: AsyncClient, admin_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    """The row must survive so order history keeps its foreign keys."""
    product = await make_product(sku="SOFT-001")

    assert (
        await client.delete(f"{PRODUCTS}/{product.id}", headers=admin_headers)
    ).status_code == 204
    assert (await client.get(f"{PRODUCTS}/{product.id}")).status_code == 404
    # Inventory still resolves, which proves the row was not physically removed.
    assert (await client.get(f"/api/v1/inventory/{product.id}")).status_code == 200


async def test_unknown_product_is_not_found(client: AsyncClient) -> None:
    response = await client.get(f"{PRODUCTS}/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


async def test_malformed_uuid_is_a_validation_error(client: AsyncClient) -> None:
    response = await client.get(f"{PRODUCTS}/not-a-uuid")

    assert response.status_code == 422
