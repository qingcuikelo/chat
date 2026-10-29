"""用户接口测试。"""

from app.core.security import create_access_token
from app.models.user import User
from app.services.cache_service import CacheService

API = "/api/v1"


async def test_me_requires_token(client):
    response = await client.get(f"{API}/users/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_me_rejects_invalid_token(client):
    response = await client.get(
        f"{API}/users/me", headers={"Authorization": "Bearer invalid.token.value"}
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_me_ok(client, auth_headers):
    response = await client.get(f"{API}/users/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "alice"


async def test_update_me(client, auth_headers):
    response = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={
            "username": "alice_new",
            "email": "new@example.com",
            "current_password": "Passw0rd!",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice_new"
    assert body["email"] == "alice@example.com"
    assert body["pending_email"] == "new@example.com"
    assert body["email_verified"] is True

    login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice_new", "password": "Passw0rd!"},
    )
    assert login.status_code == 200


async def test_email_change_rejects_another_users_email(client, auth_headers, create_user_token):
    await create_user_token("bob", "bob@example.com")

    response = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"email": "bob@example.com", "current_password": "Passw0rd!"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EMAIL_TAKEN"


async def test_email_change_requires_current_password(client, auth_headers):
    response = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"email": "new@example.com"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


async def test_update_me_conflict(client, create_user_token):
    await create_user_token("alice", "alice@example.com")
    bob_headers = await create_user_token("bob", "bob@example.com")

    response = await client.patch(
        f"{API}/users/me", headers=bob_headers, json={"username": "alice"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USERNAME_TAKEN"


async def test_change_password_requires_current_password_and_revokes_sessions(client, auth_headers):
    login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    old_refresh = login.json()["refresh_token"]

    missing = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"password": "NewPassw0rd!"},
    )
    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "INVALID_REQUEST"

    wrong = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"password": "NewPassw0rd!", "current_password": "wrong"},
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"]["code"] == "INVALID_REQUEST"

    changed = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"password": "NewPassw0rd!", "current_password": "Passw0rd!"},
    )
    assert changed.status_code == 200

    old_access = await client.get(f"{API}/users/me", headers=auth_headers)
    assert old_access.status_code == 401

    old_refresh_attempt = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert old_refresh_attempt.status_code == 401

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


async def test_old_access_rejected_despite_stale_user_cache(client, auth_headers, fake_redis):
    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    other_access = create_access_token(me["id"], 0)

    changed = await client.patch(
        f"{API}/users/me",
        headers=auth_headers,
        json={"password": "NewPassw0rd!", "current_password": "Passw0rd!"},
    )
    assert changed.status_code == 200

    stale_user = User(
        id=me["id"],
        username="alice",
        email="alice@example.com",
        token_version=0,
        email_verified=True,
    )
    await CacheService().set_user(stale_user)

    response = await client.get(
        f"{API}/users/me",
        headers={"Authorization": f"Bearer {other_access}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_delete_me(client, auth_headers):
    response = await client.delete(f"{API}/users/me", headers=auth_headers)
    assert response.status_code == 204

    follow_up = await client.get(f"{API}/users/me", headers=auth_headers)
    assert follow_up.status_code == 401
