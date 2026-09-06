"""Redis-backed rate limiting.

A fixed window: one counter per identifier per window, incremented on each
request and expired when the window closes. Its known weakness is the boundary
- a caller can spend the full budget at the end of one window and again at the
start of the next, so the true short-term ceiling is twice the limit. A sliding
window log fixes that at the cost of storing a timestamp per request. For
slowing down credential stuffing, the fixed window is the right trade.

**Failure policy: this fails open.** If Redis is down, requests are allowed
rather than rejected. Failing closed would mean a cache outage locks every user
out of the product, turning a degraded dependency into a total one. That choice
is only defensible because login has a second, independent defence that does
not depend on Redis at all: the per-account lockout counter in PostgreSQL.
Remove that and this policy should be revisited.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request

from orderflow.core.cache import CacheClient, CacheUnavailableError
from orderflow.core.config import Settings
from orderflow.core.dependencies import SettingsDep
from orderflow.core.errors import RateLimitError
from orderflow.core.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Counts requests per identifier inside a fixed time window."""

    def __init__(self, cache: CacheClient) -> None:
        self._cache = cache

    async def check(self, *, bucket: str, identifier: str, limit: int, window_seconds: int) -> None:
        """Raise :class:`RateLimitError` once the caller exceeds ``limit``."""
        key = self._cache.key(f"ratelimit:{bucket}", self._hash(identifier))
        try:
            count, retry_after = await self._cache.incr_with_window(
                key, window_seconds=window_seconds
            )
        except CacheUnavailableError:
            logger.warning("rate_limit_skipped_cache_unavailable", bucket=bucket)
            return

        if count > limit:
            logger.warning(
                "rate_limit_exceeded",
                bucket=bucket,
                count=count,
                limit=limit,
            )
            raise RateLimitError(
                "Too many requests. Please wait before trying again.",
                details={"retry_after_seconds": max(retry_after, 1)},
            )

    @staticmethod
    def _hash(identifier: str) -> str:
        """Store a digest, not the raw identifier.

        The key would otherwise put a client IP - personal data under GDPR -
        in plain sight of anyone with Redis access, including in a memory dump.
        """
        return hashlib.sha256(identifier.encode()).hexdigest()[:32]


def client_identifier(request: Request) -> str:
    """Best-effort caller identity for rate limiting.

    ``X-Forwarded-For`` is only trusted because the deployment puts our own
    load balancer in front. Exposed directly, a client could forge it and reset
    its own counter at will, so this must never be the sole control.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_rate_limiter(request: Request) -> RateLimiter:
    cache: CacheClient = request.app.state.cache
    return RateLimiter(cache)


def rate_limit(
    bucket: str,
    *,
    limit: Callable[[Settings], int],
    window: Callable[[Settings], int],
) -> Callable[..., Awaitable[None]]:
    """Build a dependency that enforces one named limit.

    The limit is read from settings at request time rather than captured at
    import time, so it can be tuned per environment without a code change.
    """

    async def dependency(
        request: Request,
        limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
        settings: SettingsDep,
    ) -> None:
        await limiter.check(
            bucket=bucket,
            identifier=client_identifier(request),
            limit=limit(settings),
            window_seconds=window(settings),
        )

    return dependency
