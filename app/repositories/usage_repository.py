"""用户 token 用量数据访问层。"""

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.models.usage import UsageDaily


class UsageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert(
        self,
        *,
        user_id: int,
        usage_date: date,
        prompt_tokens: int,
        completion_tokens: int,
        request_count: int = 1,
    ) -> None:
        """按 (user_id, usage_date) 累加用量；同一事务内可重复调用。"""
        values = {
            "user_id": user_id,
            "usage_date": usage_date,
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "request_count": max(0, request_count),
            "updated_at": utcnow(),
        }
        dialect = self.db.get_bind().dialect.name
        statement: Any

        if dialect == "mysql":
            statement = mysql_insert(UsageDaily).values(**values)
            statement = statement.on_duplicate_key_update(
                prompt_tokens=UsageDaily.prompt_tokens + statement.inserted.prompt_tokens,
                completion_tokens=UsageDaily.completion_tokens
                + statement.inserted.completion_tokens,
                request_count=UsageDaily.request_count + statement.inserted.request_count,
                updated_at=statement.inserted.updated_at,
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(UsageDaily).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[UsageDaily.user_id, UsageDaily.usage_date],
                set_={
                    "prompt_tokens": UsageDaily.prompt_tokens + statement.excluded.prompt_tokens,
                    "completion_tokens": UsageDaily.completion_tokens
                    + statement.excluded.completion_tokens,
                    "request_count": UsageDaily.request_count + statement.excluded.request_count,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        else:
            raise RuntimeError(f"不支持的数据库方言: {dialect}")

        await self.db.execute(statement)

    async def list_range(self, user_id: int, start_date: date, end_date: date) -> list[UsageDaily]:
        result = await self.db.execute(
            select(UsageDaily)
            .where(
                UsageDaily.user_id == user_id,
                UsageDaily.usage_date >= start_date,
                UsageDaily.usage_date <= end_date,
            )
            .order_by(UsageDaily.usage_date.asc())
        )
        return list(result.scalars().all())

    async def totals_for_date(self, user_id: int, usage_date: date) -> tuple[int, int]:
        result = await self.db.execute(
            select(
                func.coalesce(func.sum(UsageDaily.prompt_tokens), 0),
                func.coalesce(func.sum(UsageDaily.completion_tokens), 0),
            ).where(
                UsageDaily.user_id == user_id,
                UsageDaily.usage_date == usage_date,
            )
        )
        prompt_tokens, completion_tokens = result.one()
        return int(prompt_tokens or 0), int(completion_tokens or 0)
