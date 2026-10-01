"""Request-ID 中间件（纯 ASGI，兼容 SSE 流式响应）。"""

import uuid

from app.core.config import settings
from app.core.context import request_id_var


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
