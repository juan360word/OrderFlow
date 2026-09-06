"""Redis cache and rate limiting tests (phase 9)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from orderflow.core.cache import CacheClient, CacheUnavailableError
from orderflow.core.config import Settings
from orderflow.core.errors import RateLimitError
from orderflow.core.rate_limit import RateLimiter

pytestmark = pytest.mark.integration

PRODUCTS = "/api/v1/products"


class TestCacheClient:
    async def test_round_trip(self, cache: CacheClient) -> None:
        key = cache.key("test", "round-trip")
        await cache.set_json(key, {"value": 42}, ttl_seconds=60)

        assert await cache.get_json(key) == {"value": 42}

    async def test_a_missing_key_is_none(self, cache: CacheClient) -> None:
        assert await cache.get_json(cache.key("test", "absent")) is None

    async def test_delete_removes_the_entry(self, cache: CacheClient) -> None:
        key = cache.key("test", "deletable")
        await cache.set_json(key, {"value": 1}, ttl_seconds=60)

        await cache.delete(key)

        assert await cache.get_json(key) is None

    async def test_keys_are_namespaced_and_versioned(self, cache: CacheClient) -> None:
        assert cache.key("product", "abc") == "orderflow:v1:product:abc"

    async def test_corrupt_payloads_are_discarded(self, cache: CacheClient) -> None:
        """A bad value must not turn a read into a 500."""
        key = cache.key("test", "corrupt")
        await cache._client.set(key, "{not json")  # type: ignore[union-attr]

        assert await cache.get_json(key) is None

    async def test_the_rebuild_lock_admits_one_caller(self, cache: CacheClient) -> None:
        key = cache.key("test", "stampede")

        first = await cache.acquire_rebuild_lock(key)
        second = await cache.acquire_rebuild_lock(key)

        assert first is True
        assert second is False

    async def test_a_disconnected_client_degrades_to_a_miss(self, settings: Settings) -> None:
        """Redis being down must never break a request."""
        offline = CacheClient(settings.model_copy(update={"redis_port": 6399}))
        await offline.connect()

        assert offline.available is False
        assert await offline.get_json("anything") is None
        await offline.set_json("anything", {"a": 1}, ttl_seconds=10)
        await offline.delete("anything")

    async def test_a_disconnected_client_cannot_rate_limit(self, settings: Settings) -> None:
        offline = CacheClient(settings.model_copy(update={"redis_port": 6399}))
        await offline.connect()

        with pytest.raises(CacheUnavailableError):
            await offline.incr_with_window("k", window_seconds=60)


class TestProductCache:
    async def test_a_second_read_is_served_from_cache(
        self, client: AsyncClient, cache: CacheClient, make_product: Callable[..., object]
    ) -> None:
        product = await make_product(sku="CACHE-001", price="19.99")

        first = await client.get(f"{PRODUCTS}/{product.id}")
        cached = await cache.get_json(cache.key("product", str(product.id)))
        second = await client.get(f"{PRODUCTS}/{product.id}")

        assert first.status_code == 200
        assert cached is not None
        assert second.json() == first.json()

    async def test_an_update_invalidates_the_entry(
        self,
        client: AsyncClient,
        cache: CacheClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """Without invalidation the API would keep serving the old price."""
        product = await make_product(sku="CACHE-002", price="10.00")
        await client.get(f"{PRODUCTS}/{product.id}")

        await client.patch(
            f"{PRODUCTS}/{product.id}", headers=admin_headers, json={"price": "25.00"}
        )

        assert await cache.get_json(cache.key("product", str(product.id))) is None
        assert (await client.get(f"{PRODUCTS}/{product.id}")).json()["price"] == "25.00"

    async def test_a_deactivation_invalidates_the_entry(
        self,
        client: AsyncClient,
        cache: CacheClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        product = await make_product(sku="CACHE-003")
        await client.get(f"{PRODUCTS}/{product.id}")

        await client.delete(f"{PRODUCTS}/{product.id}", headers=admin_headers)

        assert await cache.get_json(cache.key("product", str(product.id))) is None
        assert (await client.get(f"{PRODUCTS}/{product.id}")).status_code == 404

    async def test_stock_is_never_cached(
        self,
        client: AsyncClient,
        cache: CacheClient,
        admin_headers: dict[str, str],
        make_product: Callable[..., object],
    ) -> None:
        """Availability changes on every purchase; a stale count would oversell."""
        product = await make_product(sku="CACHE-004", stock=5)
        await client.get(f"/api/v1/inventory/{product.id}")

        assert await cache.get_json(cache.key("inventory", str(product.id))) is None

        await client.post(
            f"/api/v1/inventory/{product.id}/adjust",
            headers=admin_headers,
            json={"delta": 10, "reason": "restock"},
        )
        assert (await client.get(f"/api/v1/inventory/{product.id}")).json()[
            "quantity_available"
        ] == 15


class TestRateLimiter:
    async def test_requests_under_the_limit_pass(self, cache: CacheClient) -> None:
        limiter = RateLimiter(cache)

        for _ in range(3):
            await limiter.check(bucket="test", identifier="1.2.3.4", limit=3, window_seconds=60)

    async def test_the_limit_is_enforced(self, cache: CacheClient) -> None:
        limiter = RateLimiter(cache)
        for _ in range(3):
            await limiter.check(bucket="test", identifier="5.6.7.8", limit=3, window_seconds=60)

        with pytest.raises(RateLimitError) as exc_info:
            await limiter.check(bucket="test", identifier="5.6.7.8", limit=3, window_seconds=60)

        assert exc_info.value.details["retry_after_seconds"] >= 1

    async def test_counters_are_isolated_per_identifier(self, cache: CacheClient) -> None:
        limiter = RateLimiter(cache)
        for _ in range(3):
            await limiter.check(bucket="test", identifier="9.9.9.9", limit=3, window_seconds=60)

        await limiter.check(bucket="test", identifier="8.8.8.8", limit=3, window_seconds=60)

    async def test_counters_are_isolated_per_bucket(self, cache: CacheClient) -> None:
        limiter = RateLimiter(cache)
        for _ in range(3):
            await limiter.check(bucket="a", identifier="7.7.7.7", limit=3, window_seconds=60)

        await limiter.check(bucket="b", identifier="7.7.7.7", limit=3, window_seconds=60)

    async def test_the_identifier_is_not_stored_in_the_clear(self, cache: CacheClient) -> None:
        """A client IP is personal data; the key holds a digest of it."""
        limiter = RateLimiter(cache)
        await limiter.check(bucket="test", identifier="203.0.113.7", limit=3, window_seconds=60)

        keys = [key async for key in cache._client.scan_iter("*ratelimit*")]  # type: ignore[union-attr]

        assert keys
        assert all("203.0.113.7" not in key for key in keys)

    async def test_it_fails_open_when_redis_is_down(self, settings: Settings) -> None:
        """A cache outage must not lock every user out of the product."""
        offline = CacheClient(settings.model_copy(update={"redis_port": 6399}))
        await offline.connect()
        limiter = RateLimiter(offline)

        for _ in range(50):
            await limiter.check(bucket="test", identifier="1.1.1.1", limit=1, window_seconds=60)


class TestLoginRateLimit:
    async def test_repeated_logins_are_throttled(
        self, app: FastAPI, client: AsyncClient, settings: Settings
    ) -> None:
        """Brute forcing the login endpoint must hit a wall, not the database."""
        app.state.settings = settings.model_copy(update={"login_rate_limit_attempts": 3})

        statuses = [
            (
                await client.post(
                    "/api/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "whatever-long-enough"},
                )
            ).status_code
            for _ in range(5)
        ]

        assert statuses.count(401) == 3
        assert statuses.count(429) == 2

    async def test_a_throttled_response_says_when_to_retry(
        self, app: FastAPI, client: AsyncClient, settings: Settings
    ) -> None:
        app.state.settings = settings.model_copy(update={"login_rate_limit_attempts": 1})

        await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "whatever-long-enough"},
        )
        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "whatever-long-enough"},
        )

        assert blocked.status_code == 429
        assert blocked.json()["errors"]["retry_after_seconds"] >= 1
