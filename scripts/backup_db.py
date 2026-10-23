"""MySQL 备份入口：python -m scripts.backup_db。"""

import argparse
import os
import sys

from scripts.db_tools import backup_database


def main() -> int:
    parser = argparse.ArgumentParser(description="备份 ai_chatbot 数据库")
    parser.add_argument(
        "--keep-days",
        type=int,
        default=None,
        help="备份保留天数（默认读取 BACKUP_KEEP_DAYS，未设置则为 7）",
    )
    args = parser.parse_args()

    try:
        keep_days = (
            args.keep_days
            if args.keep_days is not None
            else int(os.getenv("BACKUP_KEEP_DAYS", "7"))
        )
    except ValueError:
        print("BACKUP_KEEP_DAYS 必须为整数", file=sys.stderr)
        return 1

    try:
        path = backup_database(keep_days=keep_days)
    except Exception as exc:
        print(f"备份失败: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
