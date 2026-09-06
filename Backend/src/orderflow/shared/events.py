"""Domain events exchanged between the API and its workers."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self


class EventType(StrEnum):
    """Every event the system publishes."""

    ORDER_CREATED = "order.created"
    ORDER_CONFIRMED = "order.confirmed"
    ORDER_CANCELLED = "order.cancelled"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Something that already happened, described in the past tense.

    ``event_id`` is what makes a consumer able to deduplicate. Queues deliver
    at least once, so the same event will eventually arrive twice; a consumer
    that has recorded this id can recognise the repeat and skip it.

    ``payload`` carries a copy of the data the consumer needs rather than just
    an id to look up. That keeps the consumer working when the source row has
    changed since, and it is the difference between an event and a
    notification.
    """

    event_type: EventType
    aggregate_type: str
    aggregate_id: uuid.UUID
    payload: dict[str, Any]
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_message(self) -> dict[str, Any]:
        """Wire representation. JSON-safe, versioned, self-describing."""
        return {
            "schema_version": 1,
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id),
            "occurred_at": self.occurred_at.isoformat(),
            "payload": self.payload,
        }

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> Self:
        """Parse a message off the queue.

        Raises ``ValueError`` on anything malformed, which the worker turns
        into a rejected message rather than a crash.
        """
        try:
            return cls(
                event_id=uuid.UUID(message["event_id"]),
                event_type=EventType(message["event_type"]),
                aggregate_type=str(message["aggregate_type"]),
                aggregate_id=uuid.UUID(message["aggregate_id"]),
                occurred_at=datetime.fromisoformat(message["occurred_at"]),
                payload=dict(message["payload"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Malformed event message: {exc}") from exc

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
