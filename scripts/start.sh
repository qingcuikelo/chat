#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"

"$PYTHON" -m alembic upgrade head
exec "$PYTHON" -m uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WEB_CONCURRENCY"
