"""LLM 调用服务（兼容 OpenAI SDK）。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from openai import APIError, AsyncOpenAI

from app.core.config import settings
from app.core.exceptions import LLMError


@dataclass
class StreamChunk:
    """流式产出：delta 为增量文本，final 携带结束原因与用量。"""

    kind: Literal["delta", "final"]
    text: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


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

    async def _open_stream(self, messages: list[dict[str, str]], include_usage: bool):
        client = self._get_client()
        kwargs: dict[str, Any] = dict(
            model=settings.LLM_MODEL,
            messages=messages,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            stream=True,
        )
        if include_usage:
            kwargs["stream_options"] = {"include_usage": True}
        try:
            return await client.chat.completions.create(**kwargs)
        except APIError as exc:
            if not include_usage:
                raise LLMError() from exc
            # 上游不支持 stream_options 时降级重试（无 usage，但有 finish_reason）
            kwargs.pop("stream_options", None)
            try:
                return await client.chat.completions.create(**kwargs)
            except APIError as retry_exc:
                raise LLMError() from retry_exc

    async def stream_chat(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[StreamChunk]:
        stream = await self._open_stream(
            messages, include_usage=settings.LLM_STREAM_INCLUDE_USAGE
        )
        finish_reason: str | None = None
        usage: dict[str, Any] | None = None
        try:
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if choices:
                    choice = choices[0]
                    delta = getattr(choice, "delta", None)
                    content = getattr(delta, "content", None) if delta else None
                    if content:
                        yield StreamChunk("delta", text=content)
                    reason = getattr(choice, "finish_reason", None)
                    if reason:
                        finish_reason = reason
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = (
                        chunk_usage.model_dump()
                        if hasattr(chunk_usage, "model_dump")
                        else dict(chunk_usage)
                    )
        except APIError as exc:
            raise LLMError() from exc

        yield StreamChunk(
            "final", finish_reason=finish_reason or "stop", usage=usage
        )

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
