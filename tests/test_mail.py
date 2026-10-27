"""邮件构建与发送降级测试。"""

import logging

from app.core.config import settings
from app.services.mail_service import MailService


def test_build_verification_email(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.com")
    message = MailService().build_verification_email("a@example.com", "tok")

    assert message["Subject"] == "请验证你的邮箱"
    assert "https://api.example.com/api/v1/auth/verify-email?token=tok" in message.get_content()


def test_build_password_reset_email(monkeypatch):
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com")
    message = MailService().build_password_reset_email("a@example.com", "tok")

    assert message["Subject"] == "重置你的密码"
    assert "https://app.example.com/reset-password?token=tok" in message.get_content()


async def test_send_without_smtp_returns_false(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")

    service = MailService()
    assert await service.send_verification_email("a@example.com", "tok") is False
    assert await service.send_password_reset_email("a@example.com", "tok") is False


async def test_development_logs_verification_link_without_smtp(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.com")

    with caplog.at_level(logging.WARNING):
        assert await MailService().send_verification_email("a@example.com", "dev-token") is False

    assert "https://api.example.com/api/v1/auth/verify-email?token=dev-token" in caplog.text
