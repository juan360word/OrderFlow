"""Health endpoint tests (phase 2)."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.api


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "testing"


async def test_health_does_not_leak_internals(client: AsyncClient) -> None:
    """The probe must not expose configuration or dependency detail."""
    body = await client.get("/health")

    assert set(body.json()) == {"status", "environment", "version"}


async def test_readiness_reports_every_dependency(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] is True
    assert body["checks"]["cache"] is True


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.headers["X-Request-ID"]


async def test_inbound_request_id_is_propagated(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})

    assert response.headers["X-Request-ID"] == "trace-abc-123"


async def test_malicious_request_id_is_replaced(client: AsyncClient) -> None:
    """A header-injection attempt must not be echoed back."""
    response = await client.get("/health", headers={"X-Request-ID": "abc\ninjected: 1"})

    assert response.headers["X-Request-ID"] != "abc\ninjected: 1"
    assert "injected" not in response.headers


async def test_security_headers_are_present(client: AsyncClient) -> None:
    headers = (await client.get("/health")).headers

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


async def test_unknown_route_returns_problem_json(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "not_found"
