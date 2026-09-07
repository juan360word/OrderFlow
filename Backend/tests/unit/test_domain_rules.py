"""Pure business-rule tests: no database, no Redis, no HTTP, no containers.

These are the base of the pyramid. They are fast enough to run on every
keystroke, which is only true because nothing here reaches outside the process.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError

from orderflow.modules.auth.schemas import RegisterRequest
from orderflow.modules.idempotency.service import IdempotencyService
from orderflow.modules.orders.schemas import OrderCreate, OrderItemRequest
from orderflow.modules.orders.service import PricedLine
from orderflow.modules.products.models import Product
from orderflow.modules.products.schemas import ProductCreate, ProductUpdate
from orderflow.shared.events import DomainEvent, EventType
from orderflow.shared.pagination import MAX_PAGE_SIZE, Page, PageParams
from tests.conftest import SHIPPING_INPUT

pytestmark = pytest.mark.unit


class TestOrderBasketValidation:
    def test_a_basket_needs_at_least_one_line(self) -> None:
        with pytest.raises(ValidationError):
            OrderCreate(shipping_address=SHIPPING_INPUT, items=[])

    def test_a_product_cannot_appear_twice(self) -> None:
        product_id = uuid.uuid4()

        with pytest.raises(ValidationError, match="only once"):
            OrderCreate(
                shipping_address=SHIPPING_INPUT,
                items=[
                    OrderItemRequest(product_id=product_id, quantity=1),
                    OrderItemRequest(product_id=product_id, quantity=2),
                ],
            )

    @pytest.mark.parametrize("quantity", [0, -5, 20_000])
    def test_quantities_are_bounded(self, quantity: int) -> None:
        with pytest.raises(ValidationError):
            OrderItemRequest(product_id=uuid.uuid4(), quantity=quantity)


class TestLinePricing:
    @pytest.mark.parametrize(
        ("price", "quantity", "expected"),
        [
            ("19.99", 3, "59.97"),
            ("0.10", 3, "0.30"),
            ("0.01", 7, "0.07"),
            ("1000000.00", 2, "2000000.00"),
        ],
    )
    def test_a_subtotal_is_exact(self, price: str, quantity: int, expected: str) -> None:
        """Decimal arithmetic, never float: 0.10 * 3 must be 0.30, not 0.30000000000000004."""
        line = PricedLine(
            product=Product(sku="X-1", name="X", price=Decimal(price), currency="USD"),
            quantity=quantity,
        )

        assert line.subtotal == Decimal(expected)

    def test_float_would_get_this_wrong(self) -> None:
        """The reason the column is NUMERIC, stated as an executable fact."""
        assert 0.1 * 3 != 0.3
        assert Decimal("0.10") * 3 == Decimal("0.30")


class TestPasswordPolicy:
    def test_a_short_password_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(email="a@example.com", password=SecretStr("short"), full_name="A B")

    def test_a_common_password_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(
                email="a@example.com",
                password=SecretStr("passwordpassword"),
                full_name="A B",
            )

    def test_a_password_containing_the_email_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(
                email="danielserrato@example.com",
                password=SecretStr("danielserrato99"),
                full_name="A B",
            )

    def test_a_good_passphrase_is_accepted(self) -> None:
        request = RegisterRequest(
            email="a@example.com",
            password=SecretStr("correct-horse-battery-staple"),
            full_name="A B",
        )

        assert request.email == "a@example.com"

    def test_unknown_fields_are_refused(self) -> None:
        """Blocks the mass-assignment attempt {"role": "admin"}."""
        with pytest.raises(ValidationError):
            RegisterRequest(
                email="a@example.com",
                password=SecretStr("correct-horse-battery-staple"),
                full_name="A B",
                role="admin",
            )


class TestProductSchemas:
    def test_a_sku_is_uppercased(self) -> None:
        assert ProductCreate(sku="abc-1", name="X", price=Decimal("1.00")).sku == "ABC-1"

    @pytest.mark.parametrize("price", ["-1.00", "1.234"])
    def test_invalid_prices_are_rejected(self, price: str) -> None:
        with pytest.raises(ValidationError):
            ProductCreate(sku="ABC-1", name="X", price=Decimal(price))

    def test_a_patch_reports_only_what_was_sent(self) -> None:
        """``exclude_unset`` is what stops PATCH from nulling untouched columns."""
        assert ProductUpdate(price=Decimal("5.00")).changed_fields() == {"price": Decimal("5.00")}

    def test_an_empty_patch_changes_nothing(self) -> None:
        assert ProductUpdate().changed_fields() == {}


class TestIdempotencyFingerprint:
    def test_the_same_request_hashes_the_same(self) -> None:
        payload = {"items": [{"product_id": "abc", "quantity": 2}]}

        first = IdempotencyService._fingerprint("POST", "/orders", payload)
        second = IdempotencyService._fingerprint("POST", "/orders", payload)

        assert first == second

    def test_key_order_does_not_change_the_hash(self) -> None:
        """Canonical JSON: the client's serialisation order must not matter."""
        assert IdempotencyService._fingerprint(
            "POST", "/orders", {"a": 1, "b": 2}
        ) == IdempotencyService._fingerprint("POST", "/orders", {"b": 2, "a": 1})

    def test_a_different_payload_hashes_differently(self) -> None:
        assert IdempotencyService._fingerprint(
            "POST", "/orders", {"quantity": 1}
        ) != IdempotencyService._fingerprint("POST", "/orders", {"quantity": 2})

    def test_the_same_body_on_a_different_route_hashes_differently(self) -> None:
        assert IdempotencyService._fingerprint(
            "POST", "/orders", {"a": 1}
        ) != IdempotencyService._fingerprint("POST", "/refunds", {"a": 1})


class TestEventSerialization:
    def test_an_event_survives_the_wire(self) -> None:
        original = DomainEvent(
            event_type=EventType.ORDER_CREATED,
            aggregate_type="order",
            aggregate_id=uuid.uuid4(),
            payload={"total_amount": "19.99"},
        )

        restored = DomainEvent.from_message(original.to_message())

        assert restored == original

    def test_the_id_is_stable_across_serialisation(self) -> None:
        """Deduplication depends on this id being the same on both sides."""
        event = DomainEvent(
            event_type=EventType.ORDER_CREATED,
            aggregate_type="order",
            aggregate_id=uuid.uuid4(),
            payload={},
        )

        assert DomainEvent.from_message(event.to_message()).event_id == event.event_id


class TestPagination:
    def test_the_page_size_is_capped(self) -> None:
        """An uncapped ?limit is a one-request denial of service."""
        with pytest.raises(ValidationError):
            PageParams(limit=MAX_PAGE_SIZE + 1)

    def test_it_reports_whether_more_rows_exist(self) -> None:
        page: Page[int] = Page(items=[1, 2], total=10, limit=2, offset=0)

        assert page.has_more is True

    def test_the_last_page_reports_no_more(self) -> None:
        page: Page[int] = Page(items=[9, 10], total=10, limit=2, offset=8)

        assert page.has_more is False
