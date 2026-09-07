"""Orders API tests (phase 7): transaction, lifecycle, ownership and pricing."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import SHIPPING_ADDRESS, TEST_PASSWORD

pytestmark = pytest.mark.api

ORDERS = "/api/v1/orders"
INVENTORY = "/api/v1/inventory"
PRODUCTS = "/api/v1/products"


async def _place_order(
    client: AsyncClient, headers: dict[str, str], product_id: str, quantity: int = 1
) -> dict:
    response = await client.post(
        ORDERS,
        headers=headers,
        json={
            "shipping_address": SHIPPING_ADDRESS,
            "items": [{"product_id": product_id, "quantity": quantity}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_placing_an_order_reserves_stock(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-001", stock=10, price="19.99")

    order = await _place_order(client, auth_headers, str(product.id), quantity=3)

    assert order["status"] == "pending"
    assert order["total_amount"] == "59.97"
    assert len(order["items"]) == 1

    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 7
    assert stock["quantity_reserved"] == 3


async def test_line_totals_are_computed_by_the_database(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-SUB-001", stock=10, price="12.50")

    order = await _place_order(client, auth_headers, str(product.id), quantity=4)

    assert order["items"][0]["subtotal"] == "50.00"
    assert order["total_amount"] == "50.00"


async def test_items_snapshot_the_product_at_purchase_time(
    client: AsyncClient,
    admin_headers: dict[str, str],
    auth_headers: dict[str, str],
    make_product: Callable[..., object],
) -> None:
    """A later price change must not rewrite the history of an existing order."""
    product = await make_product(sku="ORD-SNAP-001", stock=10, price="19.99")
    order = await _place_order(client, auth_headers, str(product.id), quantity=2)

    await client.patch(f"{PRODUCTS}/{product.id}", headers=admin_headers, json={"price": "99.99"})

    reread = (await client.get(f"{ORDERS}/{order['id']}", headers=auth_headers)).json()
    assert reread["items"][0]["unit_price"] == "19.99"
    assert reread["total_amount"] == "39.98"


async def test_a_multi_line_order_is_atomic(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_product: Callable[..., object],
) -> None:
    """If the last line has no stock, the earlier reservations roll back too."""
    plenty = await make_product(sku="ORD-ATOM-A", stock=10)
    scarce = await make_product(sku="ORD-ATOM-B", stock=1)

    response = await client.post(
        ORDERS,
        headers=auth_headers,
        json={
            "shipping_address": SHIPPING_ADDRESS,
            "items": [
                {"product_id": str(plenty.id), "quantity": 2},
                {"product_id": str(scarce.id), "quantity": 5},
            ],
        },
    )

    assert response.status_code == 409

    for product, expected in ((plenty, 10), (scarce, 1)):
        stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
        assert stock["quantity_available"] == expected
        assert stock["quantity_reserved"] == 0

    assert (await client.get(ORDERS, headers=auth_headers)).json()["total"] == 0


async def test_ordering_more_than_available_is_a_conflict(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-SHORT-001", stock=2)

    response = await client.post(
        ORDERS,
        headers=auth_headers,
        json={
            "shipping_address": SHIPPING_ADDRESS,
            "items": [{"product_id": str(product.id), "quantity": 5}],
        },
    )

    assert response.status_code == 409
    assert response.json()["title"] == "insufficient_stock"


async def test_ordering_an_unknown_product_is_not_found(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        ORDERS,
        headers=auth_headers,
        json={
            "shipping_address": SHIPPING_ADDRESS,
            "items": [{"product_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}],
        },
    )

    assert response.status_code == 404


async def test_ordering_an_inactive_product_is_not_found(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-DEAD-001", stock=10, is_active=False)

    response = await client.post(
        ORDERS,
        headers=auth_headers,
        json={
            "shipping_address": SHIPPING_ADDRESS,
            "items": [{"product_id": str(product.id), "quantity": 1}],
        },
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"product_id": "00000000-0000-0000-0000-000000000000", "quantity": 0}],
        [{"product_id": "00000000-0000-0000-0000-000000000000", "quantity": -1}],
    ],
)
async def test_invalid_baskets_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str], items: list[dict]
) -> None:
    response = await client.post(
        ORDERS, headers=auth_headers, json={"shipping_address": SHIPPING_ADDRESS, "items": items}
    )

    assert response.status_code == 422


async def test_the_same_product_cannot_appear_twice(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-DUP-001", stock=10)

    response = await client.post(
        ORDERS,
        headers=auth_headers,
        json={
            "items": [
                {"product_id": str(product.id), "quantity": 1},
                {"product_id": str(product.id), "quantity": 2},
            ]
        },
    )

    assert response.status_code == 422


async def test_placing_an_order_requires_authentication(
    client: AsyncClient, make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-ANON-001")

    response = await client.post(
        ORDERS,
        json={
            "shipping_address": SHIPPING_ADDRESS,
            "items": [{"product_id": str(product.id), "quantity": 1}],
        },
    )

    assert response.status_code == 401


async def test_cancelling_a_pending_order_releases_the_stock(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-CANCEL-001", stock=10)
    order = await _place_order(client, auth_headers, str(product.id), quantity=4)

    response = await client.post(
        f"{ORDERS}/{order['id']}/cancel", headers=auth_headers, json={"reason": "changed my mind"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancellation_reason"] == "changed my mind"

    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 10
    assert stock["quantity_reserved"] == 0


async def test_an_order_cannot_be_cancelled_twice(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-CANCEL-002", stock=10)
    order = await _place_order(client, auth_headers, str(product.id))
    await client.post(f"{ORDERS}/{order['id']}/cancel", headers=auth_headers, json={})

    response = await client.post(f"{ORDERS}/{order['id']}/cancel", headers=auth_headers, json={})

    assert response.status_code == 409
    assert response.json()["title"] == "invalid_order_transition"


async def test_confirming_consumes_the_reserved_stock(
    client: AsyncClient,
    auth_headers: dict[str, str],
    admin_headers: dict[str, str],
    make_product: Callable[..., object],
) -> None:
    product = await make_product(sku="ORD-CONF-001", stock=10)
    order = await _place_order(client, auth_headers, str(product.id), quantity=3)

    response = await client.post(f"{ORDERS}/{order['id']}/confirm", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert response.json()["confirmed_at"] is not None

    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 7
    assert stock["quantity_reserved"] == 0
    assert stock["quantity_total"] == 7


async def test_a_customer_cannot_confirm_an_order(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    """Confirmation is the fulfilment step, not something the buyer triggers."""
    product = await make_product(sku="ORD-CONF-002", stock=10)
    order = await _place_order(client, auth_headers, str(product.id))

    response = await client.post(f"{ORDERS}/{order['id']}/confirm", headers=auth_headers)

    assert response.status_code == 403


async def test_a_customer_cannot_cancel_a_confirmed_order(
    client: AsyncClient,
    auth_headers: dict[str, str],
    admin_headers: dict[str, str],
    make_product: Callable[..., object],
) -> None:
    product = await make_product(sku="ORD-CONF-003", stock=10)
    order = await _place_order(client, auth_headers, str(product.id))
    await client.post(f"{ORDERS}/{order['id']}/confirm", headers=admin_headers)

    response = await client.post(f"{ORDERS}/{order['id']}/cancel", headers=auth_headers, json={})

    assert response.status_code == 403


async def test_an_admin_cancelling_a_confirmed_order_restocks(
    client: AsyncClient,
    auth_headers: dict[str, str],
    admin_headers: dict[str, str],
    make_product: Callable[..., object],
) -> None:
    """The hold was already consumed, so the units come back as a restock."""
    product = await make_product(sku="ORD-CONF-004", stock=10)
    order = await _place_order(client, auth_headers, str(product.id), quantity=3)
    await client.post(f"{ORDERS}/{order['id']}/confirm", headers=admin_headers)

    response = await client.post(
        f"{ORDERS}/{order['id']}/cancel", headers=admin_headers, json={"reason": "customer return"}
    )

    assert response.status_code == 200
    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_available"] == 10
    assert stock["quantity_reserved"] == 0


async def test_a_user_cannot_read_another_users_order(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    """IDOR: the response must be 404, not 403 - a 403 confirms the order exists."""
    product = await make_product(sku="ORD-IDOR-001", stock=10)
    order = await _place_order(client, auth_headers, str(product.id))

    await make_user(email="intruder@example.com")
    intruder = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "intruder@example.com", "password": TEST_PASSWORD},
        )
    ).json()
    intruder_headers = {"Authorization": f"Bearer {intruder['access_token']}"}

    response = await client.get(f"{ORDERS}/{order['id']}", headers=intruder_headers)

    assert response.status_code == 404


async def test_a_user_cannot_cancel_another_users_order(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    product = await make_product(sku="ORD-IDOR-002", stock=10)
    order = await _place_order(client, auth_headers, str(product.id))

    await make_user(email="intruder2@example.com")
    intruder = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "intruder2@example.com", "password": TEST_PASSWORD},
        )
    ).json()

    response = await client.post(
        f"{ORDERS}/{order['id']}/cancel",
        headers={"Authorization": f"Bearer {intruder['access_token']}"},
        json={},
    )

    assert response.status_code == 404

    stock = (await client.get(f"{INVENTORY}/{product.id}")).json()
    assert stock["quantity_reserved"] == 1


async def test_the_listing_only_shows_your_own_orders(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user: Callable[..., object],
    make_product: Callable[..., object],
) -> None:
    product = await make_product(sku="ORD-LIST-001", stock=10)
    await _place_order(client, auth_headers, str(product.id))

    await make_user(email="other@example.com")
    other = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "other@example.com", "password": TEST_PASSWORD},
        )
    ).json()

    body = (
        await client.get(ORDERS, headers={"Authorization": f"Bearer {other['access_token']}"})
    ).json()

    assert body["total"] == 0


async def test_an_admin_sees_every_order(
    client: AsyncClient,
    auth_headers: dict[str, str],
    admin_headers: dict[str, str],
    make_product: Callable[..., object],
) -> None:
    product = await make_product(sku="ORD-LIST-002", stock=10)
    await _place_order(client, auth_headers, str(product.id))

    body = (await client.get(ORDERS, headers=admin_headers)).json()

    assert body["total"] == 1


async def test_the_listing_can_filter_by_status(
    client: AsyncClient, auth_headers: dict[str, str], make_product: Callable[..., object]
) -> None:
    product = await make_product(sku="ORD-LIST-003", stock=10)
    keep = await _place_order(client, auth_headers, str(product.id))
    drop = await _place_order(client, auth_headers, str(product.id))
    await client.post(f"{ORDERS}/{drop['id']}/cancel", headers=auth_headers, json={})

    body = (await client.get(ORDERS, headers=auth_headers, params={"status": "pending"})).json()

    assert body["total"] == 1
    assert body["items"][0]["id"] == keep["id"]


async def test_an_order_cannot_mix_currencies(
    client: AsyncClient, auth_headers: dict[str, str], session: AsyncSession
) -> None:
    """Summing amounts in different currencies would produce a meaningless total."""
    from orderflow.modules.inventory.models import Inventory
    from orderflow.modules.products.models import Product

    usd = Product(sku="CUR-USD-001", name="Dollar item", price=Decimal("10.00"), currency="USD")
    eur = Product(sku="CUR-EUR-001", name="Euro item", price=Decimal("10.00"), currency="EUR")
    session.add_all([usd, eur])
    await session.flush()
    session.add_all(
        [
            Inventory(product_id=usd.id, quantity_available=10),
            Inventory(product_id=eur.id, quantity_available=10),
        ]
    )
    await session.commit()

    response = await client.post(
        ORDERS,
        headers=auth_headers,
        json={
            "items": [
                {"product_id": str(usd.id), "quantity": 1},
                {"product_id": str(eur.id), "quantity": 1},
            ]
        },
    )

    assert response.status_code == 422


async def test_a_cancelled_order_keeps_its_line_history(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_product: Callable[..., object],
    session: AsyncSession,
) -> None:
    """Cancelling changes the status; it never deletes the record."""
    product = await make_product(sku="ORD-HIST-001", stock=10)
    order = await _place_order(client, auth_headers, str(product.id), quantity=2)
    await client.post(f"{ORDERS}/{order['id']}/cancel", headers=auth_headers, json={})

    rows = (
        await session.execute(
            text("SELECT count(*) FROM order_items WHERE order_id = :oid"),
            {"oid": order["id"]},
        )
    ).scalar_one()

    assert rows == 1
