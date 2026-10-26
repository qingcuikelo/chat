"""v2 WP4e 断点续传测试。"""

from app.core.config import settings
from app.services.chat_service import ChatService
from app.services.stream_service import StreamService

API = "/api/v1"


async def test_iter_stream_full_and_resume(fake_redis):
    stream = StreamService()
    request_id = "req_resume_test"
    await stream.init(request_id, 1, 1)
    await stream.publish(request_id, "start", {"user_message_id": 1})
    first_delta_id = await stream.publish(request_id, "delta", {"content": "A"})
    await stream.publish(request_id, "delta", {"content": "B"})
    await stream.set_status(request_id, "completed")
    await stream.publish(
        request_id,
        "done",
        {"assistant_message_id": 1, "token_count": 2, "finish_reason": "stop"},
    )

    service = ChatService()
    full = [(event, data) async for event, data, _ in service.iter_stream(request_id)]
    assert [event for event, _ in full] == ["start", "delta", "delta", "done"]

    resumed = [
        (event, data)
        async for event, data, _ in service.iter_stream(request_id, last_id=first_delta_id)
    ]
    assert [event for event, _ in resumed] == ["delta", "done"]
    assert resumed[0][1]["content"] == "B"


async def test_iter_stream_stale_yields_error(fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_STREAM_STALE_SECONDS", 0)
    stream = StreamService()
    request_id = "req_stale_test"
    await stream.init(request_id, 1, 1)
    await fake_redis.hset(f"chat:stream:{request_id}:meta", "updated_at", "1")

    service = ChatService()
    events = [(event, data) async for event, data, _ in service.iter_stream(request_id)]
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "STREAM_STALE"


async def test_resume_endpoint_accepts_header_and_query_cursor(client, auth_headers):
    me = (await client.get(f"{API}/users/me", headers=auth_headers)).json()
    stream = StreamService()
    request_id = "req_resume_endpoint_test"
    await stream.init(request_id, me["id"], conversation_id=1)
    await stream.publish(request_id, "start", {"request_id": request_id, "user_message_id": 1})
    cursor = await stream.publish(request_id, "delta", {"content": "A"})
    await stream.publish(request_id, "delta", {"content": "B"})
    await stream.publish(
        request_id,
        "done",
        {"assistant_message_id": 1, "token_count": 1, "finish_reason": "stop"},
    )
    await stream.set_status(request_id, "completed")

    by_header = await client.get(
        f"{API}/chat/stream/{request_id}",
        headers={**auth_headers, "Last-Event-ID": cursor},
    )
    assert by_header.status_code == 200
    assert "event: delta" in by_header.text
    assert "event: done" in by_header.text
    assert "event: start" not in by_header.text

    by_query = await client.get(
        f"{API}/chat/stream/{request_id}",
        headers=auth_headers,
        params={"last_event_id": cursor},
    )
    assert by_query.status_code == 200
    assert "event: delta" in by_query.text
    assert "event: done" in by_query.text


async def test_resume_endpoint_unknown_request(client, auth_headers):
    response = await client.get(f"{API}/chat/stream/req_does_not_exist", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "STREAM_NOT_FOUND"


async def test_resume_endpoint_owner_only(client, create_user_token):
    alice_headers = await create_user_token("alice", "alice@example.com")
    me = (await client.get(f"{API}/users/me", headers=alice_headers)).json()

    stream = StreamService()
    request_id = "req_owner_test"
    await stream.init(request_id, me["id"], conversation_id=1)
    await stream.set_status(request_id, "completed")

    allowed = await client.get(f"{API}/chat/stream/{request_id}", headers=alice_headers)
    assert allowed.status_code == 200

    bob_headers = await create_user_token("bob", "bob@example.com")
    denied = await client.get(f"{API}/chat/stream/{request_id}", headers=bob_headers)
    assert denied.status_code == 404
    assert denied.json()["detail"]["code"] == "STREAM_NOT_FOUND"
