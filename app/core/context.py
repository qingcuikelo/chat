"""请求上下文变量（request_id / user_id / conversation_id）。"""

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")
conversation_id_var: ContextVar[str] = ContextVar("conversation_id", default="-")
