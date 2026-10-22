"""会话与消息路由。"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_read_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.chat import MessageRead
from app.schemas.common import Page
from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
)
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ConversationService(db).create(current_user.id, payload)


@router.get("", response_model=Page[ConversationRead])
async def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_db),
):
    items, total = await ConversationService(db).list_conversations(
        current_user.id, page=page, page_size=page_size
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


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
    return await ConversationService(db).update_title(
        conversation_id, current_user.id, payload.title
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await ConversationService(db).delete(conversation_id, current_user.id)


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
