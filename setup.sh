#!/usr/bin/env bash
# One-shot setup for OttuKnowledgeMCP
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "▶ Creating venv..."
if [ ! -d venv ]; then
  python3 -m venv venv
fi
# shellcheck source=/dev/null
source venv/bin/activate

echo "▶ Installing Python dependencies..."
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "▶ Checking git-lfs..."
if command -v git-lfs >/dev/null 2>&1; then
  git lfs install >/dev/null
  echo "  git-lfs OK"
else
  echo "  ⚠ git-lfs not found. Install with: brew install git-lfs"
fi

echo "▶ Running doctor..."
python cli.py doctor || true

echo
echo "✅ Setup complete."
echo "   Next:"
echo "     source venv/bin/activate"
echo "     python cli.py index"
echo "   Then wire Claude Code to server.py (see README)."
