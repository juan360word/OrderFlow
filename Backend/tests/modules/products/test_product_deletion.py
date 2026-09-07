"""Withdrawing a product versus erasing one."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import SHIPPING_ADDRESS

pytestmark = pytest.mark.api

PRODUCTS = "/api/v1/products"
ORDERS = "/api/v1/orders"


class TestWithdraw:
    async def test_the_row_survives_a_withdrawal(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        """A soft delete: history points at this row, so it must not vanish."""
        product = await make_product(sku="DEL-100")

        assert (
            await client.delete(f"{PRODUCTS}/{product.id}", headers=admin_headers)
        ).status_code == 204

        still_there = (
            await session.execute(
                text("SELECT is_active FROM products WHERE id = :pid"), {"pid": product.id}
            )
        ).scalar_one()
        assert still_there is False

    async def test_a_withdrawn_product_leaves_the_public_catalogue(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="DEL-101")
        await client.delete(f"{PRODUCTS}/{product.id}", headers=admin_headers)

        assert (await client.get(f"{PRODUCTS}/{product.id}")).status_code == 404

    async def test_a_withdrawn_product_can_be_brought_back(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """Withdrawal is reversible. That is the point of not deleting."""
        product = await make_product(sku="DEL-102")
        await client.delete(f"{PRODUCTS}/{product.id}", headers=admin_headers)

        response = await client.patch(
            f"{PRODUCTS}/{product.id}", headers=admin_headers, json={"is_active": True}
        )

        assert response.status_code == 200
        assert response.json()["is_active"] is True


class TestPermanentDelete:
    async def test_a_product_never_ordered_is_erased(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        """The mistake case: created by accident, sold to nobody."""
        product = await make_product(sku="DEL-200")

        response = await client.delete(
            f"{PRODUCTS}/{product.id}?permanent=true", headers=admin_headers
        )

        assert response.status_code == 204
        remaining = (
            await session.execute(
                text("SELECT count(*) FROM products WHERE id = :pid"), {"pid": product.id}
            )
        ).scalar_one()
        assert remaining == 0

    async def test_erasing_takes_the_inventory_row_with_it(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
        session: AsyncSession,
    ) -> None:
        product = await make_product(sku="DEL-201", stock=7)

        await client.delete(f"{PRODUCTS}/{product.id}?permanent=true", headers=admin_headers)

        orphans = (
            await session.execute(
                text("SELECT count(*) FROM inventory WHERE product_id = :pid"),
                {"pid": product.id},
            )
        ).scalar_one()
        assert orphans == 0

    async def test_a_product_that_was_ordered_cannot_be_erased(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """Deleting it would erase the record of a real sale."""
        product = await make_product(sku="DEL-202", stock=5)
        placed = await client.post(
            ORDERS,
            headers=auth_headers,
            json={
                "shipping_address": SHIPPING_ADDRESS,
                "items": [{"product_id": str(product.id), "quantity": 1}],
            },
        )
        assert placed.status_code == 201

        response = await client.delete(
            f"{PRODUCTS}/{product.id}?permanent=true", headers=admin_headers
        )

        assert response.status_code == 409
        assert "Withdraw it from sale instead" in response.json()["detail"]

    async def test_the_order_survives_the_refused_delete(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """A rolled-back delete must leave nothing half-removed behind."""
        product = await make_product(sku="DEL-203", stock=5)
        order = (
            await client.post(
                ORDERS,
                headers=auth_headers,
                json={
                    "shipping_address": SHIPPING_ADDRESS,
                    "items": [{"product_id": str(product.id), "quantity": 2}],
                },
            )
        ).json()

        await client.delete(f"{PRODUCTS}/{product.id}?permanent=true", headers=admin_headers)

        fetched = await client.get(f"{ORDERS}/{order['id']}", headers=auth_headers)
        assert fetched.status_code == 200
        assert fetched.json()["items"][0]["quantity"] == 2

    async def test_it_can_still_be_withdrawn_after_the_refusal(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """The error tells the admin what to do instead; it has to work."""
        product = await make_product(sku="DEL-204", stock=5)
        await client.post(
            ORDERS,
            headers=auth_headers,
            json={
                "shipping_address": SHIPPING_ADDRESS,
                "items": [{"product_id": str(product.id), "quantity": 1}],
            },
        )
        await client.delete(f"{PRODUCTS}/{product.id}?permanent=true", headers=admin_headers)

        assert (
            await client.delete(f"{PRODUCTS}/{product.id}", headers=admin_headers)
        ).status_code == 204

    async def test_a_customer_cannot_erase_anything(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="DEL-205")

        response = await client.delete(
            f"{PRODUCTS}/{product.id}?permanent=true", headers=auth_headers
        )

        assert response.status_code == 403

    async def test_erasing_something_that_is_not_there_is_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.delete(
            f"{PRODUCTS}/{uuid.uuid4()}?permanent=true", headers=admin_headers
        )

        assert response.status_code == 404
