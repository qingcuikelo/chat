"""token 计数单测。"""

import pytest

from app.core import tokens
from app.core.config import settings
from app.core.tokens import count_tokens


def test_count_empty():
    assert count_tokens("") == 0


def test_count_ascii():
    assert count_tokens("hello world") == 3


def test_count_cjk():
    assert count_tokens("你好世界") == 4


def test_count_mixed():
    assert count_tokens("你好abcd") == 3


def test_count_emoji():
    assert count_tokens("😊") == 1


def test_count_tokens_with_tiktoken_when_configured(monkeypatch):
    pytest.importorskip("tiktoken")
    monkeypatch.setattr(settings, "TOKENIZER_ENCODING", "cl100k_base")
    tokens._encoder.cache_clear()
    try:
        assert count_tokens("hello world") > 0
    finally:
        tokens._encoder.cache_clear()
