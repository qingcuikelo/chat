"""应用配置，统一从环境变量 / `.env` 读取。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 应用 ----
    APP_NAME: str = "AI ChatBot"
    APP_ENV: str = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    CORS_ORIGINS: str = "*"

    # ---- 数据库 ----
    DATABASE_URL: str = "sqlite+aiosqlite:///./aichat.db"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 1800

    # ---- Redis ----
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 1800
    REDIS_REQUEST_TTL: int = 600
    REDIS_CACHE_TTL: int = 3600

    # ---- 认证与安全 ----
    JWT_SECRET_KEY: str = "change-me-to-a-random-32-byte-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PASSWORD_HASH_SCHEME: str = "bcrypt"
    RATE_LIMIT_PER_MINUTE: int = 20

    # ---- LLM ----
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 2048
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 2
    LLM_SYSTEM_PROMPT: str = "你是一个乐于助人的 AI 助手。"

    # ---- 聊天上下文 ----
    CHAT_CONTEXT_MAX_MESSAGES: int = 20
    CHAT_CONTEXT_MAX_TOKENS: int = 8000
    CHAT_STREAM_HEARTBEAT: int = 15

    # ---- 日志 ----
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"

    @property
    def cors_origins_list(self) -> list[str]:
        value = self.CORS_ORIGINS.strip()
        if value == "*":
            return ["*"]
        return [origin.strip() for origin in value.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
