"""数据库迁移冒烟测试（v3 WP10 后续兼容性）。"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sqlite_migrations_fresh_upgrade(tmp_path):
    database = tmp_path / "migration.db"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    env = {**os.environ, "DATABASE_URL": url}

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert version == ("c1e2f3a4b5d6",)
    assert {"users", "conversation", "messages", "messages_archive", "usage_daily"} <= tables


def test_alembic_escapes_percent_in_database_url():
    url = "mysql+aiomysql://user:p%40ss@localhost:3306/chat"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
