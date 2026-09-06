"""Liveness and readiness probes.

Two endpoints, not one, because an orchestrator asks two different questions:

* **Liveness** — "is this process wedged and in need of a restart?" It must not
  touch dependencies: if PostgreSQL blips, restarting every API pod makes the
  outage worse, not better.
* **Readiness** — "can this instance serve traffic right now?" This one *does*
  check the database, so a pod with a broken connection pool is pulled out of
  the load balancer instead of returning 500s.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from orderflow.core.cache import CacheClient
from orderflow.core.database import Database
from orderflow.core.dependencies import SettingsDep, get_cache, get_database

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: Literal["ok"]
    environment: str
    version: str


class ReadinessResponse(BaseModel):
    """Readiness payload with per-dependency detail."""

    status: Literal["ready", "degraded"]
    checks: dict[str, bool]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    """Answer without touching any dependency."""
    return HealthResponse(
        status="ok",
        environment=settings.environment.value,
        version=settings.app_version,
    )


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(
    response: Response,
    database: Annotated[Database, Depends(get_database)],
    cache: Annotated[CacheClient, Depends(get_cache)],
) -> ReadinessResponse:
    """Verify every dependency needed to serve a request.

    Returns 503 when degraded so the check is meaningful to a load balancer
    that only looks at the status code.
    """
    checks = {"database": await database.check_connection()}

    if cache.available:
        checks["cache"] = await cache.ping()

    if not checks["database"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="degraded", checks=checks)

    return ReadinessResponse(status="ready", checks=checks)
