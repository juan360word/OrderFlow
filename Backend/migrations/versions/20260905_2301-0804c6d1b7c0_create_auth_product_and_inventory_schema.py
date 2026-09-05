"""create auth, product and inventory schema

Baseline schema for phases 3-6: roles, users, refresh_tokens, products,
inventory and inventory_reservations.

Hand-edited after autogeneration, which is the normal workflow — Alembic diffs
the models, a human reviews the SQL. Three things it cannot infer:

  1. The `citext` and `pgcrypto` extensions the columns depend on.
  2. The seed rows for `roles`: the application cannot register a user without
     them, so they belong to the schema, not to a setup script someone forgets
     to run.
  3. Dropping the native enum type in downgrade — CREATE TYPE is implicit in
     CREATE TABLE, DROP TYPE is not.

Revision ID: 0804c6d1b7c0
Revises:
Create Date: 2026-09-05 23:01:19.097178+00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0804c6d1b7c0"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "products",
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_products_currency_iso4217")),
        sa.CheckConstraint("char_length(name) >= 1", name=op.f("ck_products_name_not_empty")),
        sa.CheckConstraint("char_length(sku) >= 3", name=op.f("ck_products_sku_min_length")),
        sa.CheckConstraint("price >= 0", name=op.f("ck_products_price_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("sku", name=op.f("uq_products_sku")),
    )
    op.create_index(
        "ix_products_active_created_at",
        "products",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_products_name_lower", "products", [sa.literal_column("lower(name)")], unique=False
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("name", name=op.f("uq_roles_name")),
    )
    op.create_table(
        "inventory",
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("quantity_available", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("quantity_reserved", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity_available + quantity_reserved <= 1000000000",
            name=op.f("ck_inventory_total_quantity_sane"),
        ),
        sa.CheckConstraint(
            "quantity_available >= 0", name=op.f("ck_inventory_available_non_negative")
        ),
        sa.CheckConstraint(
            "quantity_reserved >= 0", name=op.f("ck_inventory_reserved_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_inventory_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("product_id", name=op.f("pk_inventory")),
    )
    op.create_table(
        "users",
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "failed_login_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(email) BETWEEN 3 AND 254", name=op.f("ck_users_email_length")
        ),
        sa.CheckConstraint(
            "failed_login_attempts >= 0", name=op.f("ck_users_failed_login_attempts_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name=op.f("fk_users_role_id_roles"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_role_id"), "users", ["role_id"], unique=False)
    op.create_table(
        "inventory_reservations",
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("reference", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("held", "confirmed", "released", name="reservation_status"),
            server_default=sa.text("'held'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_inventory_reservations_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["inventory.product_id"],
            name=op.f("fk_inventory_reservations_product_id_inventory"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_reservations")),
        sa.UniqueConstraint(
            "reference", "product_id", name="uq_inventory_reservations_reference_product"
        ),
    )
    op.create_index(
        "ix_inventory_reservations_expiring",
        "inventory_reservations",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'held'"),
    )
    op.create_index(
        op.f("ix_inventory_reservations_status"), "inventory_reservations", ["status"], unique=False
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        sa.Column("created_by_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "expires_at > issued_at", name=op.f("ck_refresh_tokens_expiry_after_issue")
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_tokens.id"],
            name=op.f("fk_refresh_tokens_replaced_by_id_refresh_tokens"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        "ix_refresh_tokens_active",
        "refresh_tokens",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)

    op.execute(
        """
        INSERT INTO roles (name, description) VALUES
            ('customer', 'Can browse the catalogue and place orders'),
            ('admin', 'Can manage products and inventory')
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(
        "ix_refresh_tokens_active",
        table_name="refresh_tokens",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_inventory_reservations_status"), table_name="inventory_reservations")
    op.drop_index(
        "ix_inventory_reservations_expiring",
        table_name="inventory_reservations",
        postgresql_where=sa.text("status = 'held'"),
    )
    op.drop_table("inventory_reservations")
    op.drop_index(op.f("ix_users_role_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("inventory")
    op.drop_table("roles")
    op.drop_index("ix_products_name_lower", table_name="products")
    op.drop_index(
        "ix_products_active_created_at",
        table_name="products",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("products")

    postgresql.ENUM(name="reservation_status").drop(op.get_bind(), checkfirst=True)
