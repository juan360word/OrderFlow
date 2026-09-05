"""Auth tables: roles, users, refresh tokens.

Schema decisions are justified inline. The guiding principle: an invariant that
must always hold is enforced by the database, not by application code. Code can
be bypassed by a migration, a script, a psql session, or simply by two requests
racing; a constraint cannot.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orderflow.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ROLE_CUSTOMER: Final = "customer"
ROLE_ADMIN: Final = "admin"


class Role(Base, TimestampMixin):
    """A named set of permissions.

    A lookup table rather than a PostgreSQL ENUM or a plain string column:
    adding a role later ("warehouse_operator") is an INSERT, whereas altering
    an ENUM type takes a lock on every table that uses it, and a free string
    column would let a typo ("admn") silently create an unprivileged account.
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    users: Mapped[list[User]] = relationship(back_populates="role")


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An account: a customer or an administrator."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    full_name: Mapped[str] = mapped_column(String(120), nullable=False)

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("failed_login_attempts >= 0", name="failed_login_attempts_non_negative"),
        CheckConstraint("char_length(email) BETWEEN 3 AND 254", name="email_length"),
    )

    @property
    def role_name(self) -> str:
        return self.role.name

    @property
    def is_locked(self) -> bool:
        """True while a temporary lockout from failed logins is in effect."""
        from datetime import UTC

        return self.locked_until is not None and self.locked_until > datetime.now(UTC)


class RefreshToken(Base, UUIDPrimaryKeyMixin):
    """A revocable, single-use session credential.

    Only the *hash* of the token is stored: a database leak must not hand the
    attacker live sessions. Rotation is modelled with ``replaced_by_id``, which
    turns the table into a chain per login session ("token family"). If a token
    that was already rotated is presented again, the original was stolen — the
    whole family is then revoked. That is the standard OAuth 2 BCP refresh
    token reuse detection.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )

    created_by_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        Index(
            "ix_refresh_tokens_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    @property
    def is_active(self) -> bool:
        from datetime import UTC

        return self.revoked_at is None and self.expires_at > datetime.now(UTC)
