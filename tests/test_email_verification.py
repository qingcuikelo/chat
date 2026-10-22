"""邮箱验证测试。"""

from app.core.config import settings
from app.core.security import create_email_verification_token

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


async def test_email_change_resets_verification(client, auth_headers):
    response = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"email": "new@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["email_verified"] is False

    login = await client.post(
        f"{API}/auth/login", json={"username": "alice", "password": "Passw0rd!"}
    )
    assert login.status_code == 403
    assert login.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"


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
