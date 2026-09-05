"""Pydantic contracts for the inventory module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from orderflow.modules.inventory.models import ReservationStatus

QuantityField = Annotated[int, Field(gt=0, le=1_000_000)]


class StockResponse(BaseModel):
    """Current counters for one product."""

    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    quantity_available: int
    quantity_reserved: int
    quantity_total: int
    version: int
    updated_at: datetime


class StockAdjustment(BaseModel):
    """Payload for ``POST /inventory/{product_id}/adjust`` (admin only)."""

    model_config = ConfigDict(extra="forbid")

    # Signed: positive is a restock, negative is shrinkage/damage. Not
    # ``gt=0``, because a correction downwards is a real warehouse event.
    delta: Annotated[int, Field(ge=-1_000_000, le=1_000_000)]
    reason: Annotated[str, StringConstraints(min_length=3, max_length=255, strip_whitespace=True)]


class ReservationRequest(BaseModel):
    """Payload for ``POST /inventory/reservations``."""

    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: QuantityField
    # The caller's identifier for this operation (in practice, the order id).
    # Together with product_id it is UNIQUE in the database, which is what
    # makes a retried request return the original reservation instead of
    # creating a second hold.
    reference: Annotated[
        str, StringConstraints(min_length=1, max_length=128, strip_whitespace=True)
    ]


class ReservationResponse(BaseModel):
    """A stock hold."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    reference: str
    quantity: int
    status: ReservationStatus
    expires_at: datetime
    resolved_at: datetime | None
    created_at: datetime
