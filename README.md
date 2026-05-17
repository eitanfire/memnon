# Memnon

**A local-first voice pipeline that turns iPhone recordings into structured knowledge.**

Speak into your phone. A formatted, AI-summarized Markdown note appears in your knowledge base within 60 seconds — transcribed on your own machine, no audio sent to the cloud.

```
iPhone mic  →  iCloud sync  →  whisper.cpp  →  OpenAI/Ollama  →  Obsidian note
```

> **Quick start:** `git clone` → `./setup.sh` → speak → note appears.

---

## Why This Exists

Voice is the fastest way to capture an idea. But recordings rot in Voice Memos.

Memnon is a reference architecture for a local AI knowledge pipeline — small enough to read in an afternoon, designed to be forked and extended.

- **Private by default** — audio is transcribed locally by whisper.cpp, never uploaded
- **No always-on server** — a macOS launchd agent wakes the script once per minute
- **No dependencies** — one Python file, pure stdlib
- **Composable** — swap the transcriber, the AI backend, the note format, the destination

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ iPhone                                                          │
│  Voice Memos app → iOS Shortcut → iCloud Drive/Voice Inbox/raw │
└────────────────────────────┬────────────────────────────────────┘
                             │ iCloud sync (~seconds)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Mac (launchd, every 60s)                                        │
│                                                                 │
│  raw/recording.m4a                                              │
│       │                                                         │
│       ▼                                                         │
│  ffmpeg → 16kHz WAV → whisper.cpp → transcript.txt             │
│                                          │                      │
│                                          ▼                      │
│                              OpenAI gpt-4o-mini (optional)      │
│                              • title                            │
│                              • summary                          │
│                              • action items                     │
│                              • tags                             │
│                                          │                      │
│                                          ▼                      │
│                              Obsidian Inbox/Voice/note.md       │
│                                                                 │
│  audio → processed/2026/05/recording.m4a                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Example Output

A 45-second voice note becomes:

```markdown
---
title: Refactor auth middleware before deploy
type: voice-note
status: inbox
created: 2026-05-15T09:22:11-06:00
suggested_tags: [backend, auth, deployment]
---

# Refactor auth middleware before deploy

## Summary
The current auth middleware doesn't handle token expiry gracefully under load.
The fix involves adding a refresh window before expiry rather than rejecting
the request outright. This should be done before the Friday deploy.

## Action Items
- [ ] Add 5-minute refresh window to token validation
- [ ] Write regression test for expired-token edge case
- [ ] Confirm with team before merging

## Transcript
So I was thinking about the auth middleware issue again — the token expiry
thing is going to bite us if we deploy Friday without fixing it...
```

---

## Prerequisites

- **macOS** with iCloud Drive enabled
- **Python 3.11+** — `python3 --version`
- **Homebrew** — [brew.sh](https://brew.sh)
- **ffmpeg** — `brew install ffmpeg`
- **whisper.cpp** — `brew install whisper-cpp && whisper-cpp --download-model base.en`
- **Obsidian** with a vault already created
- **iPhone** with the Memnon Shortcut installed (link below)

---

## Installation

```bash
git clone https://github.com/your-username/memnon.git
cd memnon
./setup.sh
```

`setup.sh` will:
1. Check all prerequisites and print install instructions for anything missing
2. Create the iCloud Drive folder structure
3. Generate `config.json` from the example template with your username pre-filled
4. Install and activate the launchd background agent

Then open `config.json` and set:
- `obsidian_inbox_dir` — path to your vault's inbox folder
- `ai.api_key` — your OpenAI key (or set `OPENAI_API_KEY` as an env var)

Run the validator to confirm everything is wired up:

```bash
python3 src/voice_pipeline.py validate --config config.json
```

---

## iPhone Shortcut

There are two options depending on your needs:

### Option A — Basic shortcut (one-tap install)

**→ [Add Memnon Shortcut to iPhone](https://www.icloud.com/shortcuts/bddfcee377de4cbdbad12deeb20228d6)**

Tap to record, tap to finish. Simple and works well for short notes. 

> **Limitation:** The screen must stay on during recording. If your phone locks mid-recording, the recording stops.

### Option B — Voice Memos automation (recommended)

This is the better long-term setup. It records correctly even with the screen locked, and fires automatically — no second tap needed.

**Setup (one time, ~2 minutes):**
1. Open **Shortcuts** → **Automation** tab → **+**
2. Choose **App** → select **Voice Memos** → set to **"Is Closed"**
3. Turn off **"Ask Before Running"**
4. Add two actions:
   - **Get Latest Voice Memo**
   - **Save File** → iCloud Drive → `Voice Inbox/raw` → disable "Ask Where to Save"

**Workflow:** Open Voice Memos → record → close the app → note appears in Obsidian within ~60 seconds. Screen can lock at any point during recording.

---

## Full Disk Access (Required for launchd)

The background agent needs permission to reach iCloud Drive:

**System Settings → Privacy & Security → Full Disk Access → Add your Python binary**

The exact path is printed by `setup.sh`. It will look like `/opt/homebrew/bin/python3.13`.

> This is required because macOS TCC blocks background processes from accessing iCloud Drive without explicit permission.

---

## AI Backends

AI is **disabled by default** — transcripts still land in Obsidian without it.

### OpenAI (recommended)

```json
"ai": {
  "enabled": true,
  "backend": "openai_http",
  "model": "gpt-4o-mini",
  "api_key": "sk-...",
  "temperature": 0.2,
  "max_tags": 5
}
```

Cost: roughly $0.001–0.003 per note with `gpt-4o-mini`.

### Ollama (fully local, free)

```bash
brew install ollama
ollama pull llama3
```

```json
"ai": {
  "enabled": true,
  "backend": "ollama_http",
  "model": "llama3",
  "base_url": "http://127.0.0.1:11434"
}
```

---

## Transcription

The default `command` backend pipes audio through `src/transcribe.sh`:
`ffmpeg` converts m4a/mp3 to 16kHz WAV → `whisper-cli` produces a transcript.

Swap in any command-line transcriber by editing `command_template` in `config.json`.

For testing without a real transcriber:

```json
"transcription": { "backend": "mock", "mock_transcript": "Test transcript." }
```

---

## GPT Handoff Lane

Drop audio into `Voice Inbox/gpt-now` instead of `raw` to trigger an urgent lane.

The pipeline transcribes it, generates the normal Obsidian note, and copies a pre-formatted GPT/Claude packet to your clipboard — ready to paste directly into a conversation.

---

## Commands

```bash
# Validate all config and dependencies
python3 src/voice_pipeline.py validate --config config.json

# Run one processing pass manually
python3 src/voice_pipeline.py watch --config config.json --once

# Process a single file
python3 src/voice_pipeline.py process-file /path/to/audio.m4a --config config.json

# Process a file through the GPT lane
python3 src/voice_pipeline.py process-file /path/to/audio.m4a --config config.json --lane gpt
```

---

## Ways to Extend This

The pipeline is intentionally minimal. Here are the natural next layers:

| Extension | What to build |
|-----------|---------------|
| **Topic routing** | Read the AI tags and move notes to different Obsidian folders automatically |
| **Slack/email ingestion** | Add a watcher for other ingest sources beyond the raw folder |
| **Meeting summaries** | Use a stereo-aware transcriber and add speaker diarization |
| **Team knowledge inbox** | Shared iCloud or Dropbox folder, shared Obsidian vault |
| **Semantic search** | Index note content into a local vector DB (e.g. ChromaDB) |
| **Auto-reminders** | Parse action items and push them to Apple Reminders or Todoist via AppleScript |
| **Web UI** | A simple Flask/FastAPI dashboard showing recent notes and pipeline status |
| **Mobile trigger** | Expand the iOS Shortcut to support different recording types routing to different lanes |
| **Local-only mode** | Replace OpenAI with a larger Ollama model for zero-cloud operation |

Pull requests welcome.

---

## Project Structure

| File | Purpose |
|------|---------|
| `src/voice_pipeline.py` | Main pipeline — pure Python stdlib, no pip install |
| `src/transcribe.sh` | ffmpeg + whisper-cli wrapper for m4a/mp3 input |
| `config.example.json` | Annotated config template |
| `setup.sh` | One-command install script |
| `templates/voice-note.md` | Obsidian note template |
| `templates/gpt-handoff.md` | GPT handoff packet template |
| `launchd/com.memnon.voice-pipeline.plist` | macOS launchd agent template |

---

## License

MIT
