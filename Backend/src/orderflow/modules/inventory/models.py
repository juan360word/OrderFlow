"""Inventory tables — the heart of the concurrency work.

Two tables:

* ``inventory``  — the current counters for one product.
* ``inventory_reservations`` — the ledger of who holds what, which is what
  makes reserve → confirm/release auditable and idempotent.

The invariant this schema must never allow to break:

    quantity_available >= 0  AND  quantity_reserved >= 0

It is enforced by CHECK constraints. Those constraints are the *last* line of
defence, not the first: the service uses a transaction plus a lock (or an
atomic conditional UPDATE) so the constraint is never even approached. But if
some future code path forgets, PostgreSQL aborts the transaction instead of
selling stock that does not exist.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orderflow.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from orderflow.modules.products.models import Product


class ReservationStatus(StrEnum):
    """Lifecycle of a single stock reservation."""

    HELD = "held"
    CONFIRMED = "confirmed"
    RELEASED = "released"


class Inventory(Base, TimestampMixin):
    """Stock counters for one product.

    Availability is split in two columns rather than kept as a single number:

    * ``quantity_available`` — free to be sold right now.
    * ``quantity_reserved``  — held for in-flight orders, not yet shipped.

    A single ``stock`` column cannot express "the customer is in checkout".
    Either you decrement at checkout (and oversell nothing but lose stock to
    abandoned carts), or you decrement at payment (and oversell). Two columns
    let a reservation move quantity from available to reserved atomically, and
    the physical total is always available + reserved.
    """

    __tablename__ = "inventory"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    )

    quantity_available: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    quantity_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))

    product: Mapped[Product] = relationship(back_populates="inventory")
    reservations: Mapped[list[InventoryReservation]] = relationship(
        back_populates="inventory",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("quantity_available >= 0", name="available_non_negative"),
        CheckConstraint("quantity_reserved >= 0", name="reserved_non_negative"),
        CheckConstraint(
            "quantity_available + quantity_reserved <= 1000000000",
            name="total_quantity_sane",
        ),
    )

    @property
    def quantity_total(self) -> int:
        """Physical units in the warehouse (sellable + held)."""
        return self.quantity_available + self.quantity_reserved


class InventoryReservation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One hold on stock, owned by an external reference (an order).

    ``reference`` is the caller's identifier for the operation — in practice
    the order id. Combined with ``product_id`` in a UNIQUE constraint it makes
    ``reserve`` **idempotent at the database level**: a client that retries
    after a network timeout cannot create a second hold, because the second
    INSERT violates the unique index. That is the same mechanism the dedicated
    idempotency-key work builds on later, applied here where it is cheapest.
    """

    __tablename__ = "inventory_reservations"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory.product_id", ondelete="CASCADE"),
        nullable=False,
    )
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[ReservationStatus] = mapped_column(
        Enum(
            ReservationStatus,
            name="reservation_status",
            native_enum=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=text("'held'"),
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    inventory: Mapped[Inventory] = relationship(back_populates="reservations")

    __table_args__ = (
        UniqueConstraint(
            "reference", "product_id", name="uq_inventory_reservations_reference_product"
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        Index(
            "ix_inventory_reservations_expiring",
            "expires_at",
            postgresql_where=text("status = 'held'"),
        ),
    )
