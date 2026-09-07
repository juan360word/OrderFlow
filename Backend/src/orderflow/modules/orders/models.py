"""Order tables: order headers and their line items."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orderflow.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from orderflow.modules.orders.state_machine import OrderStatus

if TYPE_CHECKING:  # pragma: no cover
    from orderflow.modules.auth.models import User

MAX_ITEMS_PER_ORDER = 50


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A purchase placed by a customer."""

    __tablename__ = "orders"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            native_enum=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=text("'pending'"),
    )

    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))

    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship(lazy="raise_on_sql")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="OrderItem.created_at",
    )
    shipping_address: Mapped[ShippingAddress | None] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        # Never lazy-loaded behind the scenes: an order read outside a session
        # would emit a surprise query, and the order listing deliberately does
        # not fetch addresses at all.
        lazy="raise_on_sql",
        single_parent=True,
    )

    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="total_amount_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="cancelled_at_matches_status",
        ),
        CheckConstraint(
            "status <> 'confirmed' OR confirmed_at IS NOT NULL",
            name="confirmed_at_present_when_confirmed",
        ),
        Index("ix_orders_user_id_created_at", "user_id", text("created_at DESC")),
        Index(
            "ix_orders_pending_created_at",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def reservation_reference(self) -> str:
        """Identifier this order uses when holding stock in the inventory module."""
        return f"order:{self.id}"


class ShippingAddress(Base, TimestampMixin):
    """Where one order has to be delivered.

    A separate table rather than nine more columns on ``orders``, for two
    reasons. The listing endpoint reads orders constantly and never needs the
    address, so it stays out of that row; and an order placed before this
    existed simply has no row, which is honest — far better than nine NULL
    columns that pretend the question was asked and left blank.

    The address is a *snapshot*, not a link to an address book. It records
    where this order was sent, and it must not change when the customer later
    edits their profile — the same reason ``OrderItem`` freezes the price and
    the product name at purchase time. That is also why there is no
    ``user_id`` here: this row belongs to the order, not to the person.
    """

    __tablename__ = "order_shipping_addresses"

    # The primary key *is* the foreign key: one address per order, enforced by
    # the schema rather than by a unique index bolted on afterwards.
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        primary_key=True,
    )

    recipient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str] = mapped_column(String(120), nullable=False)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # ISO 3166-1 alpha-2. A code, not a free-text country name: "Colombia",
    # "colombia" and "CO" are the same destination, and a carrier API will
    # only accept one of them.
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    order: Mapped[Order] = relationship(back_populates="shipping_address")

    __table_args__ = (
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="country_iso3166_alpha2"),
        CheckConstraint("length(btrim(recipient_name)) > 0", name="recipient_name_not_blank"),
        CheckConstraint("length(btrim(line1)) > 0", name="line1_not_blank"),
        CheckConstraint("length(btrim(city)) > 0", name="city_not_blank"),
    )

    @property
    def single_line(self) -> str:
        """The address as one line, for logs and labels."""
        parts = [self.line1, self.line2, self.city, self.region, self.postal_code, self.country]
        return ", ".join(part for part in parts if part)


class OrderItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One product line inside an order, with the catalogue data frozen at purchase time."""

    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    product_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        Computed("quantity * unit_price", persisted=True),
        nullable=False,
    )

    order: Mapped[Order] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_items_order_id_product_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(f"quantity <= {MAX_ITEMS_PER_ORDER * 1000}", name="quantity_sane"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
    )
