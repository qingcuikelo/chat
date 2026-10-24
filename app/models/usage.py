"""用户按日 token 用量模型（v3 WP6）。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampTZ, utcnow


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint("user_id", "usage_date", name="uq_usage_daily_user_date"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    completion_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    request_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TimestampTZ,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )
