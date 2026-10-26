"""v3 WP10 OpenAPI 契约测试。"""

from app.main import app


def test_openapi_info_and_tags():
    spec = app.openapi()

    assert spec["info"]["version"] == "0.3.0"
    assert "docs/API_CONTRACT.md" in spec["info"]["description"]
    tag_names = {tag["name"] for tag in spec["tags"]}
    assert {"auth", "users", "conversations", "chat", "system"} <= tag_names


def test_chat_sse_openapi_contract():
    spec = app.openapi()
    chat = spec["paths"]["/api/v1/chat"]["post"]
    content = chat["responses"]["200"]["content"]

    assert list(content) == ["text/event-stream"]
    variants = content["text/event-stream"]["schema"]["oneOf"]
    events = {variant["properties"]["event"]["enum"][0] for variant in variants}
    assert events == {"start", "delta", "done", "error"}
    assert chat["responses"]["429"]["content"]["application/json"]["schema"]["title"] == (
        "ErrorResponse"
    )
    assert "422" not in chat["responses"]
    assert "HTTPValidationError" not in spec["components"]["schemas"]


def test_resume_stream_openapi_contract():
    spec = app.openapi()
    resume = spec["paths"]["/api/v1/chat/stream/{request_id}"]["get"]
    params = {(param["name"], param["in"]) for param in resume["parameters"]}

    assert ("Last-Event-ID", "header") in params
    assert ("last_event_id", "query") in params
    assert list(resume["responses"]["200"]["content"]) == ["text/event-stream"]
