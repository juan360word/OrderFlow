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


class OrderItemRequest(BaseModel):
    """One requested line of a new order."""

    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: QuantityField


class OrderCreate(BaseModel):
    """Payload for ``POST /orders``."""

    model_config = ConfigDict(extra="forbid")

    items: Annotated[list[OrderItemRequest], Field(min_length=1, max_length=MAX_ITEMS_PER_ORDER)]

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
