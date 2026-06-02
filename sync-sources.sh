#!/bin/bash
# Sync canonical sources.json to all destinations before deploying.
# Run this whenever data/sources.json is updated.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="$PROJECT_ROOT/data/sources.json"

if [ ! -f "$SRC" ]; then
  echo "✗  data/sources.json not found" >&2
  exit 1
fi

cp "$SRC" "$PROJECT_ROOT/public/sources.json"
cp "$SRC" "$PROJECT_ROOT/functions/sources.json"

echo "✓  sources.json synced to public/ and functions/"
