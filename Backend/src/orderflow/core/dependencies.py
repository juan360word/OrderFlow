"""Application-wide FastAPI dependencies.

Everything the routers need — settings, a database session, the services, the
authenticated user — is resolved here. Two reasons this is not a pile of module
globals:

* **Testability.** A test overrides ``app.dependency_overrides[get_db]`` and
  gets its own transaction. With globals it would have to monkey-patch imports.
* **Lifecycle.** The engine is created on startup and disposed on shutdown; a
  module-level engine would be built at import time, before configuration is
  even known, and would leak connections in tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from orderflow.core.cache import CacheClient
from orderflow.core.config import Settings, get_settings
from orderflow.core.database import Database
from orderflow.core.errors import AuthenticationError, AuthorizationError
from orderflow.core.logging import get_logger
from orderflow.core.security import PasswordService, TokenService, TokenType
from orderflow.modules.auth.models import ROLE_ADMIN, User
from orderflow.modules.auth.service import AuthService, RequestContext

logger = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


def get_app_settings(request: Request) -> Settings:
    """Settings this application instance was built with.

    Not ``get_settings()`` directly: an app constructed with explicit settings
    (a test, or a second app in one process) must not have its dependencies
    silently read the process-wide ``.env`` instead.
    """
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_database(request: Request) -> Database:
    """The Database owned by this application instance (set up in lifespan)."""
    database = getattr(request.app.state, "database", None)
    if database is None:  # pragma: no cover - misconfiguration guard
        raise RuntimeError("Database is not initialised; check the application lifespan.")
    return database  # type: ignore[no-any-return]


def get_cache(request: Request) -> CacheClient:
    """The CacheClient owned by this application instance."""
    cache = getattr(request.app.state, "cache", None)
    if cache is None:  # pragma: no cover - misconfiguration guard
        raise RuntimeError("Cache is not initialised; check the application lifespan.")
    return cache  # type: ignore[no-any-return]


CacheDep = Annotated[CacheClient, Depends(get_cache)]


async def get_db_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """One transaction per request.

    FastAPI runs the code after ``yield`` when the response is finished, so the
    commit happens only if the endpoint returned normally. Any exception —
    including one raised by a later dependency — rolls the transaction back.
    That is what makes a partially-applied write impossible.
    """
    async with database.session() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_password_service(settings: SettingsDep) -> PasswordService:
    return _password_service_singleton(settings)


def get_token_service(settings: SettingsDep) -> TokenService:
    return TokenService(settings)


@lru_cache(maxsize=4)
def _password_service_singleton(settings: Settings) -> PasswordService:
    return PasswordService(settings)


def get_auth_service(
    session: DbSession,
    settings: SettingsDep,
    passwords: Annotated[PasswordService, Depends(get_password_service)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
) -> AuthService:
    return AuthService(session, settings, passwords, tokens)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_request_context(request: Request) -> RequestContext:
    """Audit metadata for security-relevant events."""
    forwarded = request.headers.get("x-forwarded-for")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    return RequestContext(ip_address=ip, user_agent=request.headers.get("user-agent"))


RequestContextDep = Annotated[RequestContext, Depends(get_request_context)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    auth_service: AuthServiceDep,
) -> User:
    """Resolve the caller from the ``Authorization: Bearer`` header.

    The signature is verified cryptographically, but we still load the user
    row. A JWT is a *snapshot*: it cannot know that the account was disabled or
    the role downgraded thirty seconds after it was issued. For a 15-minute
    token, one indexed primary-key lookup is the right price for that
    correctness.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Authentication credentials were not provided.")

    claims = tokens.decode(credentials.credentials, expected_type=TokenType.ACCESS)
    user = await auth_service.get_user(claims.subject)

    if not user.is_active:
        raise AuthenticationError("Account is disabled.")

    if claims.role != user.role_name:
        logger.info("token_role_stale", user_id=str(user.id), token_role=claims.role)

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*allowed_roles: str) -> object:
    """Build a dependency that admits only the listed roles.

    Authentication ("who are you") is ``get_current_user``; authorization
    ("may you do this") is this. Keeping them separate means an endpoint can
    require a valid session without requiring a privilege, and the privilege
    check is declared in the route signature where a reviewer can see it —
    rather than buried in the handler body where it is easy to forget.
    """
    allowed = frozenset(allowed_roles)

    async def dependency(user: CurrentUser) -> User:
        if user.role_name not in allowed:
            logger.warning(
                "authorization_denied",
                user_id=str(user.id),
                role=user.role_name,
                required=sorted(allowed),
            )
            raise AuthorizationError()
        return user

    return Depends(dependency)


AdminUser = Annotated[User, require_roles(ROLE_ADMIN)]
