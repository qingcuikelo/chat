"""MySQL 备份/恢复的共享工具。"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

DB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
SYSTEM_DATABASES = {"mysql", "information_schema", "performance_schema", "sys"}
BACKUP_GLOB = "ai_chatbot_*.sql.gz"
KNOWN_TABLES = ("users", "conversation", "messages", "messages_archive", "usage_daily")


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    username: str
    password: str
    database: str


def validate_database_name(name: str) -> str:
    if not DB_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"非法数据库名: {name!r}")
    if name.lower() in SYSTEM_DATABASES:
        raise ValueError(f"禁止使用系统数据库: {name!r}")
    return name


def load_db_config(database: str | None = None) -> DbConfig:
    """从应用配置解析 MySQL 连接信息；备份脚本只支持 MySQL。"""
    from app.core.config import settings

    url = make_url(settings.DATABASE_URL)
    if url.get_backend_name() != "mysql":
        raise ValueError("备份脚本仅支持 MySQL 的 DATABASE_URL")
    if not url.database:
        raise ValueError("DATABASE_URL 缺少数据库名")

    resolved = validate_database_name(database or url.database)
    return DbConfig(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        username=url.username or "",
        password=url.password or "",
        database=resolved,
    )


def _quote_option(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@contextmanager
def mysql_defaults_file(config: DbConfig) -> Iterator[str]:
    """生成 0600 的临时 MySQL 配置文件，避免密码出现在命令行。

    MySQL Windows 客户端无法打开非 ASCII 路径（用户目录可能含中文），
    因此默认放在项目 `tmp/` 下（已 gitignore）。
    """
    temp_dir = Path(os.getenv("AI_CHATBOT_TMP_DIR", "tmp"))
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="ai_chatbot_mysql_", suffix=".cnf", dir=temp_dir)
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("[client]\n")
            handle.write(f"host={_quote_option(config.host)}\n")
            handle.write(f"port={config.port}\n")
            handle.write(f"user={_quote_option(config.username)}\n")
            handle.write(f"password={_quote_option(config.password)}\n")
            handle.write("default-character-set=utf8mb4\n")
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def backup_filename(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return f"ai_chatbot_{moment:%Y%m%d_%H%M%S_%f}.sql.gz"


def prune_backups(directory: Path, keep_days: int) -> list[Path]:
    if keep_days <= 0:
        return []

    cutoff = time.time() - keep_days * 86400
    removed: list[Path] = []
    for path in directory.glob(BACKUP_GLOB):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def backup_database(
    *,
    backup_dir: Path | str | None = None,
    keep_days: int = 7,
    mysqldump: str | None = None,
) -> Path:
    config = load_db_config()
    out_dir = Path(backup_dir or os.getenv("BACKUP_DIR", "backups"))
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        out_dir.chmod(0o700)
    except OSError:
        pass
    out_path = out_dir / backup_filename()

    fd, temp_name = tempfile.mkstemp(prefix=".backup_", suffix=".tmp", dir=out_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with mysql_defaults_file(config) as defaults_file:
            command = [
                mysqldump or os.getenv("MYSQLDUMP", "mysqldump"),
                f"--defaults-extra-file={defaults_file}",
                "--single-transaction",
                "--quick",
                "--no-tablespaces",
                "--set-gtid-purged=OFF",
                "--default-character-set=utf8mb4",
                config.database,
            ]
            with gzip.open(temp_path, "wb", compresslevel=6) as gz:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if process.stdout is None:
                    raise RuntimeError("无法读取 mysqldump 输出")
                shutil.copyfileobj(process.stdout, gz)
                stderr = process.stderr.read() if process.stderr else b""
                returncode = process.wait()

            if returncode != 0:
                message = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"mysqldump 失败（{returncode}）：{message}")

        os.replace(temp_path, out_path)
        prune_backups(out_dir, keep_days)
        return out_path
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _run_mysql(mysql: str, defaults_file: str, args: list[str]) -> bytes:
    process = subprocess.run(
        [
            mysql,
            f"--defaults-extra-file={defaults_file}",
            "--default-character-set=utf8mb4",
            *args,
        ],
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"mysql 失败（{process.returncode}）：{message}")
    return process.stdout


def restore_database(
    backup_file: Path | str,
    target_database: str,
    *,
    yes: bool = False,
    allow_overwrite: bool = False,
    mysql: str | None = None,
) -> dict[str, int]:
    target = validate_database_name(target_database)
    confirmed = yes or os.getenv("CONFIRM_RESTORE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not confirmed:
        raise ValueError("恢复操作需要 --yes 或 CONFIRM_RESTORE=yes")

    source = load_db_config()
    if target == source.database and not allow_overwrite:
        raise ValueError("目标库与源库相同；如确认覆盖请加 --allow-overwrite")

    backup = Path(backup_file)
    if not backup.is_file():
        raise FileNotFoundError(f"备份文件不存在: {backup}")

    mysql_bin = mysql or os.getenv("MYSQL", "mysql")
    opener = gzip.open if backup.suffix == ".gz" else open

    with mysql_defaults_file(source) as defaults_file:
        existing = (
            _run_mysql(
                mysql_bin,
                defaults_file,
                [
                    "-N",
                    "-B",
                    "-e",
                    "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA "
                    f"WHERE SCHEMA_NAME = '{target}'",
                ],
            )
            .decode("utf-8", errors="replace")
            .strip()
        )
        if existing and not allow_overwrite:
            raise ValueError("目标数据库已存在；确认覆盖请显式添加 --allow-overwrite")

        _run_mysql(
            mysql_bin,
            defaults_file,
            [
                "-e",
                f"CREATE DATABASE IF NOT EXISTS `{target}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
            ],
        )

        with opener(backup, "rb") as source_file:
            process = subprocess.Popen(
                [
                    mysql_bin,
                    f"--defaults-extra-file={defaults_file}",
                    "--default-character-set=utf8mb4",
                    target,
                ],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if process.stdin is None:
                raise RuntimeError("无法写入 mysql 输入")
            shutil.copyfileobj(source_file, process.stdin)
            process.stdin.close()
            stderr = process.stderr.read() if process.stderr else b""
            returncode = process.wait()

        if returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"恢复导入失败（{returncode}）：{message}")

        tables = set(
            _run_mysql(
                mysql_bin,
                defaults_file,
                ["-N", "-B", "-e", f"SHOW TABLES FROM `{target}`"],
            )
            .decode("utf-8", errors="replace")
            .splitlines()
        )
        counts: dict[str, int] = {}
        for table in KNOWN_TABLES:
            if table not in tables:
                continue
            output = _run_mysql(
                mysql_bin,
                defaults_file,
                [
                    "-N",
                    "-B",
                    "-e",
                    f"SELECT COUNT(*) FROM `{target}`.`{table}`",
                ],
            )
            counts[table] = int(output.decode("utf-8").strip() or 0)

    return counts
