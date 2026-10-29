"""用户相关 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.security import validate_password_length


class UserBase(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    email: EmailStr


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_password_length(cls, value: str) -> str:
        return validate_password_length(value)


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    current_password: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("password", "current_password")
    @classmethod
    def _check_password_length(cls, value: str | None) -> str | None:
        return validate_password_length(value) if value is not None else None

    @model_validator(mode="after")
    def _require_current_password(self) -> "UserUpdate":
        if self.password is not None and not self.current_password:
            raise ValueError("修改密码需要提供 current_password")
        return self


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pending_email: EmailStr | None = None
    email_verified: bool = False
    created_at: datetime
    updated_at: datetime
