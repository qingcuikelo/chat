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

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> str:
        """非流式补全（用于摘要等内部任务）。"""
        client = self._get_client()
        try:
            response = await client.chat.completions.create(
                model=model or settings.LLM_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                stream=False,
            )
        except APIError as exc:
            raise LLMError() from exc

        if not response.choices:
            return ""
        return response.choices[0].message.content or ""
