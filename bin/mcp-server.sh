#!/usr/bin/env bash
# Start the StatMon MCP server on a DNS node.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

source .venv/bin/activate

exec uvicorn statmon_mcp.server:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-8100}" \
    --app-dir statmon-mcp \
    "$@"
