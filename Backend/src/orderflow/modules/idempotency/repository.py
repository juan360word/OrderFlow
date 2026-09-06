"""Data access for idempotency keys."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.modules.idempotency.models import IdempotencyKey, IdempotencyStatus


class IdempotencyRepository:
    """Queries over ``idempotency_keys``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self,
        *,
        user_id: uuid.UUID,
        key: str,
        method: str,
        path: str,
        fingerprint: str,
        expires_at: datetime,
    ) -> IdempotencyKey | None:
        """Try to take ownership of a key. ``None`` means somebody else has it.

        ``INSERT ... ON CONFLICT DO NOTHING`` is doing three jobs at once:

        * It inserts when the key is new.
        * It **blocks** while a concurrent transaction holds an uncommitted row
          for the same key, because the unique index makes the second inserter
          wait on the first. That is the same row-level serialisation the
          inventory module relies on, applied to a different problem.
        * It returns zero rows, without aborting our transaction, once that
          other transaction has committed. A plain INSERT would raise, and in
          PostgreSQL an error poisons the whole transaction, so we could not
          then read the stored response.
        """
        stmt = (
            insert(IdempotencyKey)
            .values(
                user_id=user_id,
                key=key,
                method=method,
                path=path,
                request_fingerprint=fingerprint,
                status=IdempotencyStatus.PROCESSING,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_keys_user_id_key")
            .returning(IdempotencyKey)
        )
        result = await self._session.execute(stmt, execution_options={"populate_existing": True})
        return result.scalar_one_or_none()

    async def get_for_update(self, *, user_id: uuid.UUID, key: str) -> IdempotencyKey | None:
        """Read an existing key with its row locked."""
        result = await self._session.execute(
            select(IdempotencyKey)
            .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def take_over_expired(
        self, record: IdempotencyKey, *, fingerprint: str, expires_at: datetime
    ) -> IdempotencyKey:
        """Recycle a key whose retention window has passed."""
        record.request_fingerprint = fingerprint
        record.status = IdempotencyStatus.PROCESSING
        record.response_status_code = None
        record.response_body = None
        record.resource_id = None
        record.completed_at = None
        record.expires_at = expires_at
        await self._session.flush()
        return record

    async def complete(
        self,
        record: IdempotencyKey,
        *,
        status_code: int,
        body: dict[str, Any],
        resource_id: uuid.UUID | None,
    ) -> None:
        """Store the outcome so a later retry can replay it verbatim."""
        record.status = IdempotencyStatus.COMPLETED
        record.response_status_code = status_code
        record.response_body = body
        record.resource_id = resource_id
        record.completed_at = datetime.now(UTC)
        await self._session.flush()

    async def delete_expired(self, *, limit: int = 1000) -> int:
        """Drop keys past their retention window."""
        subquery = (
            select(IdempotencyKey.id)
            .where(IdempotencyKey.expires_at <= datetime.now(UTC))
            .limit(limit)
            .scalar_subquery()
        )
        result = await self._session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.id.in_(subquery))
        )
        return int(result.rowcount) if isinstance(result, CursorResult) else 0
