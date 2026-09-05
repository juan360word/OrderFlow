"""Pydantic contracts for the auth module.

Input and output models are separate types on purpose. If the ORM object were
serialised directly, adding a column would immediately publish it — which is
exactly how ``password_hash`` ends up in an API response. An explicit output
schema means a field is exposed only when someone writes it down.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from orderflow.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

# `strip_whitespace` catches the trailing space a mobile keyboard adds to an
# email; `max_length` bounds every free-text field so a payload cannot be used
# to push megabytes into the database.
NameStr = Annotated[str, StringConstraints(min_length=2, max_length=120, strip_whitespace=True)]
PasswordStr = Annotated[
    SecretStr, Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
]


class RegisterRequest(BaseModel):
    """Payload for ``POST /auth/register``."""

    model_config = ConfigDict(extra="forbid")  # reject unknown keys instead of ignoring them

    email: EmailStr
    password: PasswordStr
    full_name: NameStr

    @field_validator("password")
    @classmethod
    def _reject_trivial_passwords(cls, value: SecretStr) -> SecretStr:
        """Block the handful of passwords that appear in every breach corpus.

        Length is the main defence (enforced by the type), but a 12-character
        "passwordpassword" still fails instantly against a wordlist.
        """
        secret = value.get_secret_value()
        lowered = secret.lower()
        if lowered in _COMMON_PASSWORDS or len(set(secret)) < 5:
            raise ValueError("Password is too common or not varied enough.")
        return value

    @model_validator(mode="after")
    def _password_must_not_contain_email(self) -> Self:
        local_part = self.email.split("@", 1)[0].lower()
        if len(local_part) >= 4 and local_part in self.password.get_secret_value().lower():
            raise ValueError("Password must not contain your email address.")
        return self


class LoginRequest(BaseModel):
    """Payload for ``POST /auth/login``."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr


class RefreshRequest(BaseModel):
    """Payload for ``POST /auth/refresh`` and ``POST /auth/logout``."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: SecretStr


class TokenPair(BaseModel):
    """Response of register / login / refresh.

    ``token_type: bearer`` and ``expires_in`` follow RFC 6749 so any standard
    OAuth2 client library can consume this without custom code.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - RFC 6749 literal, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds")


class UserResponse(BaseModel):
    """Public representation of a user. Note what is absent: the hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    @classmethod
    def from_model(cls, user: object) -> UserResponse:
        """Build from an ORM ``User``, flattening the role relationship."""
        return cls(
            id=user.id,  # type: ignore[attr-defined]
            email=user.email,  # type: ignore[attr-defined]
            full_name=user.full_name,  # type: ignore[attr-defined]
            role=user.role.name,  # type: ignore[attr-defined]
            is_active=user.is_active,  # type: ignore[attr-defined]
            created_at=user.created_at,  # type: ignore[attr-defined]
            last_login_at=user.last_login_at,  # type: ignore[attr-defined]
        )


class ChangePasswordRequest(BaseModel):
    """Payload for ``POST /auth/change-password``."""

    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr
    new_password: PasswordStr

    @model_validator(mode="after")
    def _must_differ(self) -> Self:
        if self.current_password.get_secret_value() == self.new_password.get_secret_value():
            raise ValueError("The new password must be different from the current one.")
        return self


# Not a security control on its own — just the cheapest possible filter for the
# passwords that lead every breach list.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password123",
        "passwordpassword",
        "123456789012",
        "qwertyuiop123",
        "administrator",
        "letmeinplease",
        "iloveyou1234",
        "welcome12345",
        "changeme1234",
    }
)
