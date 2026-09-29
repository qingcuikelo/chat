"""认证接口测试。"""

from app.core.security import decode_token

API = "/api/v1"


async def test_register_success(client):
    response = await client.post(
        f"{API}/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "Passw0rd!"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "alice"
    assert body["email"] == "alice@example.com"
    assert {"id", "created_at", "updated_at"} <= body.keys()
    assert "password" not in body and "password_hash" not in body


async def test_register_duplicate_username(client):
    payload = {"username": "alice", "email": "a@example.com", "password": "Passw0rd!"}
    await client.post(f"{API}/auth/register", json=payload)

    response = await client.post(
        f"{API}/auth/register",
        json={"username": "alice", "email": "b@example.com", "password": "Passw0rd!"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USERNAME_TAKEN"


async def test_register_duplicate_email(client):
    payload = {"username": "alice", "email": "a@example.com", "password": "Passw0rd!"}
    await client.post(f"{API}/auth/register", json=payload)

    response = await client.post(
        f"{API}/auth/register",
        json={"username": "bob", "email": "a@example.com", "password": "Passw0rd!"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EMAIL_TAKEN"


async def test_register_invalid_payload(client):
    response = await client.post(
        f"{API}/auth/register",
        json={"username": "alice", "email": "not-an-email", "password": "short"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


async def test_login_success(client, auth_headers):
    response = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


async def test_login_by_email(client, auth_headers):
    response = await client.post(
        f"{API}/auth/login",
        json={"email": "alice@example.com", "password": "Passw0rd!"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_login_wrong_password(client, auth_headers):
    response = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_CREDENTIALS"


async def test_refresh_success(client, auth_headers):
    login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    refresh_token = login.json()["refresh_token"]

    response = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_refresh_rejects_access_token(client, auth_headers):
    login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    access_token = login.json()["access_token"]

    response = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": access_token}
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_logout_requires_token(client):
    response = await client.post(f"{API}/auth/logout")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_logout_revokes_access_token(client, auth_headers):
    assert (await client.get(f"{API}/users/me", headers=auth_headers)).status_code == 200

    response = await client.post(f"{API}/auth/logout", headers=auth_headers)
    assert response.status_code == 204

    follow_up = await client.get(f"{API}/users/me", headers=auth_headers)
    assert follow_up.status_code == 401
    assert follow_up.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_logout_revokes_refresh_token(client, auth_headers):
    login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    refresh_token = login.json()["refresh_token"]

    response = await client.post(
        f"{API}/auth/logout",
        headers=auth_headers,
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == 204

    refresh_attempt = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert refresh_attempt.status_code == 401


async def test_refresh_rotation_single_use(client, auth_headers):
    login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    first_refresh = login.json()["refresh_token"]

    rotated = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert rotated.status_code == 200
    second_refresh = rotated.json()["refresh_token"]

    reused = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert reused.status_code == 401

    again = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": second_refresh}
    )
    assert again.status_code == 200


async def test_token_state_in_redis(client, auth_headers, fake_redis):
    login = await client.post(
        f"{API}/auth/login",
        json={"username": "alice", "password": "Passw0rd!"},
    )
    refresh_jti = decode_token(login.json()["refresh_token"])["jti"]
    assert await fake_redis.exists(f"auth:refresh:{refresh_jti}")

    await client.post(f"{API}/auth/logout", headers=auth_headers)
    access_jti = decode_token(auth_headers["Authorization"].split()[1])["jti"]
    assert await fake_redis.exists(f"auth:blacklist:{access_jti}")
