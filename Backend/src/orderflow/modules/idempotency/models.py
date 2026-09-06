"""Idempotency key storage."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from orderflow.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 255


class IdempotencyStatus(StrEnum):
    """Lifecycle of a single idempotency record."""

    PROCESSING = "processing"
    COMPLETED = "completed"


class IdempotencyKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One client-supplied key, its in-flight state and the stored response.

    The stored response is the point. Recording only "this key was seen" would
    let a retry return an empty acknowledgement, and the client would never
    learn the id of the order it created. What is replayed has to be the whole
    original answer.
    """

    __tablename__ = "idempotency_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(MAX_KEY_LENGTH), nullable=False)

    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)

    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[IdempotencyStatus] = mapped_column(
        Enum(
            IdempotencyStatus,
            name="idempotency_status",
            native_enum=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=text("'processing'"),
    )

    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_idempotency_keys_user_id_key"),
        CheckConstraint(
            f"char_length(key) BETWEEN {MIN_KEY_LENGTH} AND {MAX_KEY_LENGTH}",
            name="key_length",
        ),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="completed_at_matches_status",
        ),
        CheckConstraint(
            "status <> 'completed' OR response_status_code IS NOT NULL",
            name="completed_records_a_response",
        ),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )
