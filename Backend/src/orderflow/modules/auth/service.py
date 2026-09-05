"""Auth business rules — the public door of the auth module.

Any other module that needs "who is this user" calls this service. Nothing
outside this package touches ``AuthRepository`` or the ``users`` table.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.config import Settings
from orderflow.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from orderflow.core.logging import get_logger
from orderflow.core.security import PasswordService, TokenService, TokenType, hash_token
from orderflow.modules.auth.models import ROLE_CUSTOMER, RefreshToken, User
from orderflow.modules.auth.repository import AuthRepository
from orderflow.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenPair,
)

logger = get_logger(__name__)

MAX_FAILED_LOGIN_ATTEMPTS: Final = 5
LOCKOUT_DURATION: Final = timedelta(minutes=15)
MAX_ACTIVE_SESSIONS: Final = 10


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Audit metadata attached to an issued session."""

    ip_address: str | None = None
    user_agent: str | None = None


class AuthService:
    """Registration, login, token rotation and password management."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        password_service: PasswordService,
        token_service: TokenService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._repo = AuthRepository(session)
        self._passwords = password_service
        self._tokens = token_service

    async def register(
        self, payload: RegisterRequest, context: RequestContext | None = None
    ) -> tuple[User, TokenPair]:
        """Create a customer account and open a session for it."""
        role = await self._repo.get_role_by_name(ROLE_CUSTOMER)
        if role is None:
            raise NotFoundError("Default role is not configured.")

        password_hash = self._passwords.hash(payload.password.get_secret_value())
        user = User(
            email=payload.email,
            password_hash=password_hash,
            full_name=payload.full_name,
            role_id=role.id,
        )
        self._repo.add_user(user)

        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            logger.info("registration_conflict", constraint=_constraint_name(exc))
            raise ConflictError("Registration could not be completed.") from exc

        await self._session.refresh(user, attribute_names=["role"])
        tokens = await self._issue_token_pair(user, context)
        logger.info("user_registered", user_id=str(user.id), role=role.name)
        return user, tokens

    async def login(
        self, payload: LoginRequest, context: RequestContext | None = None
    ) -> tuple[User, TokenPair]:
        """Authenticate credentials and open a session.

        Every failure path returns the identical error. "No such user",
        "wrong password" and "account disabled" are indistinguishable to the
        caller — otherwise the endpoint tells an attacker which emails are
        registered, which is half of a credential-stuffing campaign.
        """
        generic_failure = AuthenticationError("Invalid email or password.")

        user = await self._repo.get_user_by_email(payload.email)
        if user is None:
            self._passwords.fake_verify()
            raise generic_failure

        if user.is_locked:
            logger.warning("login_blocked_locked_account", user_id=str(user.id))
            raise generic_failure

        if not self._passwords.verify(payload.password.get_secret_value(), user.password_hash):
            await self._register_failed_attempt(user)
            raise generic_failure

        if not user.is_active:
            logger.warning("login_blocked_inactive_account", user_id=str(user.id))
            raise generic_failure

        if self._passwords.needs_rehash(user.password_hash):
            user.password_hash = self._passwords.hash(payload.password.get_secret_value())
            logger.info("password_hash_upgraded", user_id=str(user.id))

        await self._repo.record_successful_login(user)
        tokens = await self._issue_token_pair(user, context)
        logger.info("user_logged_in", user_id=str(user.id), role=user.role_name)
        return user, tokens

    async def _register_failed_attempt(self, user: User) -> None:
        attempts = user.failed_login_attempts + 1
        lock_until = (
            datetime.now(UTC) + LOCKOUT_DURATION if attempts >= MAX_FAILED_LOGIN_ATTEMPTS else None
        )
        await self._repo.record_failed_login(user.id, lock_until=lock_until)
        await self._session.commit()
        logger.warning(
            "login_failed",
            user_id=str(user.id),
            attempts=attempts,
            locked=lock_until is not None,
        )

    async def refresh(
        self, raw_refresh_token: str, context: RequestContext | None = None
    ) -> TokenPair:
        """Exchange a refresh token for a new pair, rotating the old one.

        Reuse detection: presenting a token that was already rotated or revoked
        means the credential leaked — the legitimate client holds the *newer*
        token. We cannot tell attacker from victim, so we close every session
        for that user and force a fresh login.
        """
        claims = self._tokens.decode(raw_refresh_token, expected_type=TokenType.REFRESH)
        token_hash = self._fingerprint(raw_refresh_token)

        stored = await self._repo.get_refresh_token_by_hash(token_hash)
        if stored is None:
            raise AuthenticationError("Invalid refresh token.")

        if stored.revoked_at is not None:
            await self._repo.revoke_all_user_tokens(stored.user_id)
            await self._session.commit()
            logger.warning(
                "refresh_token_reuse_detected",
                user_id=str(stored.user_id),
                token_id=str(stored.id),
            )
            raise AuthenticationError("Session is no longer valid. Please sign in again.")

        if not stored.is_active:
            raise AuthenticationError("Refresh token has expired.")

        user = await self._repo.get_user_by_id(claims.subject)
        if user is None or not user.is_active or stored.user_id != user.id:
            raise AuthenticationError("Invalid refresh token.")

        new_tokens, new_record = await self._create_session(user, context)
        await self._repo.revoke_refresh_token(stored, replaced_by=new_record.id)
        logger.info("refresh_token_rotated", user_id=str(user.id))
        return new_tokens

    async def logout(self, raw_refresh_token: str) -> None:
        """Revoke one session. Idempotent by design.

        An unknown or already-revoked token still returns success: logout must
        never fail, and a distinguishable response would let a caller probe
        which tokens exist.
        """
        token_hash = self._fingerprint(raw_refresh_token)
        stored = await self._repo.get_refresh_token_by_hash(token_hash)
        if stored is not None and stored.revoked_at is None:
            await self._repo.revoke_refresh_token(stored)
            logger.info("user_logged_out", user_id=str(stored.user_id))

    async def logout_all(self, user_id: uuid.UUID) -> int:
        """Revoke every session of a user (e.g. "sign out of all devices")."""
        revoked = await self._repo.revoke_all_user_tokens(user_id)
        logger.info("all_sessions_revoked", user_id=str(user_id), count=revoked)
        return revoked

    async def change_password(self, user_id: uuid.UUID, payload: ChangePasswordRequest) -> None:
        """Rotate a password and invalidate every existing session.

        Requiring the current password stops an attacker with a stolen access
        token from locking the real owner out. Revoking sessions afterwards is
        what makes a password change actually evict an intruder.
        """
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        current = payload.current_password.get_secret_value()
        if not self._passwords.verify(current, user.password_hash):
            logger.warning("password_change_rejected", user_id=str(user_id))
            raise AuthorizationError("Current password is incorrect.")

        user.password_hash = self._passwords.hash(payload.new_password.get_secret_value())
        await self._repo.revoke_all_user_tokens(user_id)
        logger.info("password_changed", user_id=str(user_id))

    async def get_user(self, user_id: uuid.UUID) -> User:
        """Fetch a user by id. This is the module's public accessor."""
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    def _fingerprint(self, raw_token: str) -> str:
        """Keyed hash used to look a refresh token up without storing it."""
        return hash_token(raw_token, pepper=self._settings.secret_key.get_secret_value())

    async def _issue_token_pair(self, user: User, context: RequestContext | None) -> TokenPair:
        tokens, _ = await self._create_session(user, context)
        return tokens

    async def _create_session(
        self, user: User, context: RequestContext | None
    ) -> tuple[TokenPair, RefreshToken]:
        """Mint an access/refresh pair and persist the refresh token's hash."""
        if await self._repo.count_active_tokens(user.id) >= MAX_ACTIVE_SESSIONS:
            await self._repo.revoke_all_user_tokens(user.id)
            logger.info("session_limit_reached_revoking_all", user_id=str(user.id))

        access_token, _ = self._tokens.create_access_token(user_id=user.id, role=user.role_name)
        refresh_token, refresh_expires_at = self._tokens.create_refresh_token(
            user_id=user.id, role=user.role_name
        )

        record = RefreshToken(
            user_id=user.id,
            token_hash=self._fingerprint(refresh_token),
            expires_at=refresh_expires_at,
            created_by_ip=context.ip_address if context else None,
            user_agent=(context.user_agent[:255] if context and context.user_agent else None),
        )
        self._repo.add_refresh_token(record)
        await self._session.flush()

        pair = TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._settings.access_token_ttl_minutes * 60,
        )
        return pair, record


def _constraint_name(exc: IntegrityError) -> str:
    """Best-effort extraction of the violated constraint, for logging only."""
    original = getattr(exc, "orig", None)
    return str(getattr(original, "constraint_name", None) or "unknown")
