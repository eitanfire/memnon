#!/usr/bin/env python3
"""
Memnon MCP server — exposes voice notes to Claude and other MCP clients.

Replaces the TypeScript server in mcp/ with a pure-Python FastMCP implementation.
Suggested by Sagar Swami Rao Kulkarni (@sagarswamirao) after reviewing the project.

Run:
    python3 src/mcp_server.py

Configure in Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "memnon": {
          "command": "/path/to/memnon/.venv/bin/python3",
          "args": ["/path/to/memnon/src/mcp_server.py"]
        }
      }
    }
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

# ── Config ──────────────────────────────────────────────────────────────────

def _notes_dir() -> Path:
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("obsidian_inbox_dir"):
            return Path(config["obsidian_inbox_dir"])
    import os
    env = os.environ.get("MEMNON_NOTES_DIR", "")
    if env:
        return Path(env)
    raise RuntimeError(
        "Cannot find notes directory: set obsidian_inbox_dir in config.json "
        "or set MEMNON_NOTES_DIR env var."
    )


# ── Note loading ─────────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    """Split YAML frontmatter from body. Returns (frontmatter_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    yaml_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fm: Dict[str, Any] = {}
    for line in yaml_block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"')
        if val.startswith("[") and val.endswith("]"):
            items = [i.strip().strip('"') for i in val[1:-1].split(",") if i.strip()]
            fm[key] = items
        else:
            fm[key] = val
    return fm, body


def _load_notes() -> List[Dict[str, Any]]:
    notes_dir = _notes_dir()
    if not notes_dir.exists():
        return []
    notes = []
    for p in sorted(notes_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        notes.append({
            "filename": p.name,
            "filepath": str(p),
            "title": fm.get("title", p.stem),
            "created": fm.get("created", ""),
            "tags": fm.get("suggested_tags", []),
            "summary": fm.get("summary", ""),
            "workflow": fm.get("workflow", ""),
            "status": fm.get("status", ""),
            "body": body,
            "content": text,
        })
    return notes


def _extract_action_items(body: str) -> List[str]:
    return [
        re.sub(r"^\s*- \[ \]\s*", "", line).strip()
        for line in body.splitlines()
        if re.match(r"^\s*- \[ \]", line)
    ]


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ── MCP server ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    "memnon",
    instructions=(
        "Access Eitan's voice note knowledge base. "
        "Notes are structured Markdown files with frontmatter (title, tags, summary, action items). "
        "Use list_notes to browse, search_notes to find specific content, "
        "get_note for full detail, get_action_items for open to-dos, "
        "and update_note_lane to correct a misrouted note."
    ),
)


@mcp.tool()
def list_notes(limit: int = 10, since: str = "") -> str:
    """List recent voice notes newest-first with title, date, tags, and summary.

    Args:
        limit: Maximum number of notes to return (default 10).
        since: ISO date string — only return notes created on or after this date.
    """
    notes = _load_notes()
    if since:
        cutoff = _parse_date(since)
        if cutoff:
            notes = [n for n in notes if _parse_date(n["created"]) and _parse_date(n["created"]) >= cutoff]

    notes = list(reversed(notes))[:limit]
    result = [
        {
            "filename": n["filename"],
            "title": n["title"],
            "created": n["created"],
            "tags": n["tags"],
            "summary": n["summary"],
            "workflow": n["workflow"],
        }
        for n in notes
    ]
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def search_notes(query: str, tags: Optional[List[str]] = None) -> str:
    """Full-text search across note content, titles, and summaries.

    Args:
        query: Search string (case-insensitive).
        tags: Optional list of tags to filter by (any match).
    """
    q = query.lower()
    tag_filter = [t.lower() for t in (tags or [])]
    results = []

    for n in reversed(_load_notes()):
        note_tags = [t.lower() for t in n["tags"]]
        if tag_filter and not any(t in note_tags for t in tag_filter):
            continue
        searchable = " ".join([n["filename"], n["title"], n["summary"], n["body"]]).lower()
        if q not in searchable:
            continue
        matches = [
            line.strip()
            for line in n["body"].splitlines()
            if q in line.lower()
        ][:3]
        results.append({
            "filename": n["filename"],
            "title": n["title"],
            "created": n["created"],
            "tags": n["tags"],
            "summary": n["summary"],
            "matches": matches,
        })

    if not results:
        return f'No notes found matching "{query}".'
    return json.dumps(results, indent=2, ensure_ascii=False)


@mcp.tool()
def get_note(filename: str) -> str:
    """Return the full content of a specific note.

    Args:
        filename: Exact filename (e.g. '2026-05-29 173222 implementing-feedback-loops.md').
                  Use list_notes or search_notes to find filenames.
    """
    notes = _load_notes()
    note = next((n for n in notes if n["filename"] == filename), None)
    if not note:
        available = "\n".join(n["filename"] for n in notes[-10:])
        return f"Note not found: {filename}\n\nMost recent notes:\n{available}"
    return note["content"]


@mcp.tool()
def get_action_items() -> str:
    """Return all open action items (- [ ] …) across every note, newest-first."""
    all_items = []
    for n in reversed(_load_notes()):
        for item in _extract_action_items(n["body"]):
            if item.lower() == "none extracted":
                continue
            all_items.append({
                "action_item": item,
                "source_title": n["title"],
                "source_file": n["filename"],
                "created": n["created"],
            })
    if not all_items:
        return "No open action items found."
    return json.dumps(all_items, indent=2, ensure_ascii=False)


@mcp.tool()
def update_note_lane(filename: str, new_lane: str, reason: str = "") -> str:
    """Correct the lane/workflow of a misrouted note and update its frontmatter.

    This is the feedback loop Sagar suggested: if a note landed in the wrong lane
    (e.g. routed to 'professional' but it's actually 'reflect'), correct it here.
    The updated frontmatter becomes the source of truth for future re-tagging passes.

    Args:
        filename: The note to update.
        new_lane: The correct lane name (e.g. 'reflect', 'professional', 'build').
        reason: Optional note explaining why the lane was wrong (logged in frontmatter).
    """
    notes_dir = _notes_dir()
    path = notes_dir / filename
    if not path.exists():
        return f"Note not found: {filename}"

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return f"Note has no frontmatter — cannot update lane: {filename}"

    # Update the workflow: line in frontmatter
    end = text.find("\n---", 3)
    fm_block = text[3:end]
    body_section = text[end:]

    fm_block = re.sub(r"^workflow:.*$", f"workflow: {new_lane}", fm_block, flags=re.MULTILINE)
    fm_block = re.sub(r"^routing_reason:.*$", f"routing_reason: manual_correction", fm_block, flags=re.MULTILINE)

    if reason:
        if "lane_correction_note:" not in fm_block:
            fm_block = fm_block.rstrip() + f'\nlane_correction_note: "{reason}"\n'

    new_text = f"---{fm_block}{body_section}"
    path.write_text(new_text, encoding="utf-8")

    return f"Updated {filename}: workflow → {new_lane}" + (f" ({reason})" if reason else "")


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
