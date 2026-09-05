"""Product catalogue table."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Index, Numeric, String, Text, text
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
