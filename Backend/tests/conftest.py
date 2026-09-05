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
from collections.abc import AsyncIterator, Callable
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Must be set before Settings is constructed anywhere.
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("POSTGRES_DB", os.environ.get("POSTGRES_DB", "orderflow"))

from orderflow.core.config import Settings
from orderflow.core.database import Database
from orderflow.core.dependencies import get_db_session
from orderflow.core.security import PasswordService
from orderflow.main import create_app
from orderflow.modules.auth.models import ROLE_ADMIN, ROLE_CUSTOMER, User
from orderflow.modules.inventory.models import Inventory
from orderflow.modules.products.models import Product

# Tables emptied between tests, children before parents so foreign keys hold.
_TABLES_TO_TRUNCATE = (
    "inventory_reservations",
    "inventory",
    "refresh_tokens",
    "products",
    "users",
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Test settings.

    Argon2 cost is dialled down to the library minimum: tests hash dozens of
    passwords and the production cost would add minutes per run. This is the
    one parameter that is deliberately *not* production-faithful, and it is
    safe because it is scoped to ENVIRONMENT=testing.
    """
    return Settings(
        environment="testing",  # type: ignore[arg-type]
        debug=False,
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )


@pytest.fixture(scope="session")
async def database(settings: Settings) -> AsyncIterator[Database]:
    db = Database(settings)
    # Fail loudly and early if the developer forgot `docker compose up`.
    if not await db.check_connection():
        pytest.fail(
            "PostgreSQL is not reachable. Run `docker compose up -d` and "
            "`uv run alembic upgrade head` before the test suite."
        )
    yield db
    await db.dispose()


@pytest.fixture(autouse=True)
async def clean_database(database: Database) -> AsyncIterator[None]:
    """Empty the data tables before each test.

    ``roles`` is excluded: it is seeded by a migration and is reference data,
    not test data. RESTART IDENTITY keeps sequences predictable.
    """
    async with database.engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE {', '.join(_TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )
    yield


@pytest.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    """A session for tests that talk to the database directly."""
    async with database.session() as db_session:
        yield db_session


@pytest.fixture
async def client(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the ASGI app in-process.

    ``ASGITransport`` skips the network entirely: no port, no server, no
    flakiness — while still exercising the full middleware and routing stack.
    """
    app = create_app(settings)
    app.state.database = database

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with database.session() as db_session:
            yield db_session

    app.dependency_overrides[get_db_session] = override_get_db

    async with app.router.lifespan_context(app):
        app.state.database = database
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Data factories
# ---------------------------------------------------------------------------

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
