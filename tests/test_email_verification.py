"""邮箱验证测试。"""

import app.services.auth_service as auth_module
from app.core.config import settings
from app.core.security import create_email_verification_token
from app.models.user import User

API = "/api/v1"


async def _register(client, username="alice", email="alice@example.com"):
    return await client.post(
        f"{API}/auth/register",
        json={"username": username, "email": email, "password": "Passw0rd!"},
    )


async def test_register_then_login_blocked_until_verified(client):
    registered = await _register(client)
    assert registered.status_code == 201
    assert registered.json()["email_verified"] is False

    login = await client.post(
        f"{API}/auth/login", json={"username": "alice", "password": "Passw0rd!"}
    )
    assert login.status_code == 403
    assert login.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"


async def test_verify_email_then_login_succeeds(client):
    registered = await _register(client)
    user_id = registered.json()["id"]

    token = create_email_verification_token(user_id, "alice@example.com")
    verified = await client.post(f"{API}/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    assert verified.json()["email_verified"] is True

    login = await client.post(
        f"{API}/auth/login", json={"username": "alice", "password": "Passw0rd!"}
    )
    assert login.status_code == 200


async def test_verify_email_is_idempotent(client):
    registered = await _register(client)
    user_id = registered.json()["id"]
    token = create_email_verification_token(user_id, "alice@example.com")

    assert (await client.post(f"{API}/auth/verify-email", json={"token": token})).status_code == 200
    assert (await client.post(f"{API}/auth/verify-email", json={"token": token})).status_code == 200


async def test_verify_email_rejects_invalid_token(client):
    response = await client.post(f"{API}/auth/verify-email", json={"token": "not-a-token"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


async def test_verify_email_rejects_wrong_email_binding(client):
    registered = await _register(client)
    user_id = registered.json()["id"]
    token = create_email_verification_token(user_id, "other@example.com")

    response = await client.post(f"{API}/auth/verify-email", json={"token": token})
    assert response.status_code == 400


async def test_resend_verification_always_accepted(client):
    await _register(client)

    known = await client.post(
        f"{API}/auth/resend-verification", json={"email": "alice@example.com"}
    )
    assert known.status_code == 202

    unknown = await client.post(
        f"{API}/auth/resend-verification", json={"email": "nobody@example.com"}
    )
    assert unknown.status_code == 202


async def test_email_change_is_pending_until_new_address_verified(
    client, auth_headers, session_factory
):
    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    pending_email = "new@example.com"

    response = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"email": pending_email, "current_password": "Passw0rd!"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert body["pending_email"] == pending_email
    assert body["email_verified"] is True

    current_login = await client.post(
        f"{API}/auth/login", json={"username": "alice", "password": "Passw0rd!"}
    )
    assert current_login.status_code == 200

    pending_login = await client.post(
        f"{API}/auth/login", json={"email": pending_email, "password": "Passw0rd!"}
    )
    assert pending_login.status_code == 401

    async with session_factory() as session:
        user = await session.get(User, me["id"])
    assert user is not None
    assert user.email == "alice@example.com"
    assert user.pending_email == pending_email
    assert user.email_verified_at is not None

    token = create_email_verification_token(me["id"], pending_email, purpose="email_change")
    verified = await client.post(f"{API}/auth/verify-email", json={"token": token})
    assert verified.status_code == 200

    async with session_factory() as session:
        user = await session.get(User, me["id"])
    assert user is not None
    assert user.email == pending_email
    assert user.pending_email is None
    assert user.email_verified is True
    assert user.email_verified_at is not None

    old_access = await client.get(f"{API}/users/me", headers=auth_headers)
    assert old_access.status_code == 401

    old_login = await client.post(
        f"{API}/auth/login", json={"email": "alice@example.com", "password": "Passw0rd!"}
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        f"{API}/auth/login", json={"email": pending_email, "password": "Passw0rd!"}
    )
    assert new_login.status_code == 200


async def test_cancel_pending_email_change(client, auth_headers):
    me_before = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    pending_email = "new@example.com"
    requested = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"email": pending_email, "current_password": "Passw0rd!"},
    )
    assert requested.status_code == 200
    token = create_email_verification_token(me_before["id"], pending_email, purpose="email_change")

    cancelled = await client.delete(f"{API}/users/me/email-change", headers=auth_headers)
    assert cancelled.status_code == 204

    me = await client.get(f"{API}/users/me", headers=auth_headers)
    assert me.json()["pending_email"] is None
    assert me.json()["email"] == "alice@example.com"

    stale = await client.post(f"{API}/auth/verify-email", json={"token": token})
    assert stale.status_code == 400

    login = await client.post(
        f"{API}/auth/login", json={"username": "alice", "password": "Passw0rd!"}
    )
    assert login.status_code == 200


async def test_resend_verification_prefers_pending_email(client, auth_headers, monkeypatch):
    captured: dict[str, str] = {}

    async def fake_dispatch(user, *, email: str | None = None, purpose: str = "verify_email"):
        captured["email"] = email or user.email
        captured["purpose"] = purpose

    monkeypatch.setattr(auth_module, "dispatch_verification_email", fake_dispatch)
    requested = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"email": "new@example.com", "current_password": "Passw0rd!"},
    )
    assert requested.status_code == 200

    response = await client.post(
        f"{API}/auth/resend-verification", json={"email": "alice@example.com"}
    )
    assert response.status_code == 202
    assert captured["email"] == "new@example.com"
    assert captured["purpose"] == "email_change"


async def test_email_change_is_direct_when_verification_disabled(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_REQUIRED", False)

    response = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"email": "new@example.com", "current_password": "Passw0rd!"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "new@example.com"
    assert response.json()["pending_email"] is None

    login = await client.post(
        f"{API}/auth/login", json={"email": "new@example.com", "password": "Passw0rd!"}
    )
    assert login.status_code == 200


async def test_verification_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_REQUIRED", False)

    registered = await _register(client)
    assert registered.json()["email_verified"] is True

    login = await client.post(
        f"{API}/auth/login", json={"username": "alice", "password": "Passw0rd!"}
    )
    assert login.status_code == 200


async def test_verify_email_get_page_success(client):
    registered = await _register(client)
    user_id = registered.json()["id"]
    token = create_email_verification_token(user_id, "alice@example.com")

    response = await client.get(f"{API}/auth/verify-email", params={"token": token})
    assert response.status_code == 200
    assert "邮箱验证成功" in response.text

    login = await client.post(
        f"{API}/auth/login", json={"username": "alice", "password": "Passw0rd!"}
    )
    assert login.status_code == 200


async def test_verify_email_get_page_invalid(client):
    response = await client.get(f"{API}/auth/verify-email", params={"token": "not-a-token"})
    assert response.status_code == 400
    assert "邮箱验证失败" in response.text
