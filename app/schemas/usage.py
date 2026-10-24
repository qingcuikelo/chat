"""用量与配额 Schema。"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class UsageDayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    day: date = Field(validation_alias="usage_date", serialization_alias="date")
    prompt_tokens: int
    completion_tokens: int
    request_count: int


class UsageRangeRead(BaseModel):
    from_date: date = Field(serialization_alias="from")
    to_date: date = Field(serialization_alias="to")
    items: list[UsageDayRead]
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int


class QuotaRead(BaseModel):
    day: date = Field(validation_alias="date", serialization_alias="date")
    limit: int
    used: int
    remaining: int | None
    enforced: bool
