"""Product catalogue table."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orderflow.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from orderflow.modules.inventory.models import Inventory


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An item that can be sold."""

    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Marker for "this product has a picture", and the type needed to serve it.
    # It lives here, next to the other small columns, precisely so that the
    # bytes do not: a catalogue query reads this and never touches the blob.
    image_content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    inventory: Mapped[Inventory | None] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint("char_length(name) >= 1", name="name_not_empty"),
        CheckConstraint("char_length(sku) >= 3", name="sku_min_length"),
        Index(
            "ix_products_active_created_at",
            "created_at",
            postgresql_where=text("is_active"),
        ),
        Index("ix_products_name_lower", text("lower(name)")),
    )


class ProductImage(Base):
    """The bytes of one product picture.

    A separate table, not a column on ``products``, and that is the whole
    point. PostgreSQL can hold binary data perfectly well, but a blob sitting
    in the products table is read by every query that does not name its
    columns, and every ``SELECT *`` in the catalogue turns into megabytes over
    the wire. Splitting the payload out means the listing pays nothing for it
    and only the endpoint that serves the picture ever loads it.

    One row per product (the product id is the primary key), and the cascade
    means deleting a product takes its picture with it rather than leaving an
    orphan.
    """

    __tablename__ = "product_images"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (CheckConstraint("byte_size > 0", name="byte_size_positive"),)
