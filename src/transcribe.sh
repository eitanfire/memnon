#!/bin/bash
# Wrapper: convert any audio format to 16kHz WAV, run whisper-cli, write {output_prefix}.txt
set -euo pipefail

# Use full paths so this works from launchd (minimal PATH)
FFMPEG=/opt/homebrew/bin/ffmpeg
WHISPER=/opt/homebrew/bin/whisper-cli

INPUT="$1"
OUTPUT_PREFIX="$2"
MODEL="${3:-/opt/homebrew/share/whisper-cpp/models/ggml-base.en.bin}"
LANGUAGE="${4:-en}"

WAV_TMP="$(mktemp /tmp/memnon_XXXXXX.wav)"
trap 'rm -f "$WAV_TMP"' EXIT

"$FFMPEG" -i "$INPUT" -ar 16000 -ac 1 -c:a pcm_s16le "$WAV_TMP" -y -loglevel error

"$WHISPER" \
  -m "$MODEL" \
  -f "$WAV_TMP" \
  -l "$LANGUAGE" \
  -nt \
  -otxt \
  -of "$OUTPUT_PREFIX"
