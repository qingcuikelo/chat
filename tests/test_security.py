"""v3 WP4 安全加固测试。"""

from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import create_app

API = "/api/v1"


async def test_security_headers_present(client):
    response = await client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "content-security-policy" in response.headers
    assert "strict-transport-security" not in response.headers


async def test_hsts_only_on_https():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get("/health")

    assert "strict-transport-security" in response.headers


async def test_trusted_host_rejects_unknown_host(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", "api.example.com")
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://evil.example.com"
    ) as client:
        rejected = await client.get("/health")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.example.com"
    ) as client:
        allowed = await client.get("/health")

    assert rejected.status_code == 400
    assert allowed.status_code == 200


async def test_chat_message_length_limit(client, auth_headers):
    conversation = await client.post(f"{API}/conversations", headers=auth_headers, json={})
    conversation_id = conversation.json()["id"]

    response = await client.post(
        f"{API}/chat",
        headers=auth_headers,
        json={
            "conversation_id": conversation_id,
            "message": "x" * (settings.CHAT_MAX_MESSAGE_CHARS + 1),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
