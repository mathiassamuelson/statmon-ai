#!/usr/bin/env bash
# Start the StatMon AI chat web server.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

source .venv/bin/activate

exec uvicorn statmon_chat.app:app \
    --host "${HOST:-127.0.0.1}" \
    --port "${PORT:-8443}" \
    --app-dir statmon-chat \
    "$@"
