"""Shared test fixtures.

Design of the test database:

Integration tests run against a *real* PostgreSQL, never SQLite. The whole
point of phases 3-6 is behaviour that only PostgreSQL has — CHECK constraints,
CITEXT, ``SELECT ... FOR UPDATE``, native enums. A SQLite stand-in would make
the concurrency tests pass while proving nothing.

Isolation between tests is by truncation rather than by wrapping each test in a
rolled-back transaction. The nested-transaction trick is faster, but it makes
every test share one connection — and the concurrency tests need several real,
independent connections committing against each other. Correctness of the tests
that matter most wins over speed here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

os.environ.setdefault("ENVIRONMENT", "testing")

# The suite gets its own database, never the one `docker compose up` serves.
# Two reasons, both learned the hard way:
#
#   * `clean_database` TRUNCATEs every data table before each test. Pointed at
#     the development database, running the suite silently wipes the data you
#     were working with.
#   * The compose stack runs a relay and a worker. They poll the same outbox
#     the tests write to, so a containerised relay claims events out from
#     under the test's own relay - `FOR UPDATE SKIP LOCKED` doing exactly its
#     job - and the outbox tests fail perhaps one run in ten. CI never sees it:
#     there is no relay container there.
#
# This mirrors what the settings fixture already does for Redis with db index
# 15. An environment variable still wins, which is how CI names it.
os.environ.setdefault("POSTGRES_DB", "orderflow_test")

from orderflow.core.cache import CacheClient
from orderflow.core.config import Settings
from orderflow.core.database import Database
from orderflow.core.security import PasswordService
from orderflow.main import create_app
from orderflow.modules.auth.models import ROLE_ADMIN, ROLE_CUSTOMER, User
from orderflow.modules.inventory.models import Inventory
from orderflow.modules.orders.schemas import ShippingAddressInput
from orderflow.modules.products.models import Product

_TABLES_TO_TRUNCATE = (
    "processed_events",
    "outbox_events",
    "idempotency_keys",
    "order_items",
    "orders",
    "inventory_reservations",
    "inventory",
    "refresh_tokens",
    "products",
    "users",
)


def _test_backend() -> str:
    """Which backing services the suite runs against.

    ``external``       - PostgreSQL and Redis are already running (docker
                         compose locally, service containers in CI). Fast, and
                         the default for the inner development loop.
    ``testcontainers`` - the suite starts throwaway containers itself, so a
                         clean checkout needs nothing but Docker.

    Both run the same tests against real servers. Neither substitutes SQLite or
    a fake Redis: the behaviour under test - CHECK constraints, FOR UPDATE,
    CITEXT, Lua scripts - only exists in the real thing.
    """
    return os.environ.get("ORDERFLOW_TEST_BACKEND", "external").strip().lower()


@pytest.fixture(scope="session")
def backend_overrides() -> Iterator[dict[str, object]]:
    """Connection settings for the backing services, starting them if asked."""
    if _test_backend() != "testcontainers":
        yield {}
        return

    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.community.redis import RedisContainer

    with (
        PostgresContainer("postgres:17-alpine") as postgres,
        RedisContainer("redis:7-alpine") as redis_container,
    ):
        yield {
            "postgres_host": postgres.get_container_host_ip(),
            "postgres_port": int(postgres.get_exposed_port(5432)),
            "postgres_user": postgres.username,
            "postgres_password": SecretStr(postgres.password),
            "postgres_db": postgres.dbname,
            "redis_host": redis_container.get_container_host_ip(),
            "redis_port": int(redis_container.get_exposed_port(6379)),
            "redis_db": 0,
        }


@pytest.fixture(scope="session")
def settings(backend_overrides: dict[str, object]) -> Settings:
    """Test settings.

    Argon2 cost is dialled down to the library minimum: the suite hashes dozens
    of passwords and the production cost would add minutes per run. It is the
    one parameter deliberately not production-faithful, and it is scoped to
    ENVIRONMENT=testing.
    """
    base = Settings(
        environment="testing",  # type: ignore[arg-type]
        debug=False,
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
        redis_db=15,
        login_rate_limit_attempts=100,
        product_cache_ttl_seconds=60,
    )
    return base.model_copy(update=backend_overrides) if backend_overrides else base


@pytest.fixture(scope="session")
async def test_database_exists(settings: Settings) -> None:
    """Create the test database if it is not there yet.

    The compose file only creates the development database, so without this
    a fresh checkout would need a manual `createdb` before the suite could
    run. Connecting to the `postgres` maintenance database is the standard way
    in: you cannot create a database from inside itself.
    """
    if _test_backend() == "testcontainers":
        return  # the container creates its own database

    if "test" not in settings.postgres_db:
        pytest.fail(
            f"Refusing to run against {settings.postgres_db!r}: the suite "
            "truncates every table before each test, so it must point at a "
            "database whose name says it is disposable."
        )

    import asyncpg

    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database="postgres",
    )
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", settings.postgres_db
        )
        if not exists:
            # CREATE DATABASE cannot run inside a transaction, and the name
            # cannot be a bind parameter, so it is quoted as an identifier.
            await connection.execute(f'CREATE DATABASE "{settings.postgres_db}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def migrated_schema(settings: Settings, test_database_exists: None) -> None:
    """Bring the test database up to head before anything queries it.

    Running the real migrations rather than ``metadata.create_all`` is the
    point: it proves the migration chain produces the schema the models expect,
    extensions and seed rows included. A ``create_all`` suite passes happily
    while the migrations are broken.

    Alembic runs in a subprocess because ``migrations/env.py`` calls
    ``asyncio.run()``, which cannot start a loop from inside the one pytest is
    already running this fixture on.
    """
    environment = {
        **os.environ,
        "POSTGRES_HOST": settings.postgres_host,
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
        "POSTGRES_DB": settings.postgres_db,
        "ENVIRONMENT": "testing",
    }
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session")
async def database(settings: Settings, migrated_schema: None) -> AsyncIterator[Database]:
    db = Database(settings)
    if not await db.check_connection():
        pytest.fail(
            "PostgreSQL is not reachable. Run `docker compose up -d` and "
            "`uv run alembic upgrade head` before the test suite."
        )
    yield db
    await db.dispose()


@pytest.fixture
async def clean_database(database: Database) -> AsyncIterator[None]:
    """Empty the data tables before each test.

    Attached to every test except those marked ``unit`` (see
    ``pytest_collection_modifyitems``). ``roles`` is never truncated - it is
    reference data seeded by a migration, not test data.
    """
    async with database.engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE {', '.join(_TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )
    yield


@pytest.fixture(scope="session")
async def cache(settings: Settings) -> AsyncIterator[CacheClient]:
    """One Redis client for the suite, pinned to a throwaway database index."""
    client = CacheClient(settings)
    await client.connect()
    yield client
    await client.close()


@pytest.fixture
async def clean_cache(cache: CacheClient) -> AsyncIterator[None]:
    """Empty Redis between tests so a cached value cannot leak across them."""
    if cache.available:
        await cache.flush_test_database()
    yield


@pytest.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    """A session for tests that talk to the database directly."""
    async with database.session() as db_session:
        yield db_session


@pytest.fixture(scope="session")
async def app(settings: Settings, database: Database, cache: CacheClient) -> AsyncIterator[FastAPI]:
    """A fully started application instance.

    The session-scoped engine and Redis client are injected before startup, so
    the lifespan adopts them instead of building its own. Without that, every
    test would pay for a fresh connection pool and a Redis handshake, and the
    shutdown would close the connections the next test still needs.
    """
    instance = create_app(settings)
    instance.state.database = database
    instance.state.cache = cache
    async with instance.router.lifespan_context(instance):
        yield instance


@pytest.fixture
def reset_app_state(app: FastAPI, settings: Settings) -> Iterator[None]:
    """Undo per-test tampering with the shared app.

    A test may swap in different settings or override a dependency; restoring
    both afterwards is what keeps one session-scoped app safe to share.
    """
    yield
    app.state.settings = settings
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the ASGI app in-process.

    ``ASGITransport`` skips the network entirely: no port, no server, no
    flakiness, while still exercising the full middleware and routing stack.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="session")
def password_service(settings: Settings) -> PasswordService:
    return PasswordService(settings)


@pytest.fixture
def make_user(session: AsyncSession, password_service: PasswordService) -> Callable[..., object]:
    """Factory creating a persisted user with a known password."""

    async def _make_user(
        email: str = "customer@example.com",
        role: str = ROLE_CUSTOMER,
        password: str = TEST_PASSWORD,
        *,
        is_active: bool = True,
    ) -> User:
        role_id = (
            await session.execute(text("SELECT id FROM roles WHERE name = :name"), {"name": role})
        ).scalar_one()
        user = User(
            email=email,
            password_hash=password_service.hash(password),
            full_name="Test User",
            role_id=role_id,
            is_active=is_active,
        )
        session.add(user)
        await session.flush()
        await session.commit()
        await session.refresh(user, attribute_names=["role"])
        return user

    return _make_user


# A valid delivery address, for the many tests whose subject is something else
# entirely. Placing an order requires one, so every order payload needs it;
# spelling it out in each test would bury what that test is actually about.
SHIPPING_ADDRESS: dict[str, str] = {
    "recipient_name": "Ana Test",
    "phone": "+57 300 123 4567",
    "line1": "Calle 123 #45-67",
    "city": "Bogotá",
    "region": "Cundinamarca",
    "country": "CO",
}


#: The same address as a validated model, for tests that call the service
#: directly instead of going through HTTP.
SHIPPING_INPUT = ShippingAddressInput(**SHIPPING_ADDRESS)


def order_payload(product_id: object, quantity: int = 1) -> dict[str, object]:
    """The body of a one-line order, address included."""
    return {
        "items": [{"product_id": str(product_id), "quantity": quantity}],
        "shipping_address": SHIPPING_ADDRESS,
    }


@pytest.fixture
def make_product(session: AsyncSession) -> Callable[..., object]:
    """Factory creating a product together with its inventory row."""

    async def _make_product(
        sku: str = "TEST-SKU-001",
        stock: int = 10,
        price: str = "19.99",
        *,
        is_active: bool = True,
    ) -> Product:
        product = Product(
            sku=sku,
            name=f"Product {sku}",
            description="A test product",
            price=Decimal(price),
            is_active=is_active,
        )
        session.add(product)
        await session.flush()
        session.add(Inventory(product_id=product.id, quantity_available=stock))
        await session.commit()
        return product

    return _make_product


@pytest.fixture
async def auth_headers(client: AsyncClient, make_user: Callable[..., object]) -> dict[str, str]:
    """Authorization header for a plain customer."""
    await make_user(email="customer@example.com", role=ROLE_CUSTOMER)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "customer@example.com", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def admin_headers(client: AsyncClient, make_user: Callable[..., object]) -> dict[str, str]:
    """Authorization header for an administrator."""
    await make_user(email="admin@example.com", role=ROLE_ADMIN)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


_BACKEND_FIXTURES = ("clean_database", "clean_cache", "reset_app_state")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Attach the backing-service fixtures to everything except unit tests.

    They cannot simply be ``autouse``: that would drag PostgreSQL, Redis and a
    built application into the setup of tests that assert on pure logic, and
    the pyramid's bottom layer would silently stop being runnable without
    Docker. Deciding per item at collection time is what keeps
    ``pytest -m unit`` honest.
    """
    for item in items:
        if item.get_closest_marker("unit"):
            continue
        for name in _BACKEND_FIXTURES:
            if name not in item.fixturenames:
                item.fixturenames.insert(0, name)
