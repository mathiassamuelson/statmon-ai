#!/bin/bash

echo "================================================"
echo "StatMon AI Aggregator - Environment Setup"
echo "================================================"

VENV_PATH="$HOME/statmon-ai"

#######################################################################
# SECTION 1: Python Environment
#######################################################################
echo ""
echo "[1/3] Setting up Python environment..."

apt install -y python3.12-venv

python_version=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "  Python version: $python_version"

if [ ! -f "$VENV_PATH/bin/activate" ]; then
    echo "  Creating virtual environment at $VENV_PATH..."
    rm -rf "$VENV_PATH"
    python3 -m venv "$VENV_PATH"
else
    echo "  Virtual environment already exists at $VENV_PATH"
fi

. "$VENV_PATH/bin/activate"

echo "  Upgrading pip..."
pip install --upgrade pip

#######################################################################
# SECTION 2: Install Dependencies
#######################################################################
echo ""
echo "[2/3] Installing dependencies..."

pip install -r requirements.txt

#######################################################################
# SECTION 3: Verify Setup
#######################################################################
echo ""
echo "[3/3] Verifying setup..."

chmod +x mock-cli/statmon

python3 -c "import statmon_mcp; print('  statmon-mcp: OK')"
python3 -c "import statmon_chat; print('  statmon-chat: OK')"

echo ""
echo "================================================"
echo "Setup complete!"
echo "================================================"
echo ""
echo "To activate the environment:"
echo "  source ~/statmon-ai/bin/activate"
