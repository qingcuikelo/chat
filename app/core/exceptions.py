"""统一业务异常与错误码（对应设计文档 3.8）。"""


class AppError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "服务器内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.status_code = status_code or type(self).status_code
        self.headers = headers
        super().__init__(self.message)


class InvalidRequestError(AppError):
    status_code = 400
    code = "INVALID_REQUEST"
    message = "请求参数校验失败"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    message = "未提供或无效的令牌"


class TokenExpiredError(AppError):
    status_code = 401
    code = "TOKEN_EXPIRED"
    message = "访问令牌已过期，需刷新"


class InvalidCredentialsError(AppError):
    status_code = 401
    code = "INVALID_CREDENTIALS"
    message = "用户名或密码错误"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "无权限访问该资源"


class EmailNotVerifiedError(AppError):
    status_code = 403
    code = "EMAIL_NOT_VERIFIED"
    message = "邮箱尚未验证，请先完成邮箱验证"


class ConversationNotFoundError(AppError):
    status_code = 404
    code = "CONVERSATION_NOT_FOUND"
    message = "会话不存在或不属于当前用户"


class StreamNotFoundError(AppError):
    status_code = 404
    code = "STREAM_NOT_FOUND"
    message = "聊天流不存在或已过期"


class UserNotFoundError(AppError):
    status_code = 404
    code = "USER_NOT_FOUND"
    message = "用户不存在"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "资源冲突"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "请求过于频繁，请稍后再试"


class AccountLockedError(AppError):
    status_code = 429
    code = "ACCOUNT_LOCKED"
    message = "登录失败次数过多，账号已临时锁定"


class QuotaExceededError(AppError):
    status_code = 429
    code = "QUOTA_EXCEEDED"
    message = "今日 token 配额已用尽"


class PasswordResetInvalidError(AppError):
    status_code = 400
    code = "PASSWORD_RESET_INVALID"
    message = "密码重置令牌无效或已使用"


class ServerShuttingDownError(AppError):
    status_code = 503
    code = "SERVER_SHUTTING_DOWN"
    message = "服务正在停机，请稍后重试"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    message = "服务暂时不可用，请稍后重试"


class LLMError(AppError):
    status_code = 502
    code = "LLM_ERROR"
    message = "上游模型不可用"
