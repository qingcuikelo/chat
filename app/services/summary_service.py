"""会话滚动摘要：阈值判断与压缩（供同步回退与后台 worker 复用）。"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tokens import count_tokens
from app.models.message import Message
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

EXCLUDED_STATUSES = ("failed", "interrupted")

SUMMARY_SYSTEM_PROMPT = (
    "你是对话摘要助手。请将给定的对话内容压缩为简洁的中文摘要，"
    "保留关键事实、用户偏好、重要决定与未解决的问题；"
    "不要遗漏关键信息，不要添加评论或解释。"
)


class SummaryService:
    def __init__(self, db: AsyncSession, llm: LLMService | None = None) -> None:
        self.db = db
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.llm = llm or LLMService()

    async def state(self, conversation_id: int) -> tuple[str | None, int]:
        conversation = await self.conversations.get(conversation_id)
        if conversation is None:
            return None, 0
        return conversation.summary, conversation.summary_until_message_id or 0

    @staticmethod
    def _filter_db_messages(messages: list[Message]) -> list[Message]:
        excluded = {
            (m.metadata_ or {}).get("answer_to")
            for m in messages
            if m.role == "assistant"
            and (m.metadata_ or {}).get("status") in EXCLUDED_STATUSES
        }
        result: list[Message] = []
        for m in messages:
            meta: dict[str, Any] = m.metadata_ or {}
            if m.role == "assistant" and meta.get("status") in EXCLUDED_STATUSES:
                continue
            if m.role == "user" and m.id in excluded:
                continue
            result.append(m)
        return result

    @staticmethod
    def _summary_messages(
        previous: str | None, messages: list[Message]
    ) -> list[dict[str, str]]:
        parts: list[str] = []
        if previous:
            parts.append(f"已有摘要：\n{previous}")
        parts.append("待压缩的对话：")
        parts.extend(f"{m.role}: {m.content}" for m in messages)
        return [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]

    async def should_compress(
        self, conversation_id: int, summary: str | None, watermark: int
    ) -> bool:
        used = (
            count_tokens(settings.LLM_SYSTEM_PROMPT)
            + count_tokens(summary or "")
            + await self.messages.sum_tokens_after(conversation_id, watermark)
        )
        threshold = (
            settings.CHAT_CONTEXT_MAX_TOKENS * settings.CHAT_SUMMARY_TRIGGER_RATIO
        )
        if used <= threshold:
            return False
        history = self._filter_db_messages(
            await self.messages.list_after(conversation_id, watermark)
        )
        return len(history) > settings.CHAT_SUMMARY_KEEP_RECENT_MESSAGES

    async def compress(self, conversation_id: int) -> tuple[str | None, int]:
        summary, watermark = await self.state(conversation_id)
        try:
            if not await self.should_compress(conversation_id, summary, watermark):
                return summary, watermark

            history = self._filter_db_messages(
                await self.messages.list_after(conversation_id, watermark)
            )
            to_summarize = history[: -settings.CHAT_SUMMARY_KEEP_RECENT_MESSAGES]

            new_summary = await self.llm.complete(
                self._summary_messages(summary, to_summarize),
                model=settings.LLM_SUMMARY_MODEL or None,
                max_tokens=settings.CHAT_SUMMARY_MAX_TOKENS,
            )
            if not new_summary:
                return summary, watermark

            new_watermark = to_summarize[-1].id
            await self.conversations.update_summary(
                conversation_id, new_summary, new_watermark
            )
            await self.db.commit()
            return new_summary, new_watermark
        except Exception:
            logger.warning(
                "生成会话摘要失败 conversation_id=%s", conversation_id, exc_info=True
            )
            await self.db.rollback()
            return summary, watermark
