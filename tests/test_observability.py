"""v2 WP7 可观测性测试。"""

import app.main as main_module


async def test_request_id_header_generated_and_echoed(client):
    response = await client.get("/health")
    assert response.headers.get("x-request-id")

    custom = await client.get("/health", headers={"X-Request-ID": "abc123"})
    assert custom.headers.get("x-request-id") == "abc123"


async def test_health_live(client):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_health_ready_ok(client, db_engine, monkeypatch):
    monkeypatch.setattr(main_module, "engine", db_engine)

    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True, "redis": True}


async def test_health_ready_reports_redis_failure(client, db_engine, monkeypatch):
    monkeypatch.setattr(main_module, "engine", db_engine)

    class _Down:
        async def ping(self):
            raise RuntimeError("redis down")

    monkeypatch.setattr(main_module, "get_redis", lambda: _Down())

    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["redis"] is False
