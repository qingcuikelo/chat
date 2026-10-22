"""认证路由。"""

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.rate_limit import check_rate_limit, get_client_ip
from app.core.security import decode_token
from app.dependencies.auth import bearer_scheme, get_current_user
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenPair,
    VerifyEmailRequest,
)
from app.schemas.user import UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _verify_result_page(success: bool, message: str) -> str:
    color = "#16a34a" if success else "#dc2626"
    title = "邮箱验证成功" if success else "邮箱验证失败"
    back_link = f'<p><a href="{settings.APP_BASE_URL}">返回应用</a></p>' if success else ""
    return (
        '<!doctype html><html lang="zh-CN"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title></head>"
        "<body style=\"font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;"
        "margin: 0; display: flex; min-height: 100vh; align-items: center;"
        'justify-content: center; background: #f8fafc;">'
        '<div style="text-align: center; padding: 32px;">'
        f'<h1 style="color: {color}; margin-bottom: 8px;">{title}</h1>'
        f'<p style="color: #334155;">{message}</p>'
        f"{back_link}</div></body></html>"
    )


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
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


@router.post("/verify-email")
async def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await check_rate_limit(
        f"rate_limit:verify:ip:{get_client_ip(request)}",
        settings.RATE_LIMIT_VERIFY_PER_MINUTE,
    )
    user = await AuthService(db).verify_email(payload.token)
    return {"email_verified": user.email_verified}


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """邮件链接直接点击的验证入口，返回 HTML 结果页。"""
    try:
        await check_rate_limit(
            f"rate_limit:verify:ip:{get_client_ip(request)}",
            settings.RATE_LIMIT_VERIFY_PER_MINUTE,
        )
        await AuthService(db).verify_email(token)
    except AppError as exc:
        status_code = exc.status_code if exc.status_code in (400, 429) else 400
        return HTMLResponse(_verify_result_page(False, exc.message), status_code=status_code)
    return HTMLResponse(_verify_result_page(True, "邮箱验证成功，现在可以返回应用登录。"))


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    payload: ResendVerificationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    email = str(payload.email).lower()
    await check_rate_limit(
        f"rate_limit:resend:account:{email}",
        settings.RATE_LIMIT_RESEND_PER_MINUTE,
    )
    await check_rate_limit(
        f"rate_limit:resend:ip:{get_client_ip(request)}",
        settings.RATE_LIMIT_RESEND_IP_PER_MINUTE,
    )
    await AuthService(db).resend_verification(email)
    return {"status": "accepted"}
