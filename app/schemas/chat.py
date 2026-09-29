"""聊天与消息相关 Schema。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    conversation_id: int
    message: str = Field(min_length=1)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    conversation_id: int
    role: str
    content: str
    token_count: int | None = None
    created_at: datetime
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_")


class ChatStartEvent(BaseModel):
    request_id: str
    user_message_id: int


class ChatDeltaEvent(BaseModel):
    content: str


class ChatDoneEvent(BaseModel):
    assistant_message_id: int
    token_count: int
    finish_reason: str


class ChatErrorEvent(BaseModel):
    code: str
    message: str
