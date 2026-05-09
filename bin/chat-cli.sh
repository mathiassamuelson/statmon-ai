#!/usr/bin/env bash
# Drive automated conversations for LoRA training data generation.
# Usage: bin/chat-cli.sh prompts.txt -o output.jsonl [-c config.yaml] [-v]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

source .venv/bin/activate

exec python -m copilot.cli "$@"
