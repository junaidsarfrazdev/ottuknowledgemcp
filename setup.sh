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

# --- Workspace configuration -------------------------------------------------
ENV_FILE="$HERE/.env"
CURRENT_WS=""
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC2002
  CURRENT_WS="$(grep -E '^OTTU_WORKSPACE=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
fi

if [ -n "${OTTU_WORKSPACE:-}" ] && [ -z "$CURRENT_WS" ]; then
  CURRENT_WS="$OTTU_WORKSPACE"
fi

if [ -z "$CURRENT_WS" ]; then
  DEFAULT_WS="$HOME/ottu-workspace"
  if [ -t 0 ]; then
    read -r -p "▶ Where are your Ottu repos cloned? [$DEFAULT_WS]: " INPUT_WS || INPUT_WS=""
    CURRENT_WS="${INPUT_WS:-$DEFAULT_WS}"
  else
    CURRENT_WS="$DEFAULT_WS"
    echo "▶ Non-interactive — defaulting OTTU_WORKSPACE=$CURRENT_WS"
  fi
fi

# Expand ~ / $HOME manually since we read it raw
CURRENT_WS="${CURRENT_WS/#\~/$HOME}"

if [ ! -f "$ENV_FILE" ]; then
  cp "$HERE/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"
fi

# Update or append OTTU_WORKSPACE= in .env
if grep -qE '^OTTU_WORKSPACE=' "$ENV_FILE"; then
  # macOS/BSD sed compatible: write to temp, then replace
  tmp="$(mktemp)"
  awk -v ws="$CURRENT_WS" '/^OTTU_WORKSPACE=/{print "OTTU_WORKSPACE=" ws; next} {print}' "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
else
  echo "OTTU_WORKSPACE=$CURRENT_WS" >> "$ENV_FILE"
fi

echo "▶ OTTU_WORKSPACE = $CURRENT_WS  (saved to .env)"

if [ ! -d "$CURRENT_WS" ]; then
  echo "  ⚠ That directory doesn't exist yet. Create it and clone your repos:"
  echo "     mkdir -p \"$CURRENT_WS\" && cd \"$CURRENT_WS\""
  echo "     gh repo clone ottuco/checkout_sdk"
  echo "     # …and any others you care about"
fi

# --- Doctor ------------------------------------------------------------------
echo "▶ Running doctor..."
python cli.py doctor || true

echo
echo "✅ Setup complete."
echo "   Next:"
echo "     source venv/bin/activate"
echo "     python cli.py index"
echo "   Then wire Claude Code to server.py (see README)."
