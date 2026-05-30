# Changelog

## v1.1.0 — 2026-05-29

### Added
- **FastMCP Python MCP server** (`src/mcp_server.py`) — replaces the TypeScript server; no Node.js required (h/t [@sagarswamirao](https://github.com/sagarswamirao))
- **`update_note_lane` MCP tool** — correct a misrouted note's lane; the feedback loop for re-tagging
- **`openai_whisper` transcription backend** — transcription via OpenAI API; no local whisper.cpp install needed
- **Interactive setup wizard** (`python3 src/voice_pipeline.py setup`) — Lite and Full modes; auto-detects Obsidian vault
- **GitHub Actions release workflow** — push a tag, get a `.dmg` on the releases page automatically
- **macOS `.dmg` distribution** — download, open, double-click `Setup Memnon.command`

### Changed
- `setup.sh` now delegates to the Python wizard after checking Python version
- README: added Privacy FAQ, whisper.cpp location section, FastMCP setup instructions
- `config.example.json`: documents both Lite and Full transcription modes

### Fixed
- Docstring no longer references Google Drive (was a leftover from an earlier prototype)

---

## v1.0.0 — 2026-05-15

Initial public release.

- iPhone → iCloud Drive → whisper.cpp → OpenAI/Ollama → Obsidian
- Multi-lane routing (voice label → keyword heuristics → default)
- TypeScript MCP server (list, search, get, action items)
- launchd background agent (macOS)
- GPT handoff lane
