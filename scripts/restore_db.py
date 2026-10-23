"""MySQL 恢复入口：python -m scripts.restore_db <backup> <target_db> --yes。"""

import argparse
import sys

from scripts.db_tools import restore_database


def main() -> int:
    parser = argparse.ArgumentParser(description="恢复 ai_chatbot 数据库备份")
    parser.add_argument("backup_file", help=".sql.gz 或 .sql 备份文件")
    parser.add_argument("target_database", help="目标库名（建议使用临时库）")
    parser.add_argument("--yes", action="store_true", help="确认执行恢复")
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="允许目标库与源库相同（危险）",
    )
    args = parser.parse_args()

    try:
        counts = restore_database(
            args.backup_file,
            args.target_database,
            yes=args.yes,
            allow_overwrite=args.allow_overwrite,
        )
    except Exception as exc:
        print(f"恢复失败: {exc}", file=sys.stderr)
        return 1

    for table, count in counts.items():
        print(f"{table}={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
