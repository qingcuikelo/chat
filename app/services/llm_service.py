"""LLM 调用服务（兼容 OpenAI SDK）。"""

from collections.abc import AsyncIterator

from openai import APIError, AsyncOpenAI

from app.core.config import settings
from app.core.exceptions import LLMError


class LLMService:
    _client: AsyncOpenAI | None = None

    @classmethod
    def _get_client(cls) -> AsyncOpenAI:
        if cls._client is None:
            cls._client = AsyncOpenAI(
                api_key=settings.LLM_API_KEY or "not-set",
                base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
                max_retries=settings.LLM_MAX_RETRIES,
            )
        return cls._client

    async def stream_chat(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        client = self._get_client()
        try:
            stream = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                temperature=settings.LLM_TEMPERATURE,
                max_tokens=settings.LLM_MAX_TOKENS,
                stream=True,
            )
        except APIError as exc:
            raise LLMError() from exc

        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                content = getattr(chunk.choices[0].delta, "content", None)
                if content:
                    yield content
        except APIError as exc:
            raise LLMError() from exc
