"""v2 索引相关测试。"""

from app.models.message import Message


def test_messages_cover_index_registered():
    names = {index.name for index in Message.__table__.indexes}
    assert "ix_messages_conversation_id_id_token_count" in names
