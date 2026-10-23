#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
exec "$PYTHON" -m scripts.restore_db "$@"
