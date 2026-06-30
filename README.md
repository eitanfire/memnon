# Memnon

<img src="memnon_logo.png" alt="Memnon logo" width="100%" />

**A local-first voice pipeline that turns iPhone recordings into structured knowledge.**

Speak into your phone. A formatted, AI-summarized Markdown note appears in your knowledge base within 60 seconds — transcribed on your own machine, no audio sent to the cloud.

```
iPhone mic  →  iCloud sync  →  whisper.cpp  →  OpenAI/Ollama  →  Obsidian note
                                                                        ↓
                                                               TypeScript MCP server
                                                                        ↓
                                                            Claude can query your notes
```

> **Quick start:** `git clone` → `./setup.sh` → speak → note appears.

---

## Why This Exists

Voice is the fastest way to capture an idea. But recordings rot in Voice Memos.

Memnon is a reference architecture for a local AI knowledge pipeline — small enough to read in an afternoon, designed to be forked and extended.

- **Private by default** — audio is transcribed locally by whisper.cpp, never uploaded
- **No always-on server** — a macOS launchd agent wakes the script once per minute
- **Readable** — one Python file, pure stdlib, no pip install
- **Composable** — swap the transcriber, the AI backend, the note format, the destination
- **AI-queryable** — a TypeScript MCP server lets Claude search and reason over your notes

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ iPhone                                                          │
│  Voice Memos app → iOS Shortcut → iCloud Drive/Voice Inbox/raw  │
└────────────────────────────┬────────────────────────────────────┘
                             │ iCloud sync (~seconds)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Mac (launchd, every 60s)                                        │
│                                                                 │
│  raw/recording.m4a                                              │
│       │                                                         │
│       ▼                                                         │
│  ffmpeg → 16kHz WAV → whisper.cpp → transcript.txt              │
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
│  audio → processed/2026/05/recording.m4a                        │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ TypeScript MCP Server (mcp/)                                    │
│                                                                 │
│  Exposes your note knowledge base to Claude and other           │
│  MCP-compatible AI tools                                        │
│                                                                 │
│  Tools: list_notes · search_notes · get_note · get_action_items │
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
- **Node.js 18+** — for the MCP server (optional)

---

## Workflows Local Dev

For the `workflows.html` vertical slice, use the dedicated local servers instead of `python3 -m http.server`.

Backend:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python functions/run_workflows_local.py
```

Static app with Firebase-style rewrites for `/workflows` and `/workflows/result/...`:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python scripts/run_workflows_static.py
```

Then open:

```text
http://127.0.0.1:8000/workflows
```

---

## Installation

### Option A — DMG (recommended for most users)

1. Download `Memnon-vX.X.X-macos.dmg` from the [latest release](https://github.com/eitanfire/memnon/releases/latest)
2. Open the disk image and drag **Memnon** to your Applications folder
3. **First launch:** right-click `Memnon.app` → **Open** → click **Open**
   - macOS blocks unsigned apps by default; right-clicking bypasses Gatekeeper once and remembers your choice
   - Or run: `xattr -dr com.apple.quarantine /Applications/Memnon.app`
4. Terminal opens and walks you through the setup wizard

### Option B — From source

```bash
git clone https://github.com/eitanfire/memnon.git
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

## iPhone Capture

The pipeline watches `Voice Inbox/raw` on iCloud Drive for new audio files. Getting recordings into that folder is the capture layer — and how you do it depends on your iOS version.

On iPhone 15 Pro and later (and all iPhone 16 models), the Action Button can be configured to open Voice Memos directly — replacing the old camera shortcut. Combined with Option A or B below, this gives you a one-press capture workflow: press the button, record, lock your screen, finish when ready. No dedicated hardware, no subscription. This is the workflow that makes Memnon a real alternative to devices like Plaud and Pocket AI.

### Option A — Manual handoff (works on all devices)

Record in Voice Memos (the screen can lock mid-recording), then share the file when done:

1. Open **Voice Memos** and record
2. Tap the recording → tap the **Share** icon
3. Choose **Save to Files** → iCloud Drive → `Voice Inbox` → `raw`

That's the only manual step. Once the file lands in `raw`, Memnon processes it automatically within ~60 seconds.

> **Tip:** Pin the `Voice Inbox/raw` folder in the Files app sidebar so the save destination is one tap away.

### Option B — Automated via Shortcuts (works on some iOS versions)

Apple's "Get Latest Voice Memo" Shortcuts action can automate the handoff entirely — no manual save needed. It fires when Voice Memos closes and saves the recording straight to `raw`.

> **Availability:** This action is not present on all devices. It varies by iOS version and is not documented by Apple. Try the setup below and check whether the action appears in your search results. If it does not, use Option A.

**Setup (one time, ~2 minutes):**
1. Open **Shortcuts** → **Automation** tab → **+**
2. Choose **App** → select **Voice Memos** → set to **"Is Closed"**
3. Turn off **"Ask Before Running"**
4. Add two actions:
   - **Get Latest Voice Memo**
   - **Save File** → iCloud Drive → `Voice Inbox/raw` → disable "Ask Where to Save"

**Workflow:** Open Voice Memos → record → close the app → note appears in Obsidian within ~60 seconds. Screen can lock at any point during recording.

### Option C — Basic shortcut (one-tap install)

**→ [Add Memnon Shortcut to iPhone](https://www.icloud.com/shortcuts/bddfcee377de4cbdbad12deeb20228d6)**

Tap to record, tap to finish. Saves directly to `Voice Inbox/raw` without opening Voice Memos.

> **Limitation:** The screen must stay on during recording. If your phone locks mid-recording, the recording stops.

### Option D — Google Drive (no iCloud required)

Best for educators already in Google Workspace. Recordings land in a Google Drive folder; `drive_poller.py` downloads them automatically into the pipeline — no iCloud, no AirDrop.

**Mac setup (one time, ~5 minutes):**

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) → New project → Enable **Google Drive API**
2. **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app** → Download JSON
3. Save the file as `google_client_secrets.json` next to `config.json`
4. Add to `config.json`:
   ```json
   "google_drive": {
     "enabled": true,
     "client_secrets_path": "./google_client_secrets.json",
     "watch_folder_id": "FOLDER_ID_FROM_DRIVE_URL",
     "poll_seconds": 60
   }
   ```
5. Authorize once (opens a browser tab):
   ```bash
   python3 src/drive_poller.py --config config.json --auth
   ```
6. Install the poller as a background agent:
   ```bash
   sed -e "s|__PYTHON__|$(which python3)|g" \
       -e "s|__PROJECT_ROOT__|$(pwd)|g" \
       -e "s|__CONFIG_PATH__|$(pwd)/config.json|g" \
       launchd/com.memnon.drive-poller.plist \
       > ~/Library/LaunchAgents/com.memnon.drive-poller.plist
   launchctl load ~/Library/LaunchAgents/com.memnon.drive-poller.plist
   ```

**iPhone capture:**
Record in Voice Memos → **Share → Save to Files → Google Drive → memnon-inbox**

Or use an iOS Shortcut with the **Save File** action pointed at your Drive folder.

> **Dependencies:** `pip install google-api-python-client google-auth-oauthlib`

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

Any model available via Ollama works. `llama3` is a solid default; `gemma3` is a great alternative — Google's open model, excellent at structured output like the JSON this pipeline expects.

```bash
brew install ollama
ollama pull llama3      # or: ollama pull gemma3
```

```json
"ai": {
  "enabled": true,
  "backend": "ollama_http",
  "model": "llama3",
  "base_url": "http://127.0.0.1:11434"
}
```

Swap `"model": "llama3"` for `"model": "gemma3"` to use Gemma locally with zero API costs.

---

## TypeScript MCP Server

A [FastMCP](https://gofastmcp.com) server (`src/mcp_server.py`) exposes your voice note knowledge base to Claude and other MCP-compatible AI tools — pure Python, no Node.js required.

> **Thanks to [@sagarswamirao](https://github.com/sagarswamirao) for suggesting FastMCP as a cleaner alternative to the original TypeScript server.**

Once connected, you can ask Claude things like:
- *"What action items do I have from this week's notes?"*
- *"Search my notes for anything about the auth middleware"*
- *"Summarize what I've been thinking about this week"*
- *"This note was misrouted — move it to the reflect lane"*

### Setup

```bash
# From the memnon project root:
python3 -m venv .venv
.venv/bin/pip install fastmcp
```

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memnon": {
      "command": "/path/to/memnon/.venv/bin/python3",
      "args": ["/path/to/memnon/src/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop. You'll see a hammer icon in the chat input confirming the tools are connected.

### Available Tools

| Tool | Description |
|------|-------------|
| `list_notes` | Recent notes with title, date, tags, summary |
| `search_notes` | Full-text search by keyword or tag |
| `get_note` | Full content of a specific note |
| `get_action_items` | All open action items across every note |
| `update_note_lane` | Correct a misrouted note's lane — the feedback loop for re-tagging |

> The original TypeScript server (`mcp/`) still works if you prefer Node.js. The Python server is now the recommended path.

---

## Transcription

The default `command` backend pipes audio through `src/transcribe.sh`:
`ffmpeg` converts m4a/mp3 to 16kHz WAV → `whisper-cli` produces a transcript.

Swap in any command-line transcriber by editing `command_template` in `config.json`.

For testing without a real transcriber:

```json
"transcription": { "backend": "mock", "mock_transcript": "Test transcript." }
```

### Where is whisper.cpp?

whisper.cpp is a **system dependency**, not part of the Memnon repo. It is installed via Homebrew:

```bash
brew install whisper-cpp
whisper-cpp --download-model base.en
```

After install, the relevant paths are:
- **Binary:** `/opt/homebrew/bin/whisper-cli`
- **Model:** `/opt/homebrew/share/whisper-cpp/models/ggml-base.en.bin`

The repo's `src/transcribe.sh` is a thin wrapper that calls `whisper-cli`. Think of whisper.cpp the same way you think of `ffmpeg` — a system tool the pipeline depends on, not something vendored into the repo.

---

## Privacy FAQ

### Is it really local if iCloud is part of the workflow?

iCloud is the **sync transport** between your iPhone and your Mac — it moves the file, it does not process it. The distinction that matters:

| Step | Where it happens |
|------|-----------------|
| File sync (iPhone → Mac) | iCloud (Apple infrastructure) |
| Transcription | Your Mac, via whisper.cpp — **fully local** |
| AI summarization (Ollama) | Your Mac — **fully local** |
| AI summarization (OpenAI) | OpenAI API — text only, audio never leaves |

**Your audio is never sent to a transcription service.** That's the privacy guarantee Memnon makes.

iCloud is also not required. The pipeline watches any folder you configure. If you'd rather use AirDrop, a USB mount, Dropbox, or a folder you populate manually, edit `raw_audio_dir` in `config.json` — the rest of the pipeline is unchanged.

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

Memnon has a deliberate product boundary:

**Core repo** — ingestion, transcription, note generation, lane routing, metadata, status. This is what belongs here and what pull requests should target.

**Companion tools** — things that sit on top of the pipeline without changing it. Build these in your own repo, pointed at the same Obsidian vault and `runtime/last-run.json`.

### Core extensions (pull requests welcome)

| Extension | What to build |
|-----------|---------------|
| **Speaker diarization** | Optional conversation mode using a stereo-aware transcriber — useful for interview and meeting lanes |
| **Slack/email ingestion** | Add a watcher for other ingest sources beyond the raw folder |
| **Team knowledge inbox** | Shared iCloud or Dropbox folder, shared Obsidian vault |
| **Local-only mode** | Replace OpenAI with a larger Ollama or Gemma model for zero-cloud operation |
| **Lane correction feedback** | Edited `workflow` frontmatter updates a local rules file the router consults next time |

### Companion tools (build in your own repo)

| Tool | What to build |
|------|---------------|
| **Menu bar app** | Reads `runtime/last-run.json` — shows a status indicator without touching the pipeline |
| **Auto-reminders** | Parse action items and push to Apple Reminders or Todoist via AppleScript |
| **Calendar events** | Extract dates from transcripts and create Calendar entries via AppleScript |
| **Mobile query UI** | Search your vault from iPhone — a companion service, not part of ingest |
| **Micro-podcast generator** | Reflect lane notes → script → ambient audio → personal podcast episode |
| **Semantic modeling** | Use [Malloy](https://www.malloydata.dev) to build a queryable semantic layer over your note metadata |
| **Web dashboard** | A TypeScript/Next.js view of recent notes and pipeline status, reads `last-run.json` |

> **The core principle:** the pipeline outputs plain Markdown to Obsidian. Every companion tool consumes from there. No tool should need to change the pipeline to add a feature — if it does, the boundary is in the wrong place.

---

## Project Structure

| File | Purpose |
|------|---------|
| `src/voice_pipeline.py` | Main pipeline — pure Python stdlib, no pip install |
| `src/transcribe.sh` | ffmpeg + whisper-cli wrapper for m4a/mp3 input |
| `mcp/src/index.ts` | TypeScript MCP server — exposes notes to Claude |
| `mcp/package.json` | MCP server dependencies |
| `config.example.json` | Annotated config template |
| `setup.sh` | One-command install script |
| `templates/voice-note.md` | Obsidian note template |
| `templates/gpt-handoff.md` | GPT handoff packet template |
| `launchd/com.memnon.voice-pipeline.plist` | macOS launchd agent template |

---

## Known Limitations (v1)

This is a working reference architecture, not production software. Known rough edges:

| Limitation | Notes |
|------------|-------|
| **macOS + iPhone only** | launchd and iCloud Drive are Apple-specific. Linux/Windows port would need a different watcher and sync mechanism. |
| **Single file pipeline** | `voice_pipeline.py` is ~1000 lines. A production version would split into modules. |
| **No retries** | If the OpenAI API call fails, the note lands in `failed/`. Re-drop the audio to retry. |
| **No structured logging** | Uses print/stderr. A production version would use Python's `logging` module. |
| **No test suite** | No automated tests. The `mock` backends exist specifically to make testing easier to add. |
| **Sequential processing** | Files are processed one at a time. Fine for personal use, slow for bulk imports. |
| **Tag scanning is O(n)** | `collect_preferred_tags()` rescans all vault files every run. Fine for small vaults. |

Pull requests welcome on any of these.

---

## License

MIT
