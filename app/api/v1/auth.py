"""认证路由。"""

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import check_rate_limit, get_client_ip
from app.core.security import decode_token
from app.dependencies.auth import bearer_scheme, get_current_user
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.schemas.user import UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=UserRead, status_code=status.HTTP_201_CREATED
)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    identifier = (payload.username or payload.email or "").strip().lower()
    if identifier:
        await check_rate_limit(
            f"rate_limit:register:account:{identifier}",
            settings.RATE_LIMIT_REGISTER_PER_MINUTE,
        )
    await check_rate_limit(
        f"rate_limit:register:ip:{get_client_ip(request)}",
        settings.RATE_LIMIT_REGISTER_IP_PER_MINUTE,
    )
    return await AuthService(db).register(payload)


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    identifier = (payload.username or payload.email or "").strip().lower()
    if identifier:
        await check_rate_limit(
            f"rate_limit:login:account:{identifier}",
            settings.RATE_LIMIT_LOGIN_PER_MINUTE,
        )
    await check_rate_limit(
        f"rate_limit:login:ip:{get_client_ip(request)}",
        settings.RATE_LIMIT_LOGIN_IP_PER_MINUTE,
    )
    return await AuthService(db).login(payload)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token)
        rate_key = str(claims.get("sub") or get_client_ip(request))
    except Exception:
        rate_key = get_client_ip(request)
    await check_rate_limit(
        f"rate_limit:refresh:user:{rate_key}",
        settings.RATE_LIMIT_REFRESH_PER_MINUTE,
    )
    return await AuthService(db).refresh(payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    access_token = credentials.credentials if credentials else ""
    refresh_token = payload.refresh_token if payload else None
    await AuthService(db).logout(access_token, refresh_token)
