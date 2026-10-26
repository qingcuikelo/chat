"""会话与消息路由。"""

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_read_db, utcnow
from app.dependencies.auth import get_current_user
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.chat import MessageRead
from app.schemas.common import Page
from app.schemas.conversation import (
    ConversationCreate,
    ConversationExport,
    ConversationPage,
    ConversationRead,
    ConversationUpdate,
)
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _export_headers(conversation_id: int, extension: str) -> dict[str, str]:
    return {
        "Content-Disposition": (
            f'attachment; filename="conversation-{conversation_id}.{extension}"'
        ),
        "Cache-Control": "no-store",
    }


def _render_markdown(
    conversation: Conversation, messages: list[Message], exported_at: datetime
) -> str:
    title = conversation.title or f"会话 {conversation.id}"
    lines = [
        f"# {title}",
        "",
        f"- 会话 ID: {conversation.id}",
        f"- 创建时间: {conversation.created_at.isoformat()}",
        f"- 更新时间: {conversation.updated_at.isoformat()}",
        f"- 置顶: {'是' if conversation.is_pinned else '否'}",
        f"- 归档: {'是' if conversation.is_archived else '否'}",
    ]
    if conversation.archived_at is not None:
        lines.append(f"- 归档时间: {conversation.archived_at.isoformat()}")
    lines.extend([f"- 导出时间: {exported_at.isoformat()}", "", "---", ""])

    for message in messages:
        lines.extend([f"## {message.role}", "", message.content, ""])
    return "\n".join(lines).rstrip() + "\n"


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ConversationService(db).create(current_user.id, payload)


@router.get("", response_model=ConversationPage)
async def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, min_length=1, max_length=255),
    archived: bool = Query(False),
    cursor: str | None = Query(None, min_length=1, max_length=512),
    limit: int | None = Query(None, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_db),
):
    items, total, next_cursor = await ConversationService(db).list_conversations(
        current_user.id,
        page=page,
        page_size=page_size,
        q=q,
        archived=archived,
        cursor=cursor,
        limit=limit,
    )
    return {
        "items": items,
        "total": total,
        "page": page if cursor is None else 1,
        "page_size": limit or page_size,
        "next_cursor": next_cursor,
    }


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_db),
):
    return await ConversationService(db).get_detail(conversation_id, current_user.id)


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: int,
    payload: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ConversationService(db).update(conversation_id, current_user.id, payload)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await ConversationService(db).delete(conversation_id, current_user.id)


@router.get(
    "/{conversation_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/json": {},
                "text/markdown": {},
            },
            "description": "会话导出文件",
        }
    },
)
async def export_conversation(
    conversation_id: int,
    export_format: Literal["json", "markdown"] = Query("json", alias="format"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_db),
):
    conversation, messages = await ConversationService(db).export(conversation_id, current_user.id)
    exported_at = utcnow()
    if export_format == "json":
        payload = ConversationExport(
            conversation=ConversationRead.model_validate(conversation),
            messages=[MessageRead.model_validate(message) for message in messages],
            exported_at=exported_at,
        )
        content = json.dumps(payload.model_dump(mode="json", by_alias=True), ensure_ascii=False)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers=_export_headers(conversation_id, "json"),
        )

    return Response(
        content=_render_markdown(conversation, messages, exported_at),
        media_type="text/markdown; charset=utf-8",
        headers=_export_headers(conversation_id, "md"),
    )


@router.get("/{conversation_id}/messages", response_model=Page[MessageRead])
async def list_messages(
    conversation_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    order: Literal["asc", "desc"] = Query("asc"),
    cursor: int | None = Query(None, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_db),
):
    items, total = await ConversationService(db).list_messages(
        conversation_id,
        current_user.id,
        page=page,
        page_size=page_size,
        order=order,
        cursor=cursor,
    )
    next_cursor = items[-1].id if cursor is not None and len(items) == page_size else None
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "next_cursor": next_cursor,
    }
