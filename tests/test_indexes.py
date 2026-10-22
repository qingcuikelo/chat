"""v2 索引相关测试。"""

from app.models.conversation import Conversation
from app.models.message import Message


def test_messages_cover_index_registered():
    names = {index.name for index in Message.__table__.indexes}
    assert "ix_messages_conversation_id_id_token_count" in names
    assert "ix_messages_created_at_id" in names


def test_messages_archive_indexes_registered():
    from app.models.message_archive import MessageArchive

    names = {index.name for index in MessageArchive.__table__.indexes}
    assert "ix_messages_archive_conversation_id_id" in names
    assert "ix_messages_archive_created_at" in names


def test_conversation_composite_index_registered():
    names = {index.name for index in Conversation.__table__.indexes}
    assert "ix_conversation_user_id_updated_at_id" in names
