"""The delivery address attached to an order.

What matters about this data is not that it round-trips, but that the order
cannot exist without it, that it stays readable to exactly the two people who
need it, and that it never changes after the fact.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import SHIPPING_ADDRESS

pytestmark = pytest.mark.api

ORDERS = "/api/v1/orders"


def _payload(product_id: object, **address_overrides: object) -> dict[str, object]:
    return {
        "items": [{"product_id": str(product_id), "quantity": 1}],
        "shipping_address": {**SHIPPING_ADDRESS, **address_overrides},
    }


class TestPlacingAnOrderWithAnAddress:
    async def test_the_address_comes_back_on_the_order(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="SHIP-001", stock=5)

        response = await client.post(ORDERS, headers=auth_headers, json=_payload(product.id))

        assert response.status_code == 201
        address = response.json()["shipping_address"]
        assert address["recipient_name"] == "Ana Test"
        assert address["city"] == "Bogotá"
        assert address["country"] == "CO"

    async def test_an_order_without_an_address_is_refused(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """These are physical goods: nobody can deliver an order with no destination."""
        product = await make_product(sku="SHIP-002", stock=5)

        response = await client.post(
            ORDERS,
            headers=auth_headers,
            json={"items": [{"product_id": str(product.id), "quantity": 1}]},
        )

        assert response.status_code == 422
        assert "shipping_address" in response.json()["errors"]

    async def test_the_address_survives_a_reload(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="SHIP-003", stock=5)
        order = (await client.post(ORDERS, headers=auth_headers, json=_payload(product.id))).json()

        fetched = await client.get(f"{ORDERS}/{order['id']}", headers=auth_headers)

        assert fetched.json()["shipping_address"]["line1"] == "Calle 123 #45-67"

    async def test_optional_fields_are_kept_when_given(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="SHIP-004", stock=5)

        response = await client.post(
            ORDERS,
            headers=auth_headers,
            json=_payload(
                product.id,
                line2="Torre B, apto 402",
                postal_code="110111",
                notes="Dejar en portería",
            ),
        )

        address = response.json()["shipping_address"]
        assert address["line2"] == "Torre B, apto 402"
        assert address["postal_code"] == "110111"
        assert address["notes"] == "Dejar en portería"

    async def test_a_blank_optional_field_is_stored_as_absent(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """An empty box in a form means "not provided", not an empty string."""
        product = await make_product(sku="SHIP-005", stock=5)

        response = await client.post(
            ORDERS,
            headers=auth_headers,
            json=_payload(product.id, line2="", postal_code="   ", notes=""),
        )

        address = response.json()["shipping_address"]
        assert address["line2"] is None
        assert address["postal_code"] is None
        assert address["notes"] is None

    async def test_surrounding_whitespace_is_trimmed(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="SHIP-006", stock=5)

        response = await client.post(
            ORDERS, headers=auth_headers, json=_payload(product.id, city="  Medellín  ")
        )

        assert response.json()["shipping_address"]["city"] == "Medellín"

    async def test_the_country_is_normalised_to_upper_case(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """'co' and 'CO' are the same destination; only one may reach a carrier."""
        product = await make_product(sku="SHIP-007", stock=5)

        response = await client.post(
            ORDERS, headers=auth_headers, json=_payload(product.id, country="co")
        )

        assert response.json()["shipping_address"]["country"] == "CO"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("recipient_name", ""),
            ("phone", "llámame"),
            ("phone", "123"),
            ("line1", "ab"),
            ("city", ""),
            ("region", ""),
            ("country", "Colombia"),
        ],
    )
    async def test_an_undeliverable_address_is_refused(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
        field: str,
        value: str,
    ) -> None:
        product = await make_product(sku=f"SHIP-BAD-{field}-{len(value)}", stock=5)

        response = await client.post(
            ORDERS, headers=auth_headers, json=_payload(product.id, **{field: value})
        )

        assert response.status_code == 422

    async def test_nothing_is_stored_when_the_order_fails(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        """The address shares the order's transaction, so it rolls back with it."""
        product = await make_product(sku="SHIP-008", stock=1)

        response = await client.post(
            ORDERS,
            headers=auth_headers,
            json={
                "items": [{"product_id": str(product.id), "quantity": 5}],
                "shipping_address": SHIPPING_ADDRESS,
            },
        )

        assert response.status_code == 409
        orphans = (
            await session.execute(text("SELECT count(*) FROM order_shipping_addresses"))
        ).scalar_one()
        assert orphans == 0


class TestWhoCanSeeTheAddress:
    async def test_the_admin_sees_where_to_send_it(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """The whole point: the person shipping the order must read the address."""
        product = await make_product(sku="SHIP-010", stock=5)
        order = (await client.post(ORDERS, headers=auth_headers, json=_payload(product.id))).json()

        fetched = await client.get(f"{ORDERS}/{order['id']}", headers=admin_headers)

        assert fetched.status_code == 200
        assert fetched.json()["shipping_address"]["phone"] == "+57 300 123 4567"

    async def test_another_customer_cannot_read_it(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
        make_user: Callable[..., object],
    ) -> None:
        """A home address is the last thing that should leak through an IDOR."""
        product = await make_product(sku="SHIP-011", stock=5)
        order = (await client.post(ORDERS, headers=auth_headers, json=_payload(product.id))).json()

        await make_user(email="nosy@example.com", password="Segura2024!XZ")
        tokens = (
            await client.post(
                "/api/v1/auth/login",
                json={"email": "nosy@example.com", "password": "Segura2024!XZ"},
            )
        ).json()
        intruder = {"Authorization": f"Bearer {tokens['access_token']}"}

        assert (await client.get(f"{ORDERS}/{order['id']}", headers=intruder)).status_code == 404

    async def test_the_listing_does_not_carry_addresses(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """Summaries stay summaries: no personal data in a list nobody reads it from."""
        product = await make_product(sku="SHIP-012", stock=5)
        await client.post(ORDERS, headers=auth_headers, json=_payload(product.id))

        listed = (await client.get(ORDERS, headers=auth_headers)).json()

        assert "shipping_address" not in listed["items"][0]


class TestTheAddressIsASnapshot:
    async def test_it_is_not_editable_through_the_order(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """Where an order was sent is history, not a mutable profile field."""
        product = await make_product(sku="SHIP-020", stock=5)
        order = (await client.post(ORDERS, headers=auth_headers, json=_payload(product.id))).json()

        response = await client.patch(
            f"{ORDERS}/{order['id']}",
            headers=auth_headers,
            json={"shipping_address": {**SHIPPING_ADDRESS, "city": "Cali"}},
        )

        assert response.status_code in (404, 405)

    async def test_two_orders_keep_their_own_addresses(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="SHIP-021", stock=10)

        first = (
            await client.post(
                ORDERS, headers=auth_headers, json=_payload(product.id, city="Bogotá")
            )
        ).json()
        second = (
            await client.post(
                ORDERS, headers=auth_headers, json=_payload(product.id, city="Medellín")
            )
        ).json()

        assert first["shipping_address"]["city"] == "Bogotá"
        assert second["shipping_address"]["city"] == "Medellín"

    async def test_it_disappears_with_the_order_it_belongs_to(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        """No orphan addresses: the row is owned by the order, not by the user."""
        product = await make_product(sku="SHIP-022", stock=5)
        order = (await client.post(ORDERS, headers=auth_headers, json=_payload(product.id))).json()

        await session.execute(
            text("DELETE FROM orders WHERE id = :oid"), {"oid": uuid.UUID(order["id"])}
        )
        await session.commit()

        remaining = (
            await session.execute(text("SELECT count(*) FROM order_shipping_addresses"))
        ).scalar_one()
        assert remaining == 0


class TestTheEventCarriesIt:
    async def test_the_order_created_event_includes_the_address(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        """The consumers that need it most - a label printer, a confirmation
        email - are the ones that must work from the event alone."""
        product = await make_product(sku="SHIP-030", stock=5)
        order = (
            await client.post(ORDERS, headers=auth_headers, json=_payload(product.id))
        ).json()

        # The stored column is the wire envelope; the business data sits under
        # its "payload" key.
        message = (
            await session.execute(
                text(
                    "SELECT payload FROM outbox_events "
                    "WHERE aggregate_id = :oid AND event_type = 'order.created'"
                ),
                {"oid": uuid.UUID(order["id"])},
            )
        ).scalar_one()

        address = message["payload"]["shipping_address"]
        assert address["city"] == "Bogotá"
        assert address["line1"] == "Calle 123 #45-67"
