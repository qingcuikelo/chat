"""SMTP 邮件发送与验证邮件投递。"""

import logging
from email.message import EmailMessage

import aiosmtplib

from app.core.config import settings
from app.core.security import create_email_verification_token

logger = logging.getLogger(__name__)


class MailService:
    @staticmethod
    def verification_link(token: str) -> str:
        return (
            f"{settings.PUBLIC_API_BASE_URL.rstrip('/')}"
            f"{settings.API_V1_PREFIX}/auth/verify-email?token={token}"
        )

    @staticmethod
    def password_reset_link(token: str) -> str:
        return f"{settings.APP_BASE_URL.rstrip('/')}/reset-password?token={token}"

    def build_verification_email(self, to_email: str, token: str) -> EmailMessage:
        link = self.verification_link(token)
        message = EmailMessage()
        message["From"] = settings.MAIL_FROM
        message["To"] = to_email
        message["Subject"] = "请验证你的邮箱"
        message.set_content(
            "你好，\n\n"
            f"请点击下面的链接完成邮箱验证（有效期 "
            f"{settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES} 分钟）：\n"
            f"{link}\n\n"
            "如果这不是你本人的操作，请忽略本邮件。"
        )
        return message

    def build_password_reset_email(self, to_email: str, token: str) -> EmailMessage:
        link = self.password_reset_link(token)
        message = EmailMessage()
        message["From"] = settings.MAIL_FROM
        message["To"] = to_email
        message["Subject"] = "重置你的密码"
        message.set_content(
            "你好，\n\n"
            f"请点击下面的链接重置密码（有效期 "
            f"{settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} 分钟）：\n"
            f"{link}\n\n"
            "如果这不是你本人的操作，请忽略本邮件。"
        )
        return message

    async def send_password_reset_email(self, to_email: str, token: str) -> bool:
        if not settings.SMTP_HOST:
            if settings.APP_ENV.lower() != "production":
                logger.warning(
                    "未配置 SMTP；开发环境密码重置链接: %s",
                    self.password_reset_link(token),
                )
            else:
                logger.warning("未配置 SMTP，跳过密码重置邮件发送 to=%s", to_email)
            return False
        message = self.build_password_reset_email(to_email, token)
        try:
            await aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or None,
                password=settings.SMTP_PASSWORD or None,
                start_tls=settings.SMTP_USE_TLS,
                use_tls=settings.SMTP_USE_SSL,
            )
            return True
        except Exception:
            logger.warning("密码重置邮件发送失败 to=%s", to_email, exc_info=True)
            return False

    async def send_verification_email(self, to_email: str, token: str) -> bool:
        if not settings.SMTP_HOST:
            if settings.APP_ENV.lower() != "production":
                logger.warning(
                    "未配置 SMTP；开发环境验证链接: %s",
                    self.verification_link(token),
                )
            else:
                logger.warning("未配置 SMTP，跳过验证邮件发送 to=%s", to_email)
            return False
        message = self.build_verification_email(to_email, token)
        try:
            await aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or None,
                password=settings.SMTP_PASSWORD or None,
                start_tls=settings.SMTP_USE_TLS,
                use_tls=settings.SMTP_USE_SSL,
            )
            return True
        except Exception:
            logger.warning("验证邮件发送失败 to=%s", to_email, exc_info=True)
            return False


async def dispatch_verification_email(user) -> None:
    """生成验证令牌并优先投递到 ARQ 队列，失败回退内联发送。"""
    token = create_email_verification_token(user.id, user.email)
    try:
        from app.workers.queue import enqueue_verification_email

        await enqueue_verification_email(user.email, token)
    except Exception:
        logger.warning("投递验证邮件任务失败，回退内联发送 to=%s", user.email, exc_info=True)
        await MailService().send_verification_email(user.email, token)


async def dispatch_password_reset_email(user, token: str) -> None:
    """优先投递密码重置邮件到 ARQ 队列，失败回退内联发送。"""
    try:
        from app.workers.queue import enqueue_password_reset_email

        await enqueue_password_reset_email(user.email, token)
    except Exception:
        logger.warning("投递密码重置邮件任务失败，回退内联发送 to=%s", user.email, exc_info=True)
        await MailService().send_password_reset_email(user.email, token)
