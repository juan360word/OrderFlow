"""Outbox rules and the event dispatcher.

The dual write problem, concretely:

    1. COMMIT the order in PostgreSQL.
    2. Publish OrderCreated to SQS.
    3. The process dies between 1 and 2.

The order exists and the event does not. No consumer will ever hear about it,
and nothing in the system can detect that. Reversing the order is no better:
publish first and the crash leaves an event for an order that was never
created. There is no ordering of two independent systems that is atomic.

The outbox removes the second system from the critical path. The event is
written to a *table*, in the same transaction as the order, so the database's
own atomicity covers both. A separate relay then moves rows to the broker,
retrying until it succeeds.

What that buys, precisely: the event is published **at least once**. If the
relay dies after SQS accepts the message but before the row is marked, the row
is still pending and will be published again. Exactly-once delivery is not
achievable here, which is why consumers deduplicate on ``event_id``.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Settings
from orderflow.core.logging import get_logger
from orderflow.core.messaging import MessagePublisher, MessagePublishError
from orderflow.modules.outbox.models import OutboxEvent, OutboxStatus
from orderflow.modules.outbox.repository import OutboxRepository
from orderflow.shared.events import DomainEvent, EventType

logger = get_logger(__name__)


class EventDispatcher(Protocol):
    """How a business service hands off an event."""

    async def dispatch(self, event: DomainEvent) -> None: ...


class OutboxService:
    """Writes events to the outbox and hands them to the relay."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = OutboxRepository(session)

    async def dispatch(self, event: DomainEvent) -> None:
        """Stage an event inside the caller's transaction.

        Deliberately no commit and no flush of anything else: this row lives or
        dies with the business change that produced it.
        """
        self._repo.add(
            OutboxEvent(
                id=event.event_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type.value,
                payload=event.to_message(),
                status=OutboxStatus.PENDING,
            )
        )
        await self._session.flush()
        logger.info(
            "event_staged_in_outbox",
            event_id=str(event.event_id),
            event_type=event.event_type.value,
        )

    async def relay_batch(self, publisher: MessagePublisher) -> tuple[int, int]:
        """Publish one batch of pending events. Returns (published, failed)."""
        batch = await self._repo.claim_batch(limit=self._settings.outbox_batch_size)
        if not batch:
            return 0, 0

        published = failed = 0
        for row in batch:
            try:
                await publisher.publish(self._to_domain_event(row))
            except (MessagePublishError, ValueError) as exc:
                await self._repo.mark_failed(
                    row,
                    error=str(exc),
                    max_attempts=self._settings.outbox_max_attempts,
                    backoff_base_seconds=self._settings.outbox_poll_interval_seconds,
                )
                failed += 1
                logger.warning(
                    "outbox_publish_failed",
                    event_id=str(row.id),
                    attempts=row.attempts,
                    status=row.status.value,
                )
            else:
                await self._repo.mark_published(row)
                published += 1

        logger.info("outbox_batch_relayed", published=published, failed=failed)
        return published, failed

    async def claim_processing_slot(
        self, *, event_id: uuid.UUID, consumer: str, event_type: str
    ) -> bool:
        """Deduplication hook for consumers. False means "already handled"."""
        return await self._repo.claim_processing_slot(
            event_id=event_id, consumer=consumer, event_type=event_type
        )

    async def stats(self) -> dict[str, int]:
        return await self._repo.stats()

    async def purge_published(self, *, older_than_days: int = 7) -> int:
        return await self._repo.purge_published(older_than_days=older_than_days)

    @staticmethod
    def _to_domain_event(row: OutboxEvent) -> DomainEvent:
        return DomainEvent(
            event_id=row.id,
            event_type=EventType(row.event_type),
            aggregate_type=row.aggregate_type,
            aggregate_id=row.aggregate_id,
            payload=row.payload.get("payload", {}),
        )


class DirectEventDispatcher:
    """Publishes straight to the broker, with no outbox row.

    Kept alongside the outbox so the two can be compared under identical
    conditions, and because the failure it exhibits is the entire justification
    for the outbox: the publish happens after the transaction commits, so a
    crash in between loses the event with no trace.

    Selected with ``EVENT_DELIVERY_MODE=direct``. Not the default, and not
    appropriate for anything that matters.
    """

    def __init__(self, publisher: MessagePublisher) -> None:
        self._publisher = publisher

    async def dispatch(self, event: DomainEvent) -> None:
        try:
            await self._publisher.publish(event)
        except MessagePublishError:
            logger.error(
                "event_lost_direct_dispatch",
                event_id=str(event.event_id),
                event_type=event.event_type.value,
            )


class NullEventDispatcher:
    """Drops events. Used when messaging is switched off."""

    async def dispatch(self, event: DomainEvent) -> None:
        logger.debug("event_discarded", event_type=event.event_type.value)
