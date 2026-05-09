#!/bin/bash
# Environment setup for statmon-ai.
# Creates a Python venv and installs the chat application in editable mode.

set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install --upgrade pip
pip install -e ./copilot

echo
echo "Setup complete. Activate with: source .venv/bin/activate"
echo "Start the chat server with: bin/chat-server.sh"
