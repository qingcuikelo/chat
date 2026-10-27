"""Request-ID / 安全响应头中间件（纯 ASGI，兼容 SSE 流式响应）。"""

import uuid

from app.core.config import settings
from app.core.context import request_id_var

SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    (
        "Content-Security-Policy",
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'; style-src 'unsafe-inline'",
    ),
)

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "img-src https://fastapi.tiangolo.com data:; "
    "font-src https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _security_headers(path: str) -> tuple[tuple[str, str], ...]:
    if path in DOCS_PATHS or path.startswith("/docs/"):
        return tuple(
            ("Content-Security-Policy", DOCS_CSP)
            if name == "Content-Security-Policy"
            else (name, value)
            for name, value in SECURITY_HEADERS
        )
    return SECURITY_HEADERS


class SecurityHeadersMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {name.lower() for name, _ in headers}
                for name, value in _security_headers(scope.get("path", "")):
                    if name.encode().lower() not in existing:
                        headers.append((name.encode(), value.encode()))
                if scope.get("scheme") == "https":
                    headers.append(
                        (
                            b"Strict-Transport-Security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestIDMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header_name = settings.REQUEST_ID_HEADER.lower().encode()
        incoming = dict(scope.get("headers") or []).get(header_name)
        request_id = incoming.decode("latin-1") if incoming else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        token = request_id_var.set(request_id)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((header_name, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)
