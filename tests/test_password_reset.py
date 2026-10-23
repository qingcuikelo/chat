"""v3 WP4 登录锁定与密码找回测试。"""

import app.services.auth_service as auth_module
from app.core.config import settings

API = "/api/v1"


async def test_login_lockout_after_threshold(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LOCKOUT_THRESHOLD", 2)
    monkeypatch.setattr(settings, "AUTH_LOCKOUT_MINUTES", 15)
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_PER_MINUTE", 100)
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_IP_PER_MINUTE", 100)

    wrong = {"username": "alice", "password": "wrong-password"}
    first = await client.post(f"{API}/auth/login", json=wrong)
    second = await client.post(f"{API}/auth/login", json=wrong)
    correct = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "ACCOUNT_LOCKED"
    assert second.headers.get("retry-after") == "900"
    assert correct.status_code == 429
    assert correct.json()["detail"]["code"] == "ACCOUNT_LOCKED"

    monkeypatch.setattr(settings, "AUTH_LOCKOUT_THRESHOLD", 0)
    unlocked = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    assert unlocked.status_code == 200


async def test_password_reset_flow(client, auth_headers, monkeypatch):
    captured: dict[str, str] = {}

    async def fake_dispatch(user, token: str) -> None:
        captured["token"] = token
        captured["user_id"] = str(user.id)

    monkeypatch.setattr(auth_module, "dispatch_password_reset_email", fake_dispatch)

    login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    old_refresh = login.json()["refresh_token"]

    forgot = await client.post(f"{API}/auth/password/forgot", json={"email": "alice@example.com"})
    assert forgot.status_code == 202
    assert captured["token"]

    reset = await client.post(
        f"{API}/auth/password/reset",
        json={"token": captured["token"], "new_password": "NewPassw0rd!"},
    )
    assert reset.status_code == 200

    old_login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "NewPassw0rd!"},
    )
    assert new_login.status_code == 200

    reused = await client.post(
        f"{API}/auth/password/reset",
        json={"token": captured["token"], "new_password": "AnotherPassw0rd!"},
    )
    assert reused.status_code == 400
    assert reused.json()["detail"]["code"] == "PASSWORD_RESET_INVALID"

    revoked = await client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
    assert revoked.status_code == 401


async def test_forgot_password_does_not_enumerate(client, monkeypatch):
    called = 0

    async def fake_dispatch(user, token: str) -> None:
        nonlocal called
        called += 1

    monkeypatch.setattr(auth_module, "dispatch_password_reset_email", fake_dispatch)

    known = await client.post(f"{API}/auth/password/forgot", json={"email": "nobody@example.com"})

    assert known.status_code == 202
    assert called == 0


async def test_password_reset_invalid_token(client):
    response = await client.post(
        f"{API}/auth/password/reset",
        json={"token": "not-a-token", "new_password": "NewPassw0rd!"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PASSWORD_RESET_INVALID"
