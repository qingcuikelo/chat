"""应用配置，统一从环境变量 / `.env` 读取。"""

from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-to-a-random-32-byte-secret"


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
    ALLOWED_HOSTS: str = "*"
    REQUEST_ID_HEADER: str = "X-Request-ID"
    SHUTDOWN_DRAIN_SECONDS: int = 30
    CHAT_MAX_MESSAGE_CHARS: int = 8000

    # ---- 数据库 ----
    DATABASE_URL: str = "sqlite+aiosqlite:///./aichat.db"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 1800
    DB_POOL_TIMEOUT: int = 30
    # ---- 只读从库（v3 WP8a，默认关闭）----
    DB_READ_URL: str = ""
    DB_REPLICA_ENABLED: bool = False
    DB_REPLICA_STICKY_SECONDS: int = 5

    # ---- 消息归档（v3 WP8b，默认关闭）----
    MESSAGE_ARCHIVE_ENABLED: bool = False
    MESSAGE_ARCHIVE_AFTER_DAYS: int = 180
    MESSAGE_ARCHIVE_BATCH_SIZE: int = 1000

    # ---- 写队列（v3 WP8c，默认关闭）----
    WRITE_QUEUE_ENABLED: bool = False
    WRITE_QUEUE_BATCH_SIZE: int = 64
    WRITE_QUEUE_BATCH_MAX_WAIT_MS: int = 200
    WRITE_QUEUE_MAX_ATTEMPTS: int = 5

    # ---- Redis ----
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 1800
    REDIS_REQUEST_TTL: int = 600
    REDIS_CACHE_TTL: int = 3600
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_MAX_CONNECTIONS: int = 50

    # ---- 认证与安全 ----
    JWT_SECRET_KEY: str = DEFAULT_JWT_SECRET
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PASSWORD_HASH_SCHEME: str = "bcrypt"
    AUTH_LOCKOUT_THRESHOLD: int = 10
    AUTH_LOCKOUT_MINUTES: int = 15
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30
    RATE_LIMIT_PER_MINUTE: int = 20
    RATE_LIMIT_LOGIN_PER_MINUTE: int = 5
    RATE_LIMIT_LOGIN_IP_PER_MINUTE: int = 30
    RATE_LIMIT_REGISTER_PER_MINUTE: int = 3
    RATE_LIMIT_REGISTER_IP_PER_MINUTE: int = 10
    RATE_LIMIT_REFRESH_PER_MINUTE: int = 10
    RATE_LIMIT_FAIL_MODE: str = "open"
    RATE_LIMIT_TRUST_FORWARDED_HEADERS: bool = False
    RATE_LIMIT_CLIENT_IP_HEADER: str = "X-Forwarded-For"
    TRUSTED_PROXIES: str = ""
    RATE_LIMIT_RESEND_PER_MINUTE: int = 3
    RATE_LIMIT_RESEND_IP_PER_MINUTE: int = 10
    RATE_LIMIT_VERIFY_PER_MINUTE: int = 10
    RATE_LIMIT_FORGOT_PER_MINUTE: int = 3
    RATE_LIMIT_FORGOT_IP_PER_MINUTE: int = 10
    RATE_LIMIT_RESET_IP_PER_MINUTE: int = 20

    # ---- 邮箱验证 ----
    EMAIL_VERIFICATION_REQUIRED: bool = True
    EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES: int = 1440
    APP_BASE_URL: str = "http://localhost:5173"
    # 后端对外地址（用于拼接验证邮件链接）
    PUBLIC_API_BASE_URL: str = "http://127.0.0.1:8000"

    # ---- 邮件（SMTP）----
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    MAIL_FROM: str = "no-reply@example.com"

    # ---- 后台任务（ARQ）----
    ARQ_REDIS_URL: str = ""
    SUMMARY_QUEUE_ENABLED: bool = True

    # ---- LLM ----
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 8192
    LLM_TIMEOUT: int = 120
    LLM_MAX_RETRIES: int = 2
    LLM_SYSTEM_PROMPT: str = "你是一个乐于助人的 AI 助手。"
    LLM_SUMMARY_MODEL: str = ""
    LLM_STREAM_INCLUDE_USAGE: bool = True

    # ---- 聊天上下文 ----
    CHAT_CONTEXT_MAX_MESSAGES: int = 40
    CHAT_CONTEXT_MAX_TOKENS: int = 65536
    CHAT_STREAM_HEARTBEAT: int = 15
    CHAT_MAX_CONCURRENT_PER_USER: int = 2
    CHAT_MAX_CONCURRENT_TOTAL: int = 100
    CHAT_INFLIGHT_TTL_SECONDS: int = 3600

    # ---- SSE delta 合批 ----
    SSE_BATCH_MIN_CHARS: int = 24
    SSE_BATCH_MAX_INTERVAL_MS: int = 50

    # ---- 聊天流缓冲 / 断点续传 ----
    CHAT_STREAM_TTL: int = 300
    CHAT_STREAM_POLL_MS: int = 50
    CHAT_STREAM_STALE_SECONDS: int = 30
    # 无订阅者宽限期（秒）；0 = 不放弃、继续生成到完成
    CHAT_STREAM_ABANDON_TIMEOUT: int = 0

    # ---- token 计数 ----
    # 留空使用 CJK 感知启发式估算；填入 tiktoken 编码名（如 cl100k_base）则用其精确计数
    TOKENIZER_ENCODING: str = ""

    # ---- 上下文滚动摘要 ----
    CHAT_SUMMARY_ENABLED: bool = True
    CHAT_SUMMARY_TRIGGER_RATIO: float = 0.8
    CHAT_SUMMARY_KEEP_RECENT_MESSAGES: int = 20
    CHAT_SUMMARY_MAX_TOKENS: int = 2048

    # ---- 用量与配额（v3 WP6）----
    USER_DAILY_TOKEN_QUOTA: int = 0
    QUOTA_ENFORCE: bool = False
    USAGE_RETENTION_DAYS: int = 365

    # ---- 日志 ----
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"
    # 可选：日志文件路径（留空只输出到 stdout）；带轮转
    LOG_FILE: str = ""
    LOG_FILE_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_FILE_BACKUP_COUNT: int = 5

    @property
    def cors_origins_list(self) -> list[str]:
        value = self.CORS_ORIGINS.strip()
        if value == "*":
            return ["*"]
        return [origin.strip() for origin in value.split(",") if origin.strip()]

    @property
    def allowed_hosts_list(self) -> list[str]:
        value = self.ALLOWED_HOSTS.strip()
        if not value:
            return ["*"]
        return [host.strip() for host in value.split(",") if host.strip()]

    @model_validator(mode="after")
    def _validate_production(self) -> Self:
        """生产环境启动期校验，避免默认密钥/不安全配置上线。"""
        if self.APP_ENV.lower() != "production":
            return self

        problems: list[str] = []
        if (
            not self.JWT_SECRET_KEY
            or self.JWT_SECRET_KEY == DEFAULT_JWT_SECRET
            or len(self.JWT_SECRET_KEY) < 32
        ):
            problems.append("JWT_SECRET_KEY 必须为至少 32 位的随机值")
        if self.DEBUG:
            problems.append("DEBUG 必须为 false")
        if not self.cors_origins_list or "*" in self.cors_origins_list:
            problems.append("CORS_ORIGINS 不能为空或 *")
        if "*" in self.allowed_hosts_list:
            problems.append("ALLOWED_HOSTS 不能为 *")
        if not self.LLM_API_KEY.strip() or self.LLM_API_KEY == "sk-your-key":
            problems.append("LLM_API_KEY 不能为空")
        if self.DATABASE_URL.startswith("sqlite"):
            problems.append("生产环境不能使用 SQLite")
        if self.DB_REPLICA_ENABLED and not self.DB_READ_URL.strip():
            problems.append("DB_REPLICA_ENABLED=true 时必须配置 DB_READ_URL")
        if self.EMAIL_VERIFICATION_REQUIRED and not self.SMTP_HOST.strip():
            problems.append("EMAIL_VERIFICATION_REQUIRED=true 时必须配置 SMTP_HOST")

        if problems:
            raise ValueError("生产配置校验失败：" + "；".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
