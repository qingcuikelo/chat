"""会话相关 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.chat import MessageRead


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    is_pinned: bool | None = None
    is_archived: bool | None = None

    @model_validator(mode="after")
    def _require_change(self) -> "ConversationUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个可更新字段")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title 不能为空")
        if not any(value is not None for value in (self.title, self.is_pinned, self.is_archived)):
            raise ValueError("至少提供一个可更新字段")
        return self


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str | None = None
    is_pinned: bool = False
    is_archived: bool = False
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConversationPage(BaseModel):
    items: list[ConversationRead]
    total: int
    page: int = 1
    page_size: int = 20
    next_cursor: str | None = None


class ConversationExport(BaseModel):
    conversation: ConversationRead
    messages: list[MessageRead]
    exported_at: datetime
