# Memnon MCP Server

Exposes your Memnon voice notes to AI models (Claude, etc.) via the [Model Context Protocol](https://modelcontextprotocol.io/).

## Tools

| Tool | Description |
|------|-------------|
| `list_notes` | List recent notes with metadata (title, date, tags, summary) |
| `search_notes` | Full-text search across all note content and titles |
| `get_note` | Retrieve the full content of a specific note by filename |
| `get_action_items` | Collect all unchecked `- [ ]` items across every note |

## Setup

### 1. Install dependencies

```bash
cd /Users/eitan/memnon/mcp
npm install
```

### 2. Build

```bash
npm run build
```

### 3. Connect to Claude Desktop

Add the following to your Claude Desktop config at `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memnon": {
      "command": "node",
      "args": ["/Users/eitan/memnon/mcp/dist/index.js"]
    }
  }
}
```

Then restart Claude Desktop. You should see "memnon" in the MCP servers list.

### 4. (Optional) Run without building

For development you can use `tsx` directly:

```json
{
  "mcpServers": {
    "memnon": {
      "command": "npx",
      "args": ["tsx", "/Users/eitan/memnon/mcp/src/index.ts"]
    }
  }
}
```

## Config resolution

The server reads `obsidian_inbox_dir` from `/Users/eitan/memnon/config.json`.  
If that file is missing, it falls back to the `MEMNON_NOTES_DIR` environment variable.

## Example queries

- *"What did I say about auth middleware last week?"* → `search_notes` with `query: "auth middleware"`
- *"Show me all my recent notes"* → `list_notes` with `limit: 20`
- *"What action items do I have outstanding?"* → `get_action_items`
- *"Read the note from May 13th about the 3pm recording"* → `get_note` with the filename
