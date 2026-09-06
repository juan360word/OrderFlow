"""Dependency wiring for the idempotency module."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from orderflow.core.dependencies import DbSession, SettingsDep
from orderflow.modules.idempotency.service import IdempotencyService


def get_idempotency_service(session: DbSession, settings: SettingsDep) -> IdempotencyService:
    """Shares the request session so the key is claimed in the same transaction."""
    return IdempotencyService(session, settings)


IdempotencyServiceDep = Annotated[IdempotencyService, Depends(get_idempotency_service)]

IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Client-generated unique value that makes a retry safe.",
        max_length=255,
    ),
]
