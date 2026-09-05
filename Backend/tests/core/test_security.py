"""Unit tests for the security primitives (phases 2 and 4).

Pure unit tests: no database, no HTTP. They pin the properties the rest of the
system assumes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from orderflow.core.config import Environment, Settings
from orderflow.core.errors import AuthenticationError, ValidationError
from orderflow.core.security import PasswordService, TokenService, TokenType, hash_token


class TestPasswordService:
    def test_hash_is_salted(self, password_service: PasswordService) -> None:
        """The same password must never produce the same hash twice."""
        first = password_service.hash("a-good-long-password")
        second = password_service.hash("a-good-long-password")

        assert first != second
        assert password_service.verify("a-good-long-password", first)
        assert password_service.verify("a-good-long-password", second)

    def test_hash_is_argon2id(self, password_service: PasswordService) -> None:
        assert password_service.hash("a-good-long-password").startswith("$argon2id$")

    def test_wrong_password_fails(self, password_service: PasswordService) -> None:
        stored = password_service.hash("a-good-long-password")

        assert not password_service.verify("A-Good-Long-Password", stored)

    def test_a_corrupt_hash_returns_false_instead_of_raising(
        self, password_service: PasswordService
    ) -> None:
        """A malformed row must not turn into a 500."""
        assert not password_service.verify("anything", "not-a-hash")

    @pytest.mark.parametrize("password", ["short", "x" * 200])
    def test_length_bounds_are_enforced(
        self, password_service: PasswordService, password: str
    ) -> None:
        with pytest.raises(ValidationError):
            password_service.hash(password)


class TestTokenService:
    def test_round_trip(self, settings: Settings) -> None:
        service = TokenService(settings)
        user_id = uuid.uuid4()

        token, expires_at = service.create_access_token(user_id=user_id, role="customer")
        claims = service.decode(token, expected_type=TokenType.ACCESS)

        assert claims.subject == user_id
        assert claims.role == "customer"
        assert expires_at > datetime.now(UTC)

    def test_each_token_has_a_unique_jti(self, settings: Settings) -> None:
        """The jti is what makes an individual token revocable."""
        service = TokenService(settings)
        user_id = uuid.uuid4()

        first, _ = service.create_access_token(user_id=user_id, role="customer")
        second, _ = service.create_access_token(user_id=user_id, role="customer")

        assert (
            service.decode(first, expected_type=TokenType.ACCESS).token_id
            != service.decode(second, expected_type=TokenType.ACCESS).token_id
        )

    def test_a_token_signed_with_another_key_is_rejected(self, settings: Settings) -> None:
        service = TokenService(settings)
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "typ": "access",
                "jti": uuid.uuid4().hex,
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
            },
            "an-attacker-controlled-key",
            algorithm="HS256",
        )

        with pytest.raises(AuthenticationError):
            service.decode(forged, expected_type=TokenType.ACCESS)

    def test_an_expired_token_is_rejected(self, settings: Settings) -> None:
        service = TokenService(settings)
        past = datetime.now(UTC) - timedelta(hours=2)
        expired = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "typ": "access",
                "jti": uuid.uuid4().hex,
                "iat": int(past.timestamp()),
                "exp": int((past + timedelta(minutes=15)).timestamp()),
                "iss": settings.jwt_issuer,
                "aud": settings.jwt_audience,
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(AuthenticationError):
            service.decode(expired, expected_type=TokenType.ACCESS)

    def test_a_token_for_another_audience_is_rejected(self, settings: Settings) -> None:
        """Stops a token minted for a sibling service from working here."""
        service = TokenService(settings)
        foreign = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "typ": "access",
                "jti": uuid.uuid4().hex,
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
                "iss": settings.jwt_issuer,
                "aud": "some-other-service",
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(AuthenticationError):
            service.decode(foreign, expected_type=TokenType.ACCESS)

    def test_refresh_lives_longer_than_access(self, settings: Settings) -> None:
        service = TokenService(settings)
        user_id = uuid.uuid4()

        _, access_expiry = service.create_access_token(user_id=user_id, role="customer")
        _, refresh_expiry = service.create_refresh_token(user_id=user_id, role="customer")

        assert refresh_expiry > access_expiry


class TestTokenHashing:
    def test_is_deterministic_and_keyed(self) -> None:
        assert hash_token("abc", pepper="k1") == hash_token("abc", pepper="k1")
        assert hash_token("abc", pepper="k1") != hash_token("abc", pepper="k2")

    def test_does_not_contain_the_token(self) -> None:
        assert "abc" not in hash_token("abc", pepper="k1")


class TestProductionHardening:
    """Configuration must refuse to boot in an unsafe production setup."""

    def test_debug_is_refused_in_production(self) -> None:
        with pytest.raises(ValueError, match="DEBUG must be false"):
            Settings(
                environment=Environment.PRODUCTION,
                debug=True,
                secret_key="x" * 48,  # type: ignore[arg-type]
                postgres_password="a-real-password",  # type: ignore[arg-type]
                allowed_hosts=("api.orderflow.dev",),
            )

    def test_default_database_password_is_refused_in_production(self) -> None:
        with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
            Settings(
                environment=Environment.PRODUCTION,
                debug=False,
                secret_key="x" * 48,  # type: ignore[arg-type]
                postgres_password="postgres",  # type: ignore[arg-type]
                allowed_hosts=("api.orderflow.dev",),
            )

    def test_wildcard_hosts_are_refused_in_production(self) -> None:
        with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
            Settings(
                environment=Environment.PRODUCTION,
                debug=False,
                secret_key="x" * 48,  # type: ignore[arg-type]
                postgres_password="a-real-password",  # type: ignore[arg-type]
                allowed_hosts=("*",),
            )

    def test_wildcard_cors_is_refused_in_production(self) -> None:
        with pytest.raises(ValueError, match="cors_origins"):
            Settings(
                environment=Environment.PRODUCTION,
                debug=False,
                secret_key="x" * 48,  # type: ignore[arg-type]
                postgres_password="a-real-password",  # type: ignore[arg-type]
                allowed_hosts=("api.orderflow.dev",),
                cors_origins=("*",),
            )

    def test_a_valid_production_configuration_boots(self) -> None:
        settings = Settings(
            environment=Environment.PRODUCTION,
            debug=False,
            secret_key="x" * 48,  # type: ignore[arg-type]
            postgres_password="a-real-password",  # type: ignore[arg-type]
            allowed_hosts=("api.orderflow.dev",),
            cors_origins=("https://orderflow.dev",),
        )

        assert settings.environment.is_production_like
