"""会话与消息接口测试。"""

from app.services.llm_service import LLMService, StreamChunk

API = "/api/v1"


async def _seed_chat(client, headers, conversation_id, monkeypatch, rounds=3):
    async def fake_stream(self, messages):
        yield StreamChunk("delta", text="回复")
        yield StreamChunk("final", finish_reason="stop")

    monkeypatch.setattr(LLMService, "stream_chat", fake_stream)
    for _ in range(rounds):
        await client.post(
            f"{API}/chat",
            headers=headers,
            json={"conversation_id": conversation_id, "message": "hi"},
        )


async def test_create_conversation(client, auth_headers):
    response = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "新会话"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "新会话"
    assert body["user_id"] > 0


async def test_list_conversations_pagination(client, auth_headers):
    for index in range(3):
        await client.post(
            f"{API}/conversations", headers=auth_headers, json={"title": f"c{index}"}
        )

    response = await client.get(
        f"{API}/conversations", headers=auth_headers, params={"page": 1, "page_size": 2}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2


async def test_get_conversation(client, auth_headers):
    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "详情"}
    )
    conversation_id = created.json()["id"]

    response = await client.get(
        f"{API}/conversations/{conversation_id}", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["id"] == conversation_id


async def test_cross_user_conversation_not_found(client, create_user_token):
    alice_headers = await create_user_token("alice", "alice@example.com")
    created = await client.post(
        f"{API}/conversations", headers=alice_headers, json={"title": "私有"}
    )
    conversation_id = created.json()["id"]

    bob_headers = await create_user_token("bob", "bob@example.com")
    response = await client.get(
        f"{API}/conversations/{conversation_id}", headers=bob_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"


async def test_update_conversation_title(client, auth_headers):
    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "旧标题"}
    )
    conversation_id = created.json()["id"]

    response = await client.patch(
        f"{API}/conversations/{conversation_id}",
        headers=auth_headers,
        json={"title": "新标题"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "新标题"


async def test_delete_conversation(client, auth_headers):
    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "待删除"}
    )
    conversation_id = created.json()["id"]

    response = await client.delete(
        f"{API}/conversations/{conversation_id}", headers=auth_headers
    )
    assert response.status_code == 204

    follow_up = await client.get(
        f"{API}/conversations/{conversation_id}", headers=auth_headers
    )
    assert follow_up.status_code == 404


async def test_list_messages_empty(client, auth_headers):
    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "空会话"}
    )
    conversation_id = created.json()["id"]

    response = await client.get(
        f"{API}/conversations/{conversation_id}/messages", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1


async def test_messages_keyset_pagination(client, auth_headers, monkeypatch):
    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={}
    )
    conversation_id = created.json()["id"]
    await _seed_chat(client, auth_headers, conversation_id, monkeypatch, rounds=3)

    collected: list[int] = []
    cursor = 0
    for _ in range(10):
        response = await client.get(
            f"{API}/conversations/{conversation_id}/messages",
            headers=auth_headers,
            params={"cursor": cursor, "page_size": 2},
        )
        body = response.json()
        collected.extend(item["id"] for item in body["items"])
        if body["next_cursor"] is None:
            break
        cursor = body["next_cursor"]

    assert len(collected) == 6
    assert collected == sorted(collected)
    assert len(set(collected)) == 6
