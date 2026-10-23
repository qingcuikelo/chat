"""v3 WP5 运维脚本单元测试。"""

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from app.core.config import settings
from app.workers.settings import WorkerSettings
from scripts import db_tools


def test_load_db_config_parses_mysql(monkeypatch):
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "mysql+aiomysql://user:p%40ss@db.example.com:3307/chat?charset=utf8mb4",
    )

    config = db_tools.load_db_config()

    assert config.host == "db.example.com"
    assert config.port == 3307
    assert config.username == "user"
    assert config.password == "p@ss"
    assert config.database == "chat"


def test_load_db_config_rejects_sqlite(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///./a.db")

    with pytest.raises(ValueError, match="MySQL"):
        db_tools.load_db_config()


def test_database_name_validation():
    assert db_tools.validate_database_name("ai_chatbot_restore_test") == ("ai_chatbot_restore_test")
    with pytest.raises(ValueError):
        db_tools.validate_database_name("bad-name;DROP")


def test_defaults_file_escapes_password(monkeypatch):
    config = db_tools.DbConfig(
        host="db",
        port=3306,
        username="user",
        password='a"b\\c',
        database="chat",
    )

    with db_tools.mysql_defaults_file(config) as path:
        content = Path(path).read_text(encoding="utf-8")
        assert Path(path).exists()

    assert 'password="a\\"b\\\\c"' in content
    assert not Path(path).exists()


def test_backup_filename_and_prune(tmp_path):
    moment = datetime(2026, 1, 2, 3, 4, 5, 678901)
    assert db_tools.backup_filename(moment) == "ai_chatbot_20260102_030405_678901.sql.gz"

    old = tmp_path / "ai_chatbot_old.sql.gz"
    new = tmp_path / "ai_chatbot_new.sql.gz"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    old_time = time.time() - 2 * 86400
    os.utime(old, (old_time, old_time))

    removed = db_tools.prune_backups(tmp_path, keep_days=1)

    assert removed == [old]
    assert not old.exists()
    assert new.exists()


def test_restore_requires_confirmation(monkeypatch):
    with pytest.raises(ValueError, match="--yes"):
        db_tools.restore_database("backup.sql.gz", "chat", yes=False)


def test_restore_refuses_source_database(monkeypatch):
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "mysql+aiomysql://user:pass@db:3306/chat?charset=utf8mb4",
    )

    with pytest.raises(ValueError, match="相同"):
        db_tools.restore_database("backup.sql.gz", "chat", yes=True)


def test_worker_health_check_config():
    assert WorkerSettings.health_check_key == "worker:heartbeat"
    assert WorkerSettings.health_check_interval == 30
