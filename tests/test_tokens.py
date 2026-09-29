"""token 计数单测。"""

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
