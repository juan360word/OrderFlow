"""Pydantic contracts for the products module."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

PriceField = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
SkuField = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9][A-Z0-9\-_]{2,63}$",
        strip_whitespace=True,
    ),
]
NameField = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]


class ProductCreate(BaseModel):
    """Payload for ``POST /products`` (admin only)."""

    model_config = ConfigDict(extra="forbid")

    sku: SkuField
    name: NameField
    description: Annotated[str, Field(max_length=5000)] = ""
    price: PriceField
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD"
    initial_stock: Annotated[int, Field(ge=0, le=1_000_000)] = 0

    @field_validator("sku", mode="before")
    @classmethod
    def _normalise_sku(cls, value: object) -> object:
        """SKUs are uppercase by convention; normalise before the pattern runs."""
        return value.strip().upper() if isinstance(value, str) else value


class ProductUpdate(BaseModel):
    """Payload for ``PATCH /products/{id}`` (admin only).

    Every field is optional: PATCH means "change what I sent". ``sku`` is
    deliberately absent — it is referenced by warehouse systems and historical
    documents, so it is immutable once assigned.
    """

    model_config = ConfigDict(extra="forbid")

    name: NameField | None = None
    description: Annotated[str, Field(max_length=5000)] | None = None
    price: PriceField | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    is_active: bool | None = None

    def changed_fields(self) -> dict[str, object]:
        """Only the keys the client actually sent.

        ``exclude_unset`` is the reason PATCH works at all: without it, a field
        the client omitted would arrive as ``None`` and null out the column.
        """
        return self.model_dump(exclude_unset=True)


class ProductResponse(BaseModel):
    """Public representation of a product."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    description: str
    price: Decimal
    currency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductFilters(BaseModel):
    """Validated query filters for the catalogue listing."""

    search: Annotated[str, StringConstraints(max_length=100, strip_whitespace=True)] | None = None
    min_price: PriceField | None = None
    max_price: PriceField | None = None
    include_inactive: bool = False
