"""Tests for the demo-account seed (orderflow.seed)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Environment, Settings
from orderflow.core.security import PasswordService
from orderflow.modules.auth.models import ROLE_ADMIN, ROLE_CUSTOMER
from orderflow.modules.auth.repository import AuthRepository
from orderflow.seed import SeedAccount, run, seed_account

pytestmark = pytest.mark.integration

ADMIN = SeedAccount(
    email="admin@orderflow.test",
    password="Segura2024!XZ",
    full_name="Admin OrderFlow",
    role=ROLE_ADMIN,
)


class TestSeedAccount:
    async def test_it_creates_an_administrator(
        self, session: AsyncSession, password_service: PasswordService
    ) -> None:
        """The only route to an admin: registration always yields a customer."""
        assert await seed_account(session, password_service, ADMIN) is True

        user = await AuthRepository(session).get_user_by_email(ADMIN.email)
        assert user is not None
        assert user.role.name == ROLE_ADMIN
        assert user.is_active is True

    async def test_the_password_is_stored_hashed(
        self, session: AsyncSession, password_service: PasswordService
    ) -> None:
        """A seeded account must be no weaker than a registered one."""
        await seed_account(session, password_service, ADMIN)
        user = await AuthRepository(session).get_user_by_email(ADMIN.email)

        assert user is not None
        assert ADMIN.password not in user.password_hash
        assert password_service.verify(ADMIN.password, user.password_hash)

    async def test_running_it_twice_creates_one_account(
        self, session: AsyncSession, password_service: PasswordService
    ) -> None:
        """`make seed` has to be safe to repeat, or it is a trap."""
        assert await seed_account(session, password_service, ADMIN) is True
        assert await seed_account(session, password_service, ADMIN) is False

    async def test_an_existing_account_keeps_its_password(
        self, session: AsyncSession, password_service: PasswordService
    ) -> None:
        """Re-seeding must not silently reset a password someone changed."""
        await seed_account(session, password_service, ADMIN)
        repo = AuthRepository(session)
        original = (await repo.get_user_by_email(ADMIN.email)).password_hash  # type: ignore[union-attr]

        await seed_account(
            session,
            password_service,
            SeedAccount(
                email=ADMIN.email,
                password="OtraClaveDistinta77",
                full_name="Otro Nombre",
                role=ADMIN.role,
            ),
        )

        assert (await repo.get_user_by_email(ADMIN.email)).password_hash == original  # type: ignore[union-attr]

    async def test_a_customer_gets_the_customer_role(
        self, session: AsyncSession, password_service: PasswordService
    ) -> None:
        account = SeedAccount(
            email="cliente@orderflow.test",
            password="Segura2024!XZ",
            full_name="Cliente Demo",
            role=ROLE_CUSTOMER,
        )
        await seed_account(session, password_service, account)

        user = await AuthRepository(session).get_user_by_email(account.email)
        assert user is not None
        assert user.role.name == ROLE_CUSTOMER


class TestProductionGuard:
    @pytest.mark.parametrize("environment", [Environment.PRODUCTION, Environment.STAGING])
    async def test_it_refuses_to_seed_a_production_like_environment(
        self, settings: Settings, environment: Environment
    ) -> None:
        """These credentials are printed on the login screen. They are demo data."""
        with pytest.raises(SystemExit, match="Refusing to seed"):
            await run(settings.model_copy(update={"environment": environment}))
