#!/bin/bash
# Memnon setup script
# Sets up folders, config, transcription, and the launchd background agent.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
USERNAME="$(whoami)"
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
VOICE_INBOX="$ICLOUD/Voice Inbox"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_NAME="com.memnon.voice-pipeline.plist"

# ── Colours ────────────────────────────────────────────────────────────────────
GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; RESET="\033[0m"
ok()   { echo -e "${GREEN}✓${RESET}  $*"; }
warn() { echo -e "${YELLOW}!${RESET}  $*"; }
fail() { echo -e "${RED}✗${RESET}  $*"; }

echo ""
echo "  Memnon — Local Voice Pipeline Setup"
echo "  ────────────────────────────────────"
echo ""

# ── 1. Prerequisites ───────────────────────────────────────────────────────────
echo "Checking prerequisites…"
MISSING=0

if python3 --version 2>/dev/null | grep -qE "3\.(1[1-9]|[2-9][0-9])"; then
    PYTHON=$(which python3)
    ok "Python 3.11+ found at $PYTHON"
else
    fail "Python 3.11+ required. Install via Homebrew: brew install python"
    MISSING=1
fi

if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg found"
else
    fail "ffmpeg not found. Install: brew install ffmpeg"
    MISSING=1
fi

if command -v whisper-cli &>/dev/null; then
    ok "whisper-cli found"
else
    fail "whisper-cli not found. Install: brew install whisper-cpp"
    MISSING=1
fi

MODEL_PATH="/opt/homebrew/share/whisper-cpp/models/ggml-base.en.bin"
if [ -f "$MODEL_PATH" ]; then
    ok "Whisper base.en model found"
else
    warn "Whisper model not found at $MODEL_PATH"
    echo "     Download it with: whisper-cpp --download-model base.en"
    MISSING=1
fi

if [ $MISSING -ne 0 ]; then
    echo ""
    echo "Fix the issues above and re-run setup.sh"
    exit 1
fi

echo ""

# ── 2. iCloud Drive folders ────────────────────────────────────────────────────
echo "Creating iCloud Drive folders…"
if [ ! -d "$ICLOUD" ]; then
    fail "iCloud Drive not found at $ICLOUD — make sure iCloud Drive is enabled in System Settings → Apple ID → iCloud"
    exit 1
fi

for DIR in "raw" "processed" "failed" "gpt-now"; do
    mkdir -p "$VOICE_INBOX/$DIR"
    ok "Voice Inbox/$DIR"
done

echo ""

# ── 3. Config ──────────────────────────────────────────────────────────────────
echo "Setting up config…"
if [ -f "$PROJECT_ROOT/config.json" ]; then
    warn "config.json already exists — skipping (edit it manually if needed)"
else
    sed "s|your-username|$USERNAME|g" "$PROJECT_ROOT/config.example.json" > "$PROJECT_ROOT/config.json"
    ok "config.json created from config.example.json"
    warn "Open config.json and set your Obsidian inbox path and OpenAI API key"
fi

echo ""

# ── 4. launchd agent ───────────────────────────────────────────────────────────
echo "Installing launchd agent…"
PYTHON_BIN=$(which python3)

# Prefer the real Homebrew binary over symlinks for TCC (Full Disk Access)
if [ -f "/opt/homebrew/bin/python3.13" ]; then
    PYTHON_BIN="/opt/homebrew/bin/python3.13"
elif [ -f "/opt/homebrew/bin/python3.12" ]; then
    PYTHON_BIN="/opt/homebrew/bin/python3.12"
elif [ -f "/opt/homebrew/bin/python3.11" ]; then
    PYTHON_BIN="/opt/homebrew/bin/python3.11"
fi

mkdir -p "$LAUNCH_AGENTS"
sed \
    -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
    -e "s|__PYTHON__|$PYTHON_BIN|g" \
    "$PROJECT_ROOT/launchd/$PLIST_NAME" \
    > "$LAUNCH_AGENTS/$PLIST_NAME"

launchctl unload "$LAUNCH_AGENTS/$PLIST_NAME" 2>/dev/null || true
launchctl load "$LAUNCH_AGENTS/$PLIST_NAME"
ok "launchd agent installed and running (every 60 seconds)"

echo ""

# ── 5. Validate ────────────────────────────────────────────────────────────────
echo "Running validation…"
"$PYTHON_BIN" "$PROJECT_ROOT/src/voice_pipeline.py" validate --config "$PROJECT_ROOT/config.json" || true

echo ""
echo "  ────────────────────────────────────"
echo "  Setup complete. Next steps:"
echo ""
echo "  1. Edit config.json:"
echo "     • Set obsidian_inbox_dir to your vault's Inbox/Voice folder"
echo "     • Set ai.api_key to your OpenAI key (or set OPENAI_API_KEY env var)"
echo ""
echo "  2. Grant Full Disk Access to Python in:"
echo "     System Settings → Privacy & Security → Full Disk Access"
echo "     Add: $PYTHON_BIN"
echo ""
echo "  3. Set up the iPhone Shortcut (one-tap install):"
echo "     → See README.md for the iCloud Shortcut link"
echo ""
echo "  4. Test the pipeline:"
echo "     $PYTHON_BIN src/voice_pipeline.py validate --config config.json"
echo ""
