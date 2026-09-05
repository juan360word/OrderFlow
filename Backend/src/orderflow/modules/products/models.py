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

    # Stock Keeping Unit: the human/warehouse-facing identifier. Unique, and
    # the natural key an admin or an import job will reference — while `id`
    # stays the immutable internal key that foreign keys point at.
    sku: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # NUMERIC(12, 2), never FLOAT. Binary floating point cannot represent 0.10
    # exactly, so float money accumulates error and 19.99 + 0.01 stops being
    # 20.00. NUMERIC is exact decimal arithmetic; SQLAlchemy maps it to
    # Python's Decimal. 12 digits caps a single price at 9,999,999,999.99.
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # ISO 4217. Stored per row so a future multi-currency catalogue does not
    # require a migration, and so an amount is never ambiguous.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))

    # Soft delete. A product referenced by historical orders can never be
    # physically removed without destroying that history, so "delete" means
    # "stop selling": is_active=false hides it from the catalogue while every
    # foreign key stays valid.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    inventory: Mapped[Inventory | None] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    __table_args__ = (
        # The database refuses a negative or absurd price regardless of which
        # code path writes it — a bug in a service cannot corrupt the catalogue.
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint("char_length(name) >= 1", name="name_not_empty"),
        CheckConstraint("char_length(sku) >= 3", name="sku_min_length"),
        # Partial index for the catalogue listing, which always filters on
        # is_active = true. Indexing only the live rows keeps it small.
        Index(
            "ix_products_active_created_at",
            "created_at",
            postgresql_where=text("is_active"),
        ),
        # Case-insensitive search on name, used by the ?search= filter.
        Index("ix_products_name_lower", text("lower(name)")),
    )
