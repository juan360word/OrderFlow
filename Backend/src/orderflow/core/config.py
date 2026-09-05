"""Application configuration.

Every knob of the system is read from the environment and validated at import
time by Pydantic. This is a deliberate "fail fast" choice: a misconfigured
container must crash on boot with a readable error instead of serving traffic
with a missing secret and blowing up on the first request that needs it.
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    PostgresDsn,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environments.

    A StrEnum (not a bare ``str``) means an invalid value such as
    ``ENVIRONMENT=produccion`` is rejected on boot rather than silently
    disabling production hardening.
    """

    LOCAL = "local"
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production_like(self) -> bool:
        """True where secrets must be real and debug output must be off."""
        return self in (Environment.STAGING, Environment.PRODUCTION)


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Secrets are case-insensitive in .env but frozen once loaded: settings
        # are read concurrently by every request, so they must be immutable.
        case_sensitive=False,
        frozen=True,
    )

    # -- Application ------------------------------------------------------
    app_name: str = "OrderFlow API"
    app_version: str = "0.1.0"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # -- HTTP -------------------------------------------------------------
    # Empty by default: a browser origin must be opted in explicitly. "*" is
    # rejected in production-like environments (see the validator below).
    # Tuples, not lists: Settings is frozen, and a frozen model is only
    # hashable (hence cacheable, hence usable as a dependency key) if every
    # field is hashable too.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ()
    # Host header allow-list; blocks Host-header poisoning behind a proxy.
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("*",)
    # Hard cap on request bodies so a single client cannot exhaust memory.
    max_request_body_bytes: Annotated[int, Field(gt=0)] = 1024 * 1024  # 1 MiB

    # -- Database ---------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "orderflow"
    postgres_password: SecretStr = SecretStr("orderflow")
    postgres_db: str = "orderflow"
    # Pool sizing: pool_size is the steady-state number of connections held
    # open; max_overflow is the burst allowance. Postgres connections are
    # processes, so this must stay well under the server's max_connections.
    db_pool_size: Annotated[int, Field(ge=1)] = 10
    db_max_overflow: Annotated[int, Field(ge=0)] = 5
    db_pool_timeout_seconds: Annotated[float, Field(gt=0)] = 10.0
    db_echo: bool = False
    # Server-side guard against a runaway query holding locks forever.
    db_statement_timeout_ms: Annotated[int, Field(ge=0)] = 10_000
    db_lock_timeout_ms: Annotated[int, Field(ge=0)] = 5_000

    # -- Security / auth --------------------------------------------------
    # 32+ random bytes. Generated per-process when absent so local dev works,
    # but a generated value is refused in staging/production: tokens must stay
    # valid across restarts and across replicas.
    secret_key: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(48)))
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "orderflow"
    jwt_audience: str = "orderflow-api"
    # Short-lived access token + long-lived rotating refresh token: if an
    # access token leaks, the blast radius is minutes, and the refresh token
    # is revocable because it lives in the database.
    access_token_ttl_minutes: Annotated[int, Field(gt=0, le=60)] = 15
    refresh_token_ttl_days: Annotated[int, Field(gt=0, le=90)] = 7

    # Argon2id parameters (OWASP Password Storage Cheat Sheet, 2024 baseline).
    argon2_time_cost: Annotated[int, Field(ge=1)] = 3
    argon2_memory_cost_kib: Annotated[int, Field(ge=8192)] = 65536  # 64 MiB
    argon2_parallelism: Annotated[int, Field(ge=1)] = 4

    # -- Inventory --------------------------------------------------------
    # Which concurrency strategy the inventory module uses. Both are correct;
    # see modules/inventory/service.py for the trade-off.
    inventory_locking_strategy: Literal["pessimistic", "atomic_update"] = "atomic_update"
    inventory_reservation_ttl_minutes: Annotated[int, Field(gt=0)] = 30

    # -- Derived ----------------------------------------------------------

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy DSN (asyncpg driver)."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password.get_secret_value(),
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    # -- Validation -------------------------------------------------------

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept ``A,B`` from the environment as well as a JSON list."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origins(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        environment = info.data.get("environment")
        is_prod = isinstance(environment, Environment) and environment.is_production_like
        if is_prod and "*" in value:
            raise ValueError("cors_origins must not contain '*' outside local development")
        return value

    @model_validator(mode="after")
    def _enforce_production_hardening(self) -> Self:
        """Refuse to boot a production-like process with dev-grade settings."""
        if not self.environment.is_production_like:
            return self

        problems: list[str] = []
        if self.debug:
            problems.append("DEBUG must be false")
        if len(self.secret_key.get_secret_value()) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")
        if "SECRET_KEY" not in _explicitly_set(self):
            problems.append(
                "SECRET_KEY must be set explicitly "
                "(a random per-boot key invalidates every token on restart)"
            )
        if self.postgres_password.get_secret_value() in {"orderflow", "postgres", "password", ""}:
            problems.append("POSTGRES_PASSWORD must not be a default value")
        if "*" in self.allowed_hosts:
            problems.append("ALLOWED_HOSTS must be an explicit list")

        if problems:
            raise ValueError(
                f"Unsafe configuration for environment={self.environment}: " + "; ".join(problems)
            )
        return self


def _explicitly_set(settings: Settings) -> set[str]:
    """Field names the operator actually provided (vs. defaulted)."""
    return {name.upper() for name in settings.model_fields_set}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so the ``.env`` file is parsed once, and injectable: tests override
    this dependency instead of mutating global state.
    """
    return Settings()
