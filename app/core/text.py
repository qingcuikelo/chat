"""文本处理小工具。"""

TITLE_MAX_LENGTH = 30


def generate_title(text: str) -> str:
    """由首条用户消息生成会话标题（折叠空白并截断）。"""
    return " ".join(text.split())[:TITLE_MAX_LENGTH]
