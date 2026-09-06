"""Data access for the transactional outbox."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.modules.outbox.models import OutboxEvent, OutboxStatus, ProcessedEvent


class OutboxRepository:
    """Queries over ``outbox_events`` and ``processed_events``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: OutboxEvent) -> OutboxEvent:
        self._session.add(event)
        return event

    async def claim_batch(self, *, limit: int) -> list[OutboxEvent]:
        """Take the next publishable rows, locked against other relay workers.

        ``FOR UPDATE SKIP LOCKED`` is what allows more than one relay to run.
        Each worker locks the rows it takes, and other workers step over them
        instead of queueing, so throughput scales with the number of workers
        and no event is ever published twice by two of them in the same moment.
        """
        result = await self._session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.PENDING,
                OutboxEvent.available_at <= datetime.now(UTC),
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def mark_published(self, event: OutboxEvent) -> None:
        event.status = OutboxStatus.PUBLISHED
        event.published_at = datetime.now(UTC)
        event.last_error = None
        await self._session.flush()

    async def mark_failed(
        self, event: OutboxEvent, *, error: str, max_attempts: int, backoff_base_seconds: float
    ) -> None:
        """Schedule a retry, or give up once the attempt budget is spent.

        The delay doubles with each attempt. Retrying immediately against a
        broker that is down just burns the attempt budget in a second and
        turns a brief outage into a permanent failure.
        """
        event.attempts += 1
        event.last_error = error[:2000]
        if event.attempts >= max_attempts:
            event.status = OutboxStatus.FAILED
        else:
            delay = backoff_base_seconds * (2 ** (event.attempts - 1))
            event.available_at = datetime.now(UTC) + timedelta(seconds=min(delay, 3600))
        await self._session.flush()

    async def count_by_status(self, status: OutboxStatus) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == status)
        )
        return int(result.scalar_one())

    async def get(self, event_id: uuid.UUID) -> OutboxEvent | None:
        return await self._session.get(OutboxEvent, event_id)

    async def list_for_aggregate(
        self, aggregate_type: str, aggregate_id: uuid.UUID
    ) -> list[OutboxEvent]:
        result = await self._session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_type == aggregate_type,
                OutboxEvent.aggregate_id == aggregate_id,
            )
            .order_by(OutboxEvent.created_at)
        )
        return list(result.scalars().all())

    async def purge_published(self, *, older_than_days: int, limit: int = 1000) -> int:
        """Trim the published tail so the table does not grow forever."""
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        subquery = (
            select(OutboxEvent.id)
            .where(
                OutboxEvent.status == OutboxStatus.PUBLISHED,
                OutboxEvent.published_at <= cutoff,
            )
            .limit(limit)
            .scalar_subquery()
        )
        result = await self._session.execute(
            delete(OutboxEvent).where(OutboxEvent.id.in_(subquery))
        )
        return int(result.rowcount) if isinstance(result, CursorResult) else 0

    async def claim_processing_slot(
        self, *, event_id: uuid.UUID, consumer: str, event_type: str
    ) -> bool:
        """Record that this consumer is handling this event.

        Returns ``False`` when the row already exists, which means the event is
        a redelivery and must be skipped. The insert is the deduplication:
        checking first and inserting after would leave a window in which two
        workers both see "not processed" and both do the work.
        """
        stmt = (
            insert(ProcessedEvent)
            .values(event_id=event_id, consumer=consumer, event_type=event_type)
            .on_conflict_do_nothing(index_elements=["event_id", "consumer"])
            .returning(ProcessedEvent.event_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def was_processed(self, *, event_id: uuid.UUID, consumer: str) -> bool:
        result = await self._session.execute(
            select(ProcessedEvent.event_id).where(
                ProcessedEvent.event_id == event_id, ProcessedEvent.consumer == consumer
            )
        )
        return result.first() is not None

    async def stats(self) -> dict[str, Any]:
        result = await self._session.execute(
            select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)
        )
        return {status.value: int(count) for status, count in result.all()}
