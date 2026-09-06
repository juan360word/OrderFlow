"""Auth API tests (phase 4).

These cover the security properties, not only the happy path: user
enumeration, privilege escalation, token type confusion, refresh rotation and
brute-force lockout.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient

from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.api

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
ME = "/api/v1/auth/me"


async def test_register_creates_a_customer(client: AsyncClient) -> None:
    response = await client.post(
        REGISTER,
        json={
            "email": "new@example.com",
            "password": TEST_PASSWORD,
            "full_name": "New Customer",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "customer"


async def test_register_never_returns_the_password_hash(client: AsyncClient) -> None:
    """The response must not expose credential material under any key."""
    response = await client.post(
        REGISTER,
        json={"email": "leak@example.com", "password": TEST_PASSWORD, "full_name": "Leak Test"},
    )

    body = response.text.lower()
    assert "password" not in body
    assert "argon2" not in body


async def test_register_rejects_a_weak_password(client: AsyncClient) -> None:
    response = await client.post(
        REGISTER,
        json={"email": "weak@example.com", "password": "short", "full_name": "Weak"},
    )

    assert response.status_code == 422
    assert "password" in response.json()["errors"]


async def test_register_rejects_a_password_containing_the_email(client: AsyncClient) -> None:
    response = await client.post(
        REGISTER,
        json={
            "email": "danielserrato@example.com",
            "password": "danielserrato-2026",
            "full_name": "Daniel",
        },
    )

    assert response.status_code == 422


async def test_duplicate_email_does_not_reveal_registration(client: AsyncClient) -> None:
    """A 409 must not confirm that an email is already registered.

    Otherwise the endpoint is a free user-enumeration oracle.
    """
    payload = {"email": "dup@example.com", "password": TEST_PASSWORD, "full_name": "Dup"}
    await client.post(REGISTER, json=payload)

    response = await client.post(REGISTER, json=payload)

    assert response.status_code == 409
    assert "dup@example.com" not in response.text
    assert "already" not in response.json()["detail"].lower()


async def test_email_uniqueness_is_case_insensitive(client: AsyncClient) -> None:
    """CITEXT is what enforces this — not application-level lowercasing."""
    await client.post(
        REGISTER,
        json={"email": "Case@Example.com", "password": TEST_PASSWORD, "full_name": "Case"},
    )

    response = await client.post(
        REGISTER,
        json={"email": "case@example.com", "password": TEST_PASSWORD, "full_name": "Case 2"},
    )

    assert response.status_code == 409


async def test_login_returns_a_token_pair(
    client: AsyncClient, make_user: Callable[..., object]
) -> None:
    await make_user(email="login@example.com")

    response = await client.post(
        LOGIN, json={"email": "login@example.com", "password": TEST_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] == 900


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("ghost@example.com", TEST_PASSWORD),
        ("known@example.com", "wrong-password-entirely"),
    ],
)
async def test_login_failures_are_indistinguishable(
    client: AsyncClient, make_user: Callable[..., object], email: str, password: str
) -> None:
    """Unknown user and wrong password must produce the identical response."""
    await make_user(email="known@example.com")

    response = await client.post(LOGIN, json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


async def test_login_locks_the_account_after_repeated_failures(
    client: AsyncClient, make_user: Callable[..., object]
) -> None:
    """Brute-force protection: the account locks even with the right password."""
    await make_user(email="lockme@example.com")

    for _ in range(5):
        await client.post(LOGIN, json={"email": "lockme@example.com", "password": "wrong-one-here"})

    response = await client.post(
        LOGIN, json={"email": "lockme@example.com", "password": TEST_PASSWORD}
    )

    assert response.status_code == 401


async def test_inactive_account_cannot_log_in(
    client: AsyncClient, make_user: Callable[..., object]
) -> None:
    await make_user(email="disabled@example.com", is_active=False)

    response = await client.post(
        LOGIN, json={"email": "disabled@example.com", "password": TEST_PASSWORD}
    )

    assert response.status_code == 401


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(ME)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_me_returns_the_caller(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.get(ME, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["email"] == "customer@example.com"


async def test_a_forged_token_is_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Flipping a byte of the signature must invalidate the token."""
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")

    response = await client.get(ME, headers={"Authorization": f"Bearer {tampered}"})

    assert response.status_code == 401


async def test_an_unsigned_token_is_rejected(client: AsyncClient) -> None:
    """The classic `alg: none` attack must not work."""
    unsigned = (
        "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0."
        "eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJ0eXAiOiJhY2Nlc3MifQ."
    )

    response = await client.get(ME, headers={"Authorization": f"Bearer {unsigned}"})

    assert response.status_code == 401


async def test_a_refresh_token_cannot_be_used_as_an_access_token(
    client: AsyncClient, make_user: Callable[..., object]
) -> None:
    """Token type confusion: the `typ` claim is what blocks this."""
    await make_user(email="typ@example.com")
    tokens = (
        await client.post(LOGIN, json={"email": "typ@example.com", "password": TEST_PASSWORD})
    ).json()

    response = await client.get(ME, headers={"Authorization": f"Bearer {tokens['refresh_token']}"})

    assert response.status_code == 401


async def test_refresh_rotates_the_token(
    client: AsyncClient, make_user: Callable[..., object]
) -> None:
    await make_user(email="rotate@example.com")
    tokens = (
        await client.post(LOGIN, json={"email": "rotate@example.com", "password": TEST_PASSWORD})
    ).json()

    response = await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    assert response.json()["refresh_token"] != tokens["refresh_token"]


async def test_reusing_a_rotated_refresh_token_kills_every_session(
    client: AsyncClient, make_user: Callable[..., object]
) -> None:
    """Reuse detection: a replayed refresh token revokes the whole family."""
    await make_user(email="reuse@example.com")
    first = (
        await client.post(LOGIN, json={"email": "reuse@example.com", "password": TEST_PASSWORD})
    ).json()
    second = (await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})).json()

    replay = await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401

    victim = await client.post(REFRESH, json={"refresh_token": second["refresh_token"]})
    assert victim.status_code == 401


async def test_logout_revokes_the_session(
    client: AsyncClient, make_user: Callable[..., object]
) -> None:
    await make_user(email="bye@example.com")
    tokens = (
        await client.post(LOGIN, json={"email": "bye@example.com", "password": TEST_PASSWORD})
    ).json()

    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert logout.status_code == 204

    reuse = await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401


async def test_logout_is_idempotent(client: AsyncClient) -> None:
    """An unknown token must not be distinguishable from a valid one."""
    response = await client.post("/api/v1/auth/logout", json={"refresh_token": "not-even-a-jwt"})

    assert response.status_code == 204


async def test_change_password_revokes_all_sessions(
    client: AsyncClient, make_user: Callable[..., object]
) -> None:
    await make_user(email="pwd@example.com")
    tokens = (
        await client.post(LOGIN, json={"email": "pwd@example.com", "password": TEST_PASSWORD})
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    response = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-passphrase-2026"},
    )
    assert response.status_code == 204

    reuse = await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401


async def test_change_password_requires_the_current_one(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/auth/change-password",
        headers=auth_headers,
        json={"current_password": "not-the-right-one", "new_password": "another-good-passphrase"},
    )

    assert response.status_code == 403


async def test_unknown_fields_are_rejected(client: AsyncClient) -> None:
    """`extra="forbid"` stops mass-assignment attempts such as role=admin."""
    response = await client.post(
        REGISTER,
        json={
            "email": "evil@example.com",
            "password": TEST_PASSWORD,
            "full_name": "Evil",
            "role": "admin",
            "is_active": True,
        },
    )

    assert response.status_code == 422
