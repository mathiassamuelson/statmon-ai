#!/bin/bash

echo "================================================"
echo "StatMon AI Aggregator - Environment Setup"
echo "================================================"

VENV_PATH="$HOME/statmon-ai"

#######################################################################
# SECTION 1: System Dependencies
#######################################################################
echo ""
echo "[1/5] Installing system dependencies..."

sudo apt-get update
sudo apt-get install -y \
    build-essential \
    python3-dev

#######################################################################
# SECTION 2: Python Environment
#######################################################################
echo ""
echo "[2/5] Setting up Python environment..."

python_version=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "  Python version: $python_version"

if [ ! -d "$VENV_PATH" ]; then
    echo "  Creating virtual environment at $VENV_PATH..."
    python3 -m venv "$VENV_PATH"
else
    echo "  Virtual environment already exists at $VENV_PATH"
fi

. "$VENV_PATH/bin/activate"

echo "  Upgrading pip..."
pip install --upgrade pip

#######################################################################
# SECTION 3: PyTorch with CUDA
#######################################################################
echo ""
echo "[3/5] Installing PyTorch with CUDA 12.1..."

pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

#######################################################################
# SECTION 4: Core Dependencies
#######################################################################
echo ""
echo "[4/5] Installing core dependencies..."

pip install -r requirements.txt

#######################################################################
# SECTION 5: Directory Structure & Verification
#######################################################################
echo ""
echo "[5/5] Creating directory structure and verifying setup..."

mkdir -p src
mkdir -p tests
mkdir -p docs
mkdir -p scripts
mkdir -p configs

# Verify CUDA
echo ""
echo "  Verifying CUDA setup..."
python3 -c "import torch; print(f'  CUDA available: {torch.cuda.is_available()}'); print(f'  GPU count: {torch.cuda.device_count()}')"

echo ""
echo "================================================"
echo "Setup complete!"
echo "================================================"
echo ""
echo "To activate the environment:"
echo "  source ~/statmon-ai/bin/activate"
