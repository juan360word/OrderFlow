"""Password hashing and JWT issuing/verification.

Password storage — why Argon2id and not bcrypt:
    A password hash must be *deliberately slow* so that an attacker holding a
    stolen `users` table cannot test billions of guesses per second. bcrypt is
    slow on CPU but cheap on memory, so a GPU or an ASIC parallelises it
    massively. Argon2id is *memory-hard*: each guess must allocate 64 MiB here,
    which is what makes GPU cracking economically painful. It won the Password
    Hashing Competition and is the OWASP first choice today. bcrypt remains
    acceptable but also caps input at 72 bytes, silently truncating long
    passphrases.

    Salting is handled by the library: every hash embeds a fresh random salt,
    which is why two users with the same password get different hashes and a
    precomputed rainbow table is useless.

Tokens — why a short access token plus a rotating refresh token:
    A JWT is *self-validating*: the server verifies the signature and trusts
    the claims without a database lookup. That is what makes it scale, and also
    its one weakness — you cannot un-issue it. So the access token lives 15
    minutes (small blast radius if it leaks), and long-lived sessions ride on a
    refresh token whose hash is stored in the database, which means it *can* be
    revoked, and is rotated on every use so a stolen one is detectable.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type as Argon2Type

from orderflow.core.config import Settings
from orderflow.core.errors import AuthenticationError
from orderflow.core.logging import get_logger

logger = get_logger(__name__)

# Bounds enforced before hashing. The lower bound is a policy decision; the
# upper bound is a denial-of-service guard — Argon2 cost grows with input, so
# an unbounded password field is a free way to burn 64 MiB of server memory.
MIN_PASSWORD_LENGTH: Final = 12
MAX_PASSWORD_LENGTH: Final = 128


class TokenType(StrEnum):
    """Distinguishes token purposes.

    Stamped into the ``typ`` claim and checked on every verification: without
    it, a refresh token would be accepted as an access token, silently turning
    a 7-day credential into an API key.
    """

    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Validated contents of a JWT."""

    subject: uuid.UUID
    token_type: TokenType
    token_id: uuid.UUID
    role: str
    issued_at: datetime
    expires_at: datetime


class PasswordService:
    """Hashes and verifies passwords with Argon2id."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=32,
            salt_len=16,
            # Argon2id: hybrid of Argon2i (side-channel resistant) and Argon2d
            # (GPU resistant). The variant OWASP recommends for passwords.
            type=Argon2Type.ID,
        )
        # A precomputed hash of a random string, used to burn the same CPU time
        # when the user does not exist. Without it, "unknown user" returns in
        # microseconds while "wrong password" takes ~50ms, and that timing gap
        # is a free user-enumeration oracle.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))

    def hash(self, password: str) -> str:
        """Hash a plaintext password. Never store the input anywhere."""
        self._validate_length(password)
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        """Constant-ish-time verification. Returns False, never raises."""
        try:
            self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        return True

    def fake_verify(self) -> None:
        """Spend the cost of a verification against a throwaway hash.

        Call this on the "user not found" branch of login so that both branches
        take comparable time.
        """
        with suppress(VerifyMismatchError, VerificationError):
            self._hasher.verify(self._dummy_hash, "not-the-password")

    def needs_rehash(self, password_hash: str) -> bool:
        """True when a stored hash used weaker parameters than we use now.

        Lets us transparently upgrade cost parameters on the next successful
        login instead of forcing a password reset on every user.
        """
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True

    @staticmethod
    def _validate_length(password: str) -> None:
        # NIST SP 800-63B: length is what buys entropy. We deliberately do not
        # impose character-class rules ("must contain a symbol") — they push
        # users toward predictable patterns like "Password1!" without adding
        # real entropy.
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            from orderflow.core.errors import ValidationError

            raise ValidationError(
                f"Password must be between {MIN_PASSWORD_LENGTH} and "
                f"{MAX_PASSWORD_LENGTH} characters."
            )


class TokenService:
    """Issues and verifies JWTs."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._key = settings.secret_key.get_secret_value()
        self._algorithm = settings.jwt_algorithm

    def create_access_token(self, *, user_id: uuid.UUID, role: str) -> tuple[str, datetime]:
        """Return (token, expiry) for a short-lived API credential."""
        return self._create(
            user_id=user_id,
            role=role,
            token_type=TokenType.ACCESS,
            ttl=timedelta(minutes=self._settings.access_token_ttl_minutes),
        )

    def create_refresh_token(self, *, user_id: uuid.UUID, role: str) -> tuple[str, datetime]:
        """Return (token, expiry) for a long-lived, revocable credential."""
        return self._create(
            user_id=user_id,
            role=role,
            token_type=TokenType.REFRESH,
            ttl=timedelta(days=self._settings.refresh_token_ttl_days),
        )

    def _create(
        self,
        *,
        user_id: uuid.UUID,
        role: str,
        token_type: TokenType,
        ttl: timedelta,
    ) -> tuple[str, datetime]:
        now = datetime.now(UTC)
        expires_at = now + ttl
        payload: dict[str, Any] = {
            "sub": str(user_id),
            "typ": token_type.value,
            "role": role,
            # jti — a unique id per token. It is what makes a specific refresh
            # token revocable and what detects replay of a rotated one.
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": self._settings.jwt_issuer,
            "aud": self._settings.jwt_audience,
        }
        token = jwt.encode(payload, self._key, algorithm=self._algorithm)
        return token, expires_at

    def decode(self, token: str, *, expected_type: TokenType) -> TokenClaims:
        """Verify signature, standard claims and token type.

        ``algorithms`` is pinned to a single value on purpose: accepting the
        algorithm advertised inside the token itself is the classic JWT
        vulnerability (``alg: none``, or an RS256 public key replayed as an
        HS256 shared secret).
        """
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=[self._algorithm],
                issuer=self._settings.jwt_issuer,
                audience=self._settings.jwt_audience,
                options={
                    "require": ["sub", "exp", "iat", "jti", "typ", "iss", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            # One generic message for every malformed/forged/wrong-audience
            # case: telling an attacker *why* their token was rejected helps
            # them craft the next one.
            logger.warning("token_rejected", reason=type(exc).__name__)
            raise AuthenticationError("Could not validate credentials.") from exc

        if payload.get("typ") != expected_type.value:
            raise AuthenticationError("Could not validate credentials.")

        try:
            return TokenClaims(
                subject=uuid.UUID(payload["sub"]),
                token_type=TokenType(payload["typ"]),
                token_id=uuid.UUID(hex=payload["jti"]),
                role=str(payload.get("role", "")),
                issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
                expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            )
        except (KeyError, ValueError) as exc:
            raise AuthenticationError("Could not validate credentials.") from exc


def hash_token(token: str, *, pepper: str) -> str:
    """Fingerprint a refresh token for storage.

    Refresh tokens are stored hashed for the same reason passwords are: a leak
    of the database must not hand out live sessions. Unlike a password, a JWT
    is already 300+ bits of unguessable data, so a fast keyed hash is enough —
    there is nothing to brute-force — and it keeps token lookup a single
    indexed equality check instead of an Argon2 verification per row.
    """
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def constant_time_compare(left: str, right: str) -> bool:
    """Timing-safe string comparison for secret material."""
    return hmac.compare_digest(left.encode(), right.encode())
