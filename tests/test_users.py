"""用户接口测试。"""

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
        json={"username": "alice_new", "email": "new@example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice_new"
    assert body["email"] == "new@example.com"


async def test_update_me_conflict(client, create_user_token):
    await create_user_token("alice", "alice@example.com")
    bob_headers = await create_user_token("bob", "bob@example.com")

    response = await client.patch(
        f"{API}/users/me", headers=bob_headers, json={"username": "alice"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USERNAME_TAKEN"


async def test_delete_me(client, auth_headers):
    response = await client.delete(f"{API}/users/me", headers=auth_headers)
    assert response.status_code == 204

    follow_up = await client.get(f"{API}/users/me", headers=auth_headers)
    assert follow_up.status_code == 401
