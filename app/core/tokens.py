"""Token 计数。

默认使用 CJK 感知的保守估算（无外部依赖）；若配置了 `TOKENIZER_ENCODING`
且安装了 `tiktoken`，则改用其精确计数（对非 OpenAI 模型仍是近似）。
"""

import math
from functools import lru_cache

from app.core.config import settings

# 按 1 token/字 估算的码点区间
_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK 标点
    (0x3040, 0x30FF),  # 平假名 / 片假名
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xAC00, 0xD7AF),  # 谚文
    (0xFF00, 0xFFEF),  # 全角字符
    (0x20000, 0x2FA1F),  # CJK 扩展 B+
)


def _is_single_token_char(ch: str) -> bool:
    code = ord(ch)
    if code >= 0x1F000:  # emoji 等
        return True
    return any(low <= code <= high for low, high in _CJK_RANGES)


@lru_cache
def _encoder(encoding: str):
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding(encoding)
    except Exception:
        return None


def _heuristic_count(text: str) -> int:
    single = sum(1 for ch in text if _is_single_token_char(ch))
    other = len(text) - single
    return single + math.ceil(other / 4)


def count_tokens(text: str) -> int:
    """估算文本 token 数，空串为 0。"""
    if not text:
        return 0
    if settings.TOKENIZER_ENCODING:
        encoder = _encoder(settings.TOKENIZER_ENCODING)
        if encoder is not None:
            return len(encoder.encode(text))
    return _heuristic_count(text)
