"""Idempotency rules.

Networks deliver *at least once*. A client that times out cannot tell whether
the server never saw the request or processed it and lost the reply on the way
back, so the only safe thing it can do is retry. Without protection that retry
places a second order and charges the customer twice.

An idempotency key lets the client say "this is the same logical operation as
before". The server records the key together with the response it produced, and
answers a repeat with the stored response instead of doing the work again.

Note what makes this necessary at all: creating an order is *not* naturally
idempotent. ``GET /products/1`` can be repeated forever and ``DELETE`` twice
leaves the same state, so neither needs a key. ``POST /orders`` creates new
state on every call, so the caller has to supply the identity that ties the
retries together.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Settings
from orderflow.core.errors import ConflictError, ValidationError
from orderflow.core.logging import get_logger
from orderflow.modules.idempotency.models import (
    MAX_KEY_LENGTH,
    MIN_KEY_LENGTH,
    IdempotencyKey,
    IdempotencyStatus,
)
from orderflow.modules.idempotency.repository import IdempotencyRepository

logger = get_logger(__name__)

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]+$")


class IdempotencyKeyReuseError(ValidationError):
    """The key was already used for a different request body."""

    code = "idempotency_key_reuse"
    message = "This Idempotency-Key was already used with a different request payload."


class IdempotencyConflictError(ConflictError):
    """An identical request with this key is still being processed."""

    code = "idempotency_request_in_flight"
    message = "A request with this Idempotency-Key is still in progress. Retry shortly."


@dataclass(slots=True)
class IdempotencySlot:
    """The outcome of claiming a key.

    ``replayed`` is the branch the caller cares about: when true, the work has
    already been done and ``status_code``/``body`` hold the original answer.
    """

    record: IdempotencyKey | None
    replayed: bool = False
    status_code: int | None = None
    body: dict[str, Any] | None = None

    @property
    def is_tracked(self) -> bool:
        return self.record is not None


class IdempotencyService:
    """Claim keys, replay stored responses, and record new ones."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = IdempotencyRepository(session)

    async def claim(
        self,
        *,
        key: str | None,
        user_id: uuid.UUID,
        method: str,
        path: str,
        payload: Any,
    ) -> IdempotencySlot:
        """Reserve a key for this request, or hand back the previous response.

        The claim happens inside the request's transaction, alongside the work
        it protects. That is deliberate: if the order fails, the key row is
        rolled back with it and the client is free to retry. A key that
        survived a failure would make a transient error permanent.
        """
        if key is None:
            return IdempotencySlot(record=None)

        self._validate_key(key)
        fingerprint = self._fingerprint(method, path, payload)
        expires_at = datetime.now(UTC) + timedelta(hours=self._settings.idempotency_retention_hours)

        claimed = await self._repo.claim(
            user_id=user_id,
            key=key,
            method=method,
            path=path,
            fingerprint=fingerprint,
            expires_at=expires_at,
        )
        if claimed is not None:
            return IdempotencySlot(record=claimed)

        existing = await self._repo.get_for_update(user_id=user_id, key=key)
        if existing is None:
            raise IdempotencyConflictError()

        if existing.expires_at <= datetime.now(UTC):
            logger.info("idempotency_key_recycled", key_id=str(existing.id))
            recycled = await self._repo.take_over_expired(
                existing, fingerprint=fingerprint, expires_at=expires_at
            )
            return IdempotencySlot(record=recycled)

        if existing.request_fingerprint != fingerprint:
            logger.warning(
                "idempotency_key_reused_with_different_payload",
                user_id=str(user_id),
                key_id=str(existing.id),
            )
            raise IdempotencyKeyReuseError()

        if existing.status is not IdempotencyStatus.COMPLETED:
            raise IdempotencyConflictError()

        logger.info(
            "idempotent_replay",
            user_id=str(user_id),
            key_id=str(existing.id),
            resource_id=str(existing.resource_id) if existing.resource_id else None,
        )
        return IdempotencySlot(
            record=existing,
            replayed=True,
            status_code=existing.response_status_code,
            body=existing.response_body,
        )

    async def record(
        self,
        slot: IdempotencySlot,
        *,
        status_code: int,
        body: dict[str, Any],
        resource_id: uuid.UUID | None = None,
    ) -> None:
        """Attach the produced response to the claimed key. A no-op when untracked."""
        if slot.record is None or slot.replayed:
            return
        await self._repo.complete(
            slot.record, status_code=status_code, body=body, resource_id=resource_id
        )

    async def purge_expired(self, *, limit: int = 1000) -> int:
        """Delete keys past their retention window.

        The table would otherwise grow without bound. Retention has to outlive
        any client's realistic retry window - hours, not minutes - because a
        key deleted too early stops protecting the retry it exists for.
        """
        removed = await self._repo.delete_expired(limit=limit)
        if removed:
            logger.info("idempotency_keys_purged", count=removed)
        return removed

    @staticmethod
    def _validate_key(key: str) -> None:
        if not MIN_KEY_LENGTH <= len(key) <= MAX_KEY_LENGTH:
            raise ValidationError(
                f"Idempotency-Key must be between {MIN_KEY_LENGTH} and {MAX_KEY_LENGTH} characters."
            )
        if not _KEY_PATTERN.match(key):
            raise ValidationError(
                "Idempotency-Key may only contain letters, digits, '.', '_', ':' and '-'."
            )

    @staticmethod
    def _fingerprint(method: str, path: str, payload: Any) -> str:
        """Hash the request so a reused key with a changed body is detectable.

        Without this check, a client could send a key with a 1-unit basket, then
        reuse it with a 100-unit basket and receive the first response - quietly
        losing the second order.
        """
        canonical = json.dumps(
            {"method": method, "path": path, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = [
    "IdempotencyConflictError",
    "IdempotencyKeyReuseError",
    "IdempotencyService",
    "IdempotencySlot",
]
