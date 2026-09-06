"""Create the demo accounts the login screen advertises.

Registration through the API always produces a customer - the role is fixed in
``AuthService.register`` so a client cannot ask for its own privileges. That
leaves no way to obtain an administrator, which is what this script is for.

It is deliberately a separate entry point rather than a migration. A migration
describes the *schema*, and the schema is identical in every environment;
demo users are data, and data that only belongs in a development database has
no business in the migration chain that also runs against production.

Run it with ``make seed`` (or ``uv run python -m orderflow.seed``). It is safe
to run repeatedly: an account that already exists is left untouched.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Settings, get_settings
from orderflow.core.database import Database
from orderflow.core.logging import configure_logging, get_logger
from orderflow.core.security import PasswordService
from orderflow.modules.auth.models import ROLE_ADMIN, ROLE_CUSTOMER, User
from orderflow.modules.auth.repository import AuthRepository

logger = get_logger(__name__)

# Defaults match the accounts the login screen offers. They are overridable by
# environment variable so that a shared demo deployment can use its own
# credentials without editing code.
DEFAULT_PASSWORD = "Segura2024!XZ"  # noqa: S105 - demo credential, never production


@dataclass(frozen=True)
class SeedAccount:
    email: str
    password: str
    full_name: str
    role: str

    @classmethod
    def from_env(cls, prefix: str, *, email: str, full_name: str, role: str) -> SeedAccount:
        return cls(
            email=os.environ.get(f"{prefix}_EMAIL", email),
            password=os.environ.get(f"{prefix}_PASSWORD", DEFAULT_PASSWORD),
            full_name=os.environ.get(f"{prefix}_NAME", full_name),
            role=role,
        )


def _accounts() -> list[SeedAccount]:
    return [
        SeedAccount.from_env(
            "SEED_ADMIN", email="admin@orderflow.io", full_name="Admin OrderFlow", role=ROLE_ADMIN
        ),
        SeedAccount.from_env(
            "SEED_CUSTOMER",
            email="cliente@orderflow.io",
            full_name="Cliente Demo",
            role=ROLE_CUSTOMER,
        ),
    ]


async def seed_account(
    session: AsyncSession, passwords: PasswordService, account: SeedAccount
) -> bool:
    """Create one account. Returns True when it was created, False if present."""
    repo = AuthRepository(session)

    if await repo.email_exists(account.email):
        logger.info("seed_account_exists", email=account.email, role=account.role)
        return False

    role = await repo.get_role_by_name(account.role)
    if role is None:
        raise RuntimeError(
            f"Role {account.role!r} is missing. Run `alembic upgrade head` first: "
            "the roles are seeded by a migration."
        )

    repo.add_user(
        User(
            email=account.email,
            password_hash=passwords.hash(account.password),
            full_name=account.full_name,
            role_id=role.id,
        )
    )
    await session.flush()
    logger.info("seed_account_created", email=account.email, role=account.role)
    return True


async def run(settings: Settings) -> int:
    """Seed every demo account. Returns how many were created."""
    if settings.environment.is_production_like:
        raise SystemExit(
            f"Refusing to seed demo accounts in {settings.environment.value}. "
            "These credentials are published in the login screen."
        )

    database = Database(settings)
    passwords = PasswordService(settings)
    created = 0
    try:
        async with database.session() as session:
            for account in _accounts():
                if await seed_account(session, passwords, account):
                    created += 1
    finally:
        await database.dispose()
    return created


async def main() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    configure_logging(debug=settings.debug)
    created = await run(settings)
    print(f"Seed complete: {created} account(s) created.")  # noqa: T201 - CLI output


if __name__ == "__main__":  # pragma: no cover
    try:
        asyncio.run(main())
    except SystemExit as exc:
        print(exc, file=sys.stderr)  # noqa: T201 - CLI output
        raise
