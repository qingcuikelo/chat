"""JWT 令牌签发 / 校验与密码哈希。"""

import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings

MAX_PASSWORD_BYTES = 72


def validate_password_length(password: str) -> str:
    """限制密码的 UTF-8 字节数，避免 bcrypt 超长输入异常或被静默截断。"""
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"密码不能超过 {MAX_PASSWORD_BYTES} 个 UTF-8 字节")
    return password


def hash_password(password: str) -> str:
    password = validate_password_length(password)
    if settings.PASSWORD_HASH_SCHEME.lower() == "argon2":
        from argon2 import PasswordHasher

        return PasswordHasher().hash(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        password = validate_password_length(password)
    except ValueError:
        return False

    if settings.PASSWORD_HASH_SCHEME.lower() == "argon2":
        from argon2 import PasswordHasher
        from argon2.exceptions import (
            InvalidHashError,
            VerificationError,
            VerifyMismatchError,
        )

        try:
            return PasswordHasher().verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _create_token(
    subject: int | str, token_type: str, expires_delta: timedelta, token_version: int = 0
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "type": token_type,
        "ver": token_version,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int | str, token_version: int = 0) -> str:
    return _create_token(
        user_id,
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        token_version,
    )


def create_refresh_token(user_id: int | str, token_version: int = 0) -> str:
    return _create_token(
        user_id,
        "refresh",
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        token_version,
    )


def create_password_reset_token(user_id: int | str) -> str:
    return _create_token(
        user_id,
        "password_reset",
        timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
    )


def create_email_verification_token(user_id: int | str, email: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "email_verify",
        "email": email,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(minutes=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def remaining_ttl(claims: dict) -> int:
    """返回令牌剩余有效秒数（用于黑名单 TTL），下限为 0。"""
    exp = claims.get("exp")
    if not exp:
        return 0
    return max(0, int(exp) - int(datetime.now(UTC).timestamp()))
