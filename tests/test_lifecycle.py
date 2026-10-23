"""v3 WP2 优雅停机测试。"""

import asyncio

from app.core import lifecycle

API = "/api/v1"


async def test_drain_waits_for_completion():
    completed = asyncio.Event()

    async def work() -> None:
        await asyncio.sleep(0.01)
        completed.set()

    task = lifecycle.spawn_generation(work())
    cancelled = await lifecycle.drain_generations(1)

    assert cancelled == 0
    assert task.done()
    assert completed.is_set()
    await lifecycle.cancel_pending()


async def test_drain_cancels_after_timeout():
    task = lifecycle.spawn_generation(asyncio.sleep(5))

    cancelled = await lifecycle.drain_generations(0.01)

    assert cancelled == 1
    assert task.cancelled()
    await lifecycle.cancel_pending()


async def test_shutdown_rejects_new_chat(client, auth_headers):
    conversation = await client.post(f"{API}/conversations", headers=auth_headers, json={})
    conversation_id = conversation.json()["id"]

    lifecycle.begin_shutdown()
    try:
        response = await client.post(
            f"{API}/chat",
            headers=auth_headers,
            json={"conversation_id": conversation_id, "message": "hi"},
        )
    finally:
        lifecycle.reset_shutdown_state()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SERVER_SHUTTING_DOWN"
