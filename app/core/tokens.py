"""Token 计数：CJK 感知的保守估算（无外部依赖）。

目标模型可能使用自家分词器，第三方分词器也只能近似。这里采用保守（偏高）
估算：CJK/全角/emoji 按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token，向上取整。
偏高估算会更早触发上下文压缩，不会撑爆模型窗口，是安全方向。
"""

import math

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


def count_tokens(text: str) -> int:
    """估算文本 token 数（保守偏高），空串为 0。"""
    if not text:
        return 0
    single = sum(1 for ch in text if _is_single_token_char(ch))
    other = len(text) - single
    return single + math.ceil(other / 4)
