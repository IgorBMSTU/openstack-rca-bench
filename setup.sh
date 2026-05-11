#!/usr/bin/env bash
set -euo pipefail

# OpenStack-RCA-Bench: one-command setup
# Usage: bash setup.sh

echo "=== OpenStack-RCA-Bench Setup ==="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found. Install Python 3.8+"
    exit 1
fi

# Create virtual environment if not exists
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate and install all dependencies
echo "Installing dependencies..."
source .venv/bin/activate
pip install -q -r requirements.txt
pip install -q -r llm_experiments/requirements.txt

# Dry-run test
echo ""
echo "Running dry-run test on 2 incidents..."
python -m llm_experiments.src.run_experiment --dry-run --limit 2

echo ""
echo "=== Setup complete ==="
echo "To run a real experiment:"
echo "  export QWEN_API_KEY='your-key'"
echo "  export QWEN_BASE_URL='your-endpoint/v1'"
echo "  python -m llm_experiments.src.run_experiment --provider qwen --model qwen3-coder-30b-a3b --prompt-strategy multi_agent"
