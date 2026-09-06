"""Transactional outbox tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from orderflow.db.base import Base, UUIDPrimaryKeyMixin


class OutboxStatus(StrEnum):
    """Publication state of one outbox row."""

    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class OutboxEvent(Base, UUIDPrimaryKeyMixin):
    """An event waiting to be published to the broker.

    The row is written in the same transaction as the business change it
    describes. That is the whole idea: PostgreSQL can commit the order and the
    event atomically because they are in the same database, which is exactly
    what a direct publish to SQS cannot do.

    The primary key doubles as the event id carried on the wire, so a consumer
    that deduplicates by event id is deduplicating on this row's identity.
    """

    __tablename__ = "outbox_events"

    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[OutboxStatus] = mapped_column(
        Enum(
            OutboxStatus,
            name="outbox_status",
            native_enum=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=text("'pending'"),
    )

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "(status = 'published') = (published_at IS NOT NULL)",
            name="published_at_matches_status",
        ),
        Index(
            "ix_outbox_events_ready",
            "available_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id"),
    )


class ProcessedEvent(Base):
    """Record that one consumer already handled one event.

    The outbox guarantees *at least once* delivery, never exactly once: the
    relay can publish a message and die before marking the row, and SQS can
    redeliver on its own. Exactly-once processing is therefore achieved at the
    consumer, by remembering what it has already done.

    The primary key is (event_id, consumer) so that two different consumers
    each get their own chance at the same event, and inserting it is what makes
    the deduplication atomic.
    """

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (Index("ix_processed_events_processed_at", "processed_at"),)
