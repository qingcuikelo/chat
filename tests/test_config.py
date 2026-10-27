"""v3 WP2 生产配置校验测试。"""

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_JWT_SECRET, Settings

PRODUCTION = {
    "APP_ENV": "production",
    "JWT_SECRET_KEY": "x" * 40,
    "DEBUG": False,
    "CORS_ORIGINS": "https://app.example.com",
    "ALLOWED_HOSTS": "api.example.com",
    "LLM_API_KEY": "sk-test",
    "SMTP_HOST": "smtp.example.com",
    "DATABASE_URL": "mysql+aiomysql://user:pass@db:3306/ai_chatbot",
    "_env_file": None,
}


def test_production_settings_valid():
    settings = Settings(**PRODUCTION)
    assert settings.APP_ENV == "production"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("JWT_SECRET_KEY", "", "JWT_SECRET_KEY"),
        ("JWT_SECRET_KEY", DEFAULT_JWT_SECRET, "JWT_SECRET_KEY"),
        ("JWT_SECRET_KEY", "short", "JWT_SECRET_KEY"),
        ("DEBUG", True, "DEBUG"),
        ("CORS_ORIGINS", "*", "CORS_ORIGINS"),
        ("CORS_ORIGINS", "", "CORS_ORIGINS"),
        ("ALLOWED_HOSTS", "*", "ALLOWED_HOSTS"),
        ("LLM_API_KEY", "", "LLM_API_KEY"),
        ("SMTP_HOST", "", "SMTP_HOST"),
        ("DATABASE_URL", "sqlite+aiosqlite:///./a.db", "SQLite"),
    ],
)
def test_production_rejects_insecure_config(field, value, message):
    payload = {**PRODUCTION, field: value}
    with pytest.raises(ValidationError, match=message):
        Settings(**payload)


def test_production_replica_requires_read_url():
    payload = {**PRODUCTION, "DB_REPLICA_ENABLED": True, "DB_READ_URL": ""}
    with pytest.raises(ValidationError, match="DB_READ_URL"):
        Settings(**payload)


def test_development_allows_defaults():
    settings = Settings(APP_ENV="development", _env_file=None)
    assert settings.DEBUG is False
    assert settings.DATABASE_URL.startswith("sqlite")
