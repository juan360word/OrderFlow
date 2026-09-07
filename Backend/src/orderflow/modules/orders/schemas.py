"""Pydantic contracts for the orders module."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from orderflow.modules.orders.models import MAX_ITEMS_PER_ORDER
from orderflow.modules.orders.state_machine import OrderStatus

QuantityField = Annotated[int, Field(gt=0, le=10_000)]

# Trimmed on the way in: " Bogotá " and "Bogotá" are the same city, and a
# trailing space in a delivery address is a defect nobody notices until a label
# is printed.
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ShippingAddressInput(BaseModel):
    """Where the customer wants the order delivered.

    Deliberately not a validator for every country's address format. Postal
    codes, region names and street conventions differ so much that a strict
    per-country parser rejects more valid addresses than invalid ones. What is
    enforced is what a courier genuinely cannot work without: a name, a phone
    number, a street, a city and a country.
    """

    model_config = ConfigDict(extra="forbid")

    recipient_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)
    ]
    # Permissive on purpose: "+57 300 123 4567" and "(300) 1234567" are both
    # real, and normalising them properly needs a phone library and the
    # destination country. What is refused is text that no courier could dial.
    phone: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=7,
            max_length=32,
            pattern=r"^[0-9+()\-.\s]+$",
        ),
    ]
    line1: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]
    line2: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None
    city: ShortText
    region: ShortText
    postal_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = (
        None
    )
    country: Annotated[
        str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{2}$")
    ]
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None

    @model_validator(mode="after")
    def _blank_optionals_are_absent(self) -> Self:
        """An empty box in a form means "not provided", not an empty string.

        Without this, a customer who tabs through the optional fields stores
        "" in the database and every later reader has to treat "" and NULL as
        the same thing.
        """
        for field in ("line2", "postal_code", "notes"):
            if getattr(self, field) == "":
                setattr(self, field, None)
        return self


class ShippingAddressResponse(BaseModel):
    """A delivery address as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    recipient_name: str
    phone: str
    line1: str
    line2: str | None = None
    city: str
    region: str
    postal_code: str | None = None
    country: str
    notes: str | None = None


class OrderItemRequest(BaseModel):
    """One requested line of a new order."""

    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: QuantityField


class OrderCreate(BaseModel):
    """Payload for ``POST /orders``."""

    model_config = ConfigDict(extra="forbid")

    items: Annotated[list[OrderItemRequest], Field(min_length=1, max_length=MAX_ITEMS_PER_ORDER)]
    # Required, not optional. These are physical goods: an order nobody can
    # deliver is not a lesser order, it is an unusable one, and letting it
    # through would only move the failure to the warehouse.
    shipping_address: ShippingAddressInput

    @model_validator(mode="after")
    def _reject_duplicate_products(self) -> Self:
        product_ids = [item.product_id for item in self.items]
        if len(set(product_ids)) != len(product_ids):
            raise ValueError(
                "Each product may appear only once; combine duplicates into one quantity."
            )
        return self


class OrderCancel(BaseModel):
    """Payload for ``POST /orders/{id}/cancel``."""

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(max_length=255, strip_whitespace=True)] | None = None


class OrderItemResponse(BaseModel):
    """One line of an order as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    product_sku: str
    product_name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


class OrderResponse(BaseModel):
    """An order with its lines."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    status: OrderStatus
    total_amount: Decimal
    currency: str
    items: list[OrderItemResponse]
    # Optional in the contract only because orders placed before addresses
    # existed still have none. Every new order has one.
    shipping_address: ShippingAddressResponse | None = None
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None


class OrderSummaryResponse(BaseModel):
    """An order without its lines, for list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    status: OrderStatus
    total_amount: Decimal
    currency: str
    item_count: int
    created_at: datetime


class OrderFilters(BaseModel):
    """Validated query filters for the order listing."""

    status: OrderStatus | None = None
