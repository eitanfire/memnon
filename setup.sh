#!/bin/bash
# Memnon setup script
# Run with no arguments for the interactive wizard (recommended).
# Pass --lite or --full to skip the mode prompt.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; RESET="\033[0m"
ok()   { echo -e "${GREEN}✓${RESET}  $*"; }
warn() { echo -e "${YELLOW}!${RESET}  $*"; }
fail() { echo -e "${RED}✗${RESET}  $*"; }

echo ""
echo "  Memnon — Setup"
echo "  ──────────────"
echo ""

# ── Python check ───────────────────────────────────────────────────────────────
if python3 --version 2>/dev/null | grep -qE "3\.(1[1-9]|[2-9][0-9])"; then
    PYTHON=$(which python3)
    ok "Python 3.11+ found at $PYTHON"
else
    fail "Python 3.11+ required.  Install via Homebrew: brew install python"
    exit 1
fi

# ── Hand off to Python wizard ──────────────────────────────────────────────────
echo ""
exec "$PYTHON" "$PROJECT_ROOT/src/voice_pipeline.py" setup "$@"
