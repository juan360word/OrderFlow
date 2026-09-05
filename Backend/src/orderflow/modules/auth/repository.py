"""Data access for the auth module.

The repository knows SQL; it does not know business rules. Two consequences:

* It never commits. The transaction boundary belongs to the request-scoped
  unit of work (``Database.session``), so that a service can compose several
  repository calls into one atomic operation.
* It returns ORM objects or ``None``; deciding that ``None`` means "401" is the
  service's job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from orderflow.modules.auth.models import RefreshToken, Role, User


class AuthRepository:
    """Queries over roles, users and refresh tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- roles ------------------------------------------------------------

    async def get_role_by_name(self, name: str) -> Role | None:
        result = await self._session.execute(select(Role).where(Role.name == name))
        return result.scalar_one_or_none()

    # -- users ------------------------------------------------------------

    async def get_user_by_email(self, email: str) -> User | None:
        """Look a user up by email.

        The comparison is case-insensitive because the column is CITEXT — the
        database does the folding, so we cannot get it wrong here and right
        elsewhere.
        """
        result = await self._session.execute(
            select(User).options(selectinload(User.role)).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(
            select(User).options(selectinload(User.role)).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        result = await self._session.execute(select(User.id).where(User.email == email).limit(1))
        return result.first() is not None

    def add_user(self, user: User) -> User:
        """Stage a new user. The flush/commit happens in the unit of work."""
        self._session.add(user)
        return user

    async def record_successful_login(self, user: User) -> None:
        """Reset the brute-force counters and stamp the login time."""
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = datetime.now(UTC)

    async def record_failed_login(self, user_id: uuid.UUID, *, lock_until: datetime | None) -> None:
        """Increment the failure counter atomically.

        Written as a single UPDATE with ``attempts = attempts + 1`` rather than
        read-modify-write in Python: concurrent failed logins from a credential
        stuffing script would otherwise overwrite each other's increments and
        the lockout would never trigger.
        """
        await self._session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                failed_login_attempts=User.failed_login_attempts + 1,
                locked_until=lock_until,
            )
        )

    # -- refresh tokens ---------------------------------------------------

    def add_refresh_token(self, token: RefreshToken) -> RefreshToken:
        self._session.add(token)
        return token

    async def get_refresh_token_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke_refresh_token(
        self, token: RefreshToken, *, replaced_by: uuid.UUID | None = None
    ) -> None:
        token.revoked_at = datetime.now(UTC)
        token.replaced_by_id = replaced_by

    async def revoke_all_user_tokens(self, user_id: uuid.UUID) -> int:
        """Kill every live session for a user.

        Used on logout-everywhere, on password change, and on refresh token
        reuse detection. Returns how many sessions were closed.
        """
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            ),
        )
        return result.rowcount

    async def count_active_tokens(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.now(UTC),
            )
        )
        return int(result.scalar_one())
