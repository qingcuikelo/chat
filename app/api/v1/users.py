"""用户路由。"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, utcnow
from app.core.exceptions import InvalidRequestError
from app.core.security import remaining_ttl
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.usage import QuotaRead, UsageDayRead, UsageRangeRead
from app.schemas.user import UserRead, UserUpdate
from app.services.usage_service import UsageService
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.patch("/me", response_model=UserRead)
async def update_me(
    payload: UserUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    claims = request.state.access_token_claims
    return await UserService(db).update_me(
        current_user,
        payload,
        access_jti=claims.get("jti"),
        access_ttl=remaining_ttl(claims),
    )


@router.get("/me/usage", response_model=UsageRangeRead)
async def get_usage(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    end_date = to_date or utcnow().date()
    start_date = from_date or end_date - timedelta(days=6)
    if start_date > end_date:
        raise InvalidRequestError("开始日期不能晚于结束日期")
    if (end_date - start_date).days > 366:
        raise InvalidRequestError("查询范围不能超过 366 天")

    rows = await UsageService(db).usage_range(current_user.id, start_date, end_date)
    items = [UsageDayRead.model_validate(row) for row in rows]
    total_prompt = sum(item.prompt_tokens for item in items)
    total_completion = sum(item.completion_tokens for item in items)
    return UsageRangeRead(
        from_date=start_date,
        to_date=end_date,
        items=items,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_tokens=total_prompt + total_completion,
    )


@router.get("/me/quota", response_model=QuotaRead)
async def get_quota(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await UsageService(db).quota_state(current_user.id)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await UserService(db).delete_me(current_user)
