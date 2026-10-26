"""会话与消息接口测试。"""

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.message_archive import MessageArchive
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
        await client.post(f"{API}/conversations", headers=auth_headers, json={"title": f"c{index}"})

    response = await client.get(
        f"{API}/conversations", headers=auth_headers, params={"page": 1, "page_size": 2}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None

    second_page = (
        await client.get(
            f"{API}/conversations",
            headers=auth_headers,
            params={"page": 2, "page_size": 2},
        )
    ).json()
    assert second_page["page"] == 2
    assert len(second_page["items"]) == 1
    assert second_page["next_cursor"] is None


async def test_list_conversations_search_and_archive(client, auth_headers):
    alpha = (
        await client.post(
            f"{API}/conversations", headers=auth_headers, json={"title": "Alpha notes"}
        )
    ).json()
    beta = (
        await client.post(
            f"{API}/conversations", headers=auth_headers, json={"title": "Beta notes"}
        )
    ).json()

    archived = await client.patch(
        f"{API}/conversations/{beta['id']}",
        headers=auth_headers,
        json={"is_archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["is_archived"] is True
    assert archived.json()["archived_at"] is not None

    active = (
        await client.get(f"{API}/conversations", headers=auth_headers, params={"q": "notes"})
    ).json()
    assert active["total"] == 1
    assert [item["id"] for item in active["items"]] == [alpha["id"]]

    archived_list = (
        await client.get(
            f"{API}/conversations",
            headers=auth_headers,
            params={"archived": True},
        )
    ).json()
    assert archived_list["total"] == 1
    assert archived_list["items"][0]["id"] == beta["id"]

    unarchived = await client.patch(
        f"{API}/conversations/{beta['id']}",
        headers=auth_headers,
        json={"is_archived": False},
    )
    assert unarchived.json()["is_archived"] is False
    assert unarchived.json()["archived_at"] is None


async def test_pin_orders_conversations_without_changing_activity(
    client, auth_headers, session_factory, monkeypatch
):
    first = (
        await client.post(f"{API}/conversations", headers=auth_headers, json={"title": "A"})
    ).json()["id"]
    second = (
        await client.post(f"{API}/conversations", headers=auth_headers, json={"title": "B"})
    ).json()["id"]
    await _seed_chat(client, auth_headers, second, monkeypatch, rounds=1)

    async with session_factory() as session:
        before = await session.get(Conversation, first)

    pinned = await client.patch(
        f"{API}/conversations/{first}", headers=auth_headers, json={"is_pinned": True}
    )
    assert pinned.status_code == 200
    assert pinned.json()["is_pinned"] is True

    items = (await client.get(f"{API}/conversations", headers=auth_headers)).json()["items"]
    assert [item["id"] for item in items][:2] == [first, second]

    async with session_factory() as session:
        after = await session.get(Conversation, first)
    assert after is not None and before is not None
    assert after.updated_at == before.updated_at

    await client.patch(
        f"{API}/conversations/{first}", headers=auth_headers, json={"is_pinned": False}
    )
    items = (await client.get(f"{API}/conversations", headers=auth_headers)).json()["items"]
    assert items[0]["id"] == second


async def test_list_conversations_cursor_pagination(client, auth_headers):
    created = []
    for index in range(5):
        created.append(
            (
                await client.post(
                    f"{API}/conversations", headers=auth_headers, json={"title": f"c{index}"}
                )
            ).json()["id"]
        )
    pinned_id = created[2]
    await client.patch(
        f"{API}/conversations/{pinned_id}", headers=auth_headers, json={"is_pinned": True}
    )

    collected: list[int] = []
    cursor = None
    for _ in range(10):
        params = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = (
            await client.get(f"{API}/conversations", headers=auth_headers, params=params)
        ).json()
        collected.extend(item["id"] for item in body["items"])
        assert body["total"] == 5
        if body["next_cursor"] is None:
            break
        cursor = body["next_cursor"]

    assert collected[0] == pinned_id
    assert len(collected) == 5
    assert len(set(collected)) == 5


async def test_invalid_conversation_cursor_returns_400(client, auth_headers):
    response = await client.get(
        f"{API}/conversations", headers=auth_headers, params={"cursor": "not-a-cursor"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


async def test_get_conversation(client, auth_headers):
    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "详情"}
    )
    conversation_id = created.json()["id"]

    response = await client.get(f"{API}/conversations/{conversation_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == conversation_id


async def test_cross_user_conversation_not_found(client, create_user_token):
    alice_headers = await create_user_token("alice", "alice@example.com")
    created = await client.post(
        f"{API}/conversations", headers=alice_headers, json={"title": "私有"}
    )
    conversation_id = created.json()["id"]

    bob_headers = await create_user_token("bob", "bob@example.com")
    response = await client.get(f"{API}/conversations/{conversation_id}", headers=bob_headers)
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

    response = await client.delete(f"{API}/conversations/{conversation_id}", headers=auth_headers)
    assert response.status_code == 204

    follow_up = await client.get(f"{API}/conversations/{conversation_id}", headers=auth_headers)
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


async def test_export_includes_hot_and_archived_messages(client, auth_headers, session_factory):
    created = await client.post(
        f"{API}/conversations", headers=auth_headers, json={"title": "导出会话"}
    )
    conversation_id = created.json()["id"]
    async with session_factory() as session:
        session.add(
            Message(
                id=700,
                conversation_id=conversation_id,
                role="user",
                content="hot message",
                token_count=2,
                metadata_={"source": "hot"},
            )
        )
        session.add(
            MessageArchive(
                id=800,
                conversation_id=conversation_id,
                role="assistant",
                content="archived message",
                token_count=3,
                metadata_={"source": "archive"},
            )
        )
        await session.commit()

    response = await client.get(
        f"{API}/conversations/{conversation_id}/export",
        headers=auth_headers,
        params={"format": "json"},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="conversation-{conversation_id}.json"'
    )
    body = response.json()
    assert body["conversation"]["title"] == "导出会话"
    assert [item["content"] for item in body["messages"]] == [
        "hot message",
        "archived message",
    ]
    assert body["messages"][0]["metadata"] == {"source": "hot"}

    markdown = await client.get(
        f"{API}/conversations/{conversation_id}/export",
        headers=auth_headers,
        params={"format": "markdown"},
    )
    assert markdown.status_code == 200
    assert markdown.headers["content-disposition"] == (
        f'attachment; filename="conversation-{conversation_id}.md"'
    )
    assert markdown.headers["content-type"].startswith("text/markdown")
    text = markdown.text
    assert "# 导出会话" in text
    assert "## user" in text
    assert "hot message" in text
    assert "archived message" in text


async def test_export_cross_user_not_found(client, create_user_token):
    alice_headers = await create_user_token("alice", "alice@example.com")
    created = await client.post(
        f"{API}/conversations", headers=alice_headers, json={"title": "私有导出"}
    )
    conversation_id = created.json()["id"]

    bob_headers = await create_user_token("bob", "bob@example.com")
    response = await client.get(
        f"{API}/conversations/{conversation_id}/export", headers=bob_headers
    )
    assert response.status_code == 404


async def test_messages_keyset_pagination(client, auth_headers, monkeypatch):
    created = await client.post(f"{API}/conversations", headers=auth_headers, json={})
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
