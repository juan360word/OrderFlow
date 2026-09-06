"""HTTP layer for the auth module.

The router does three things and nothing else: bind a URL, validate the input
into a schema, and hand it to the service. It contains no business rule — that
is what makes the same logic reusable from a worker or a CLI.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import SecretStr

from orderflow.core.dependencies import AuthServiceDep, CurrentUser, RequestContextDep
from orderflow.core.rate_limit import rate_limit
from orderflow.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)

login_rate_limit = Depends(
    rate_limit(
        "login",
        limit=lambda settings: settings.login_rate_limit_attempts,
        window=lambda settings: settings.login_rate_limit_window_seconds,
    )
)
registration_rate_limit = Depends(
    rate_limit(
        "register",
        limit=lambda settings: settings.login_rate_limit_attempts,
        window=lambda settings: settings.login_rate_limit_window_seconds,
    )
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer account",
    dependencies=[registration_rate_limit],
    responses={
        409: {"description": "Registration could not be completed"},
        429: {"description": "Too many attempts from this address"},
    },
)
async def register(
    payload: RegisterRequest,
    service: AuthServiceDep,
    context: RequestContextDep,
) -> UserResponse:
    """Register a new customer.

    Returns the created user; the session tokens are obtained via ``/login``.
    Splitting the two keeps registration replayable without minting extra
    sessions, and keeps this response free of credentials.
    """
    user, _tokens = await service.register(payload, context)
    return UserResponse.from_model(user)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange credentials for a token pair",
    dependencies=[login_rate_limit],
    responses={
        401: {"description": "Invalid email or password"},
        429: {"description": "Too many attempts from this address"},
    },
)
async def login(
    payload: LoginRequest,
    service: AuthServiceDep,
    context: RequestContextDep,
) -> TokenPair:
    """Authenticate and open a session."""
    _user, tokens = await service.login(payload, context)
    return tokens


@router.post(
    "/token",
    response_model=TokenPair,
    include_in_schema=True,
    summary="OAuth2 password flow (enables the Swagger 'Authorize' button)",
    dependencies=[login_rate_limit],
)
async def login_form(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: AuthServiceDep,
    context: RequestContextDep,
) -> TokenPair:
    """Same as ``/login`` but with a form body.

    Exists purely so the interactive docs can authenticate: the OAuth2 spec
    mandates ``application/x-www-form-urlencoded`` with a ``username`` field.
    """
    payload = LoginRequest(email=form.username, password=SecretStr(form.password))
    _user, tokens = await service.login(payload, context)
    return tokens


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate a refresh token for a fresh pair",
    responses={401: {"description": "Invalid, expired or already-used refresh token"}},
)
async def refresh(
    payload: RefreshRequest,
    service: AuthServiceDep,
    context: RequestContextDep,
) -> TokenPair:
    """Rotate the session. The presented refresh token is invalidated."""
    return await service.refresh(payload.refresh_token.get_secret_value(), context)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one session",
)
async def logout(payload: RefreshRequest, service: AuthServiceDep) -> None:
    """Revoke the given refresh token. Always succeeds (idempotent)."""
    await service.logout(payload.refresh_token.get_secret_value())


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke every session of the current user",
)
async def logout_all(user: CurrentUser, service: AuthServiceDep) -> None:
    """Sign out of all devices."""
    await service.logout_all(user.id)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Profile of the authenticated user",
)
async def read_me(user: CurrentUser) -> UserResponse:
    """Return the caller's own profile."""
    return UserResponse.from_model(user)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the password and end every session",
)
async def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    service: AuthServiceDep,
) -> None:
    """Rotate the password. All existing sessions are revoked."""
    await service.change_password(user.id, payload)
