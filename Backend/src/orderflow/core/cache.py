"""Redis cache and rate limiting.

Cache-aside, step by step, on a read:

  1. Ask Redis for the key.
  2. Hit  -> return it. PostgreSQL is never touched.
  3. Miss -> read PostgreSQL, write the value to Redis with a TTL, return it.

The application owns the cache; Redis is not a write-through layer that knows
about the database. That is what "aside" means, and it is why invalidation is
our job.

Three hazards this module addresses:

* **Staleness.** A cached copy keeps serving the old value after an update.
  Mitigated by an explicit delete on every write, plus a TTL as the backstop
  for the invalidation we forget.
* **Stampede.** A popular key expires and every concurrent reader misses at
  once, so the traffic Redis was absorbing lands on PostgreSQL in one spike.
  Mitigated by TTL jitter, so keys written together do not expire together,
  and by a short single-flight lock so only one reader rebuilds the value.
* **Unavailability.** Redis is an optimisation, not a source of truth. Every
  operation degrades to a miss when it fails, and the request still succeeds.
"""

from __future__ import annotations

import json
import random
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, Final, TypeVar

import redis.asyncio as redis
from redis.exceptions import RedisError

from orderflow.core.config import Environment, Settings
from orderflow.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

CACHE_VERSION: Final = "v1"
_LOCK_SUFFIX: Final = ":lock"

RATE_LIMIT_SCRIPT: Final = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


class CacheUnavailableError(RuntimeError):
    """Raised internally when Redis cannot answer; never surfaced to a client."""


class CacheClient:
    """Thin, failure-tolerant wrapper over Redis."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = settings.redis_enabled
        self._client: redis.Redis | None = None
        self._rate_limit_script: Any = None

    async def connect(self) -> None:
        """Open the connection pool. Never fatal: the app runs without cache."""
        if not self._enabled:
            logger.info("cache_disabled")
            return
        try:
            self._client = redis.from_url(
                self._settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=self._settings.redis_timeout_seconds,
                socket_timeout=self._settings.redis_timeout_seconds,
                max_connections=self._settings.redis_max_connections,
                health_check_interval=30,
            )
            await self._client.ping()
            self._rate_limit_script = self._client.register_script(RATE_LIMIT_SCRIPT)
            logger.info("cache_connected")
        except (RedisError, OSError) as exc:
            logger.error("cache_connection_failed", error_type=type(exc).__name__)
            self._client = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("cache_disconnected")

    @property
    def available(self) -> bool:
        return self._client is not None

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except (RedisError, OSError):
            return False

    def key(self, namespace: str, identifier: str) -> str:
        """Namespaced, versioned key.

        The version prefix turns a breaking change in the cached shape into a
        one-line deploy: bump it and every old entry is orphaned and expires on
        its own, instead of being deserialised into the new model and crashing.
        """
        return f"orderflow:{CACHE_VERSION}:{namespace}:{identifier}"

    async def get_json(self, key: str) -> Any | None:
        if self._client is None:
            return None
        try:
            raw = await self._client.get(key)
        except (RedisError, OSError) as exc:
            logger.warning("cache_get_failed", error_type=type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("cache_payload_corrupt", cache_key=key)
            await self.delete(key)
            return None

    async def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        if self._client is None:
            return
        try:
            await self._client.set(
                key, json.dumps(value, default=str), ex=self._jitter(ttl_seconds)
            )
        except (RedisError, OSError, TypeError) as exc:
            logger.warning("cache_set_failed", error_type=type(exc).__name__)

    async def delete(self, *keys: str) -> None:
        if self._client is None or not keys:
            return
        try:
            await self._client.delete(*keys)
        except (RedisError, OSError) as exc:
            logger.warning("cache_delete_failed", error_type=type(exc).__name__)

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        *,
        ttl_seconds: int,
        serialize: Callable[[T], Any],
    ) -> T:
        """Cache-aside with single-flight protection.

        On a miss, one caller takes a short lock and rebuilds the value; the
        others skip the lock and read the database directly rather than queue
        behind it. That trades a few duplicate reads for never blocking a
        request on a lock, while still cutting a stampede down from every
        concurrent reader to a handful.
        """
        cached = await self.get_json(key)
        if cached is not None:
            logger.debug("cache_hit", cache_key=key)
            return cached  # type: ignore[no-any-return]

        logger.debug("cache_miss", cache_key=key)
        value = await loader()
        if value is None:
            return value

        if await self.acquire_rebuild_lock(key):
            await self.set_json(key, serialize(value), ttl_seconds=ttl_seconds)
        return value

    async def acquire_rebuild_lock(self, key: str) -> bool:
        """Best-effort single-flight token. Absent Redis means "go ahead"."""
        if self._client is None:
            return False
        try:
            acquired = await self._client.set(
                key + _LOCK_SUFFIX,
                secrets.token_hex(8),
                nx=True,
                px=self._settings.cache_rebuild_lock_ms,
            )
        except (RedisError, OSError):
            return False
        return bool(acquired)

    async def incr_with_window(self, key: str, *, window_seconds: int) -> tuple[int, int]:
        """Atomically increment a counter and return ``(count, seconds_left)``.

        INCR and EXPIRE run inside one Lua script, which Redis executes
        atomically. Issued as two round trips they are not atomic: a crash
        between them leaves a counter with no expiry, and that key blocks the
        caller forever.
        """
        if self._client is None or self._rate_limit_script is None:
            raise CacheUnavailableError
        try:
            count, ttl = await self._rate_limit_script(keys=[key], args=[window_seconds])
        except (RedisError, OSError) as exc:
            raise CacheUnavailableError from exc
        return int(count), int(ttl)

    async def flush_test_database(self) -> None:
        """Wipe the selected Redis database.

        Guarded on the environment rather than on the database index: the index
        is a convention, ENVIRONMENT=testing is the actual statement that this
        Redis is disposable.
        """
        if self._client is None:
            return
        if self._settings.environment is not Environment.TESTING:
            raise RuntimeError(
                f"Refusing to flush Redis outside a testing environment "
                f"(environment={self._settings.environment.value})."
            )
        await self._client.flushdb()

    def _jitter(self, ttl_seconds: int) -> int:
        """Spread expiry times so keys written together do not die together."""
        spread = int(ttl_seconds * self._settings.cache_ttl_jitter_ratio)
        if spread <= 0:
            return ttl_seconds
        return max(1, ttl_seconds + random.randint(-spread, spread))  # noqa: S311
