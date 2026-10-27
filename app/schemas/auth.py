"""认证相关 Schema。"""

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import validate_password_length


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_password_length(cls, value: str) -> str:
        return validate_password_length(value)


class LoginRequest(BaseModel):
    username: str | None = None
    email: EmailStr | None = None
    password: str = Field(min_length=1)

    @field_validator("password")
    @classmethod
    def _check_password_length(cls, value: str) -> str:
        return validate_password_length(value)


class RefreshRequest(BaseModel):
    refresh_token: str


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class PasswordForgotRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_password_length(cls, value: str) -> str:
        return validate_password_length(value)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
