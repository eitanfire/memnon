"""Daily context pack: a bounded digest of the local vault for the daily brief.

The daily brief is generated in the cloud and cannot see the Obsidian vault on
this Mac. Once a day the voice-pipeline LaunchAgent calls run_if_due(), which
builds a small Markdown digest from an explicit include list in config.json
and writes it to Firestore at users/{uid}/context_pack/current. The brief
reads it read-only, as background.

Config (config.json, all keys optional except uid):

    "context_pack": {
      "enabled": true,
      "uid": "<firebase uid>",
      "firebase_project": "memnon-app",
      "vault_dir": "./memnon",
      "include": ["therapy_index", "therapy_sessions", "action_items",
                  "recent_notes", "people_household"],
      "build_after_hour": 3,
      "max_chars": 12000
    }

People and household are derived ONLY from sources where the listener is the
speaker or author (therapy notes, the Growls log, memos where they spoke the
lane label, typed entries). Ambient multi-speaker recordings never feed it,
because another speaker's family would be retold as the listener's.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_INCLUDE = ["therapy_index", "therapy_sessions", "action_items", "recent_notes", "people_household"]
DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "firebase_project": "memnon-app",
    "vault_dir": "./memnon",
    "include": DEFAULT_INCLUDE,
    "build_after_hour": 3,
    "retry_minutes": 30,
    "max_chars": 12000,
    "therapy_index_path": "Inbox/Voice/therapy-sessions-index.md",
    "therapy_index_section": "Cross-session analysis",
    "therapy_index_max_chars": 3500,
    "therapy_session_types": ["therapy-session"],
    "therapy_session_limit": 6,
    "therapy_session_max_chars": 600,
    "action_items_days": 30,
    "action_items_limit": 15,
    "recent_notes_days": 14,
    "recent_notes_limit": 30,
    "recent_note_max_chars": 200,
    "people_household_max_chars": 900,
    # One small call a day; accuracy matters more than cost here.
    "people_model": "gpt-4o",
    # Sources where the listener is the speaker or author.
    "speaker_types": ["therapy-session", "therapy-index", "therapy-practice"],
    "speaker_routing_reasons": ["voice_label", "manual-entry"],
    "speaker_dirs": ["Growls"],
    "speaker_source_max_chars": 14000,
}

SECTION_TITLES = {
    "recent_notes": "Recent notes (last {days} days, newest first)",
    "action_items": "Open action items (last {days} days)",
    "people_household": "People and household",
    "therapy_index": "Therapy: cross-session analysis",
    "therapy_sessions": "Therapy: per-session summaries",
}
# Recent material first, standing material as backdrop.
SECTION_ORDER = ["recent_notes", "action_items", "people_household", "therapy_index", "therapy_sessions"]
SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}


@dataclass
class VaultNote:
    path: Path
    rel: str
    meta: Dict[str, str]
    body: str
    created: datetime

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.path.stem

    @property
    def type(self) -> str:
        return self.meta.get("type", "")


@dataclass
class Section:
    id: str
    title: str
    lines: List[str] = field(default_factory=list)

    def render(self) -> str:
        return f"## {self.title}\n" + "\n".join(self.lines)


def settings(config: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULTS)
    merged.update(config.get("context_pack") or {})
    return merged


# ── Vault parsing ────────────────────────────────────────────────────────────

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        top = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not top:
            continue
        key, value = top.group(1), top.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].replace('\\"', '"')
        meta[key] = value
    return meta, text[match.end():]


def _parse_created(meta: Dict[str, str], path: Path) -> datetime:
    for key in ("created", "date", "processed"):
        raw = meta.get(key, "")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    stamp = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    if stamp:
        return datetime.strptime(stamp.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def load_vault(vault_dir: Path) -> List[VaultNote]:
    notes: List[VaultNote] = []
    for path in sorted(vault_dir.rglob("*.md")):
        rel_parts = path.relative_to(vault_dir).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        notes.append(VaultNote(path, "/".join(rel_parts), meta, body, _parse_created(meta, path)))
    notes.sort(key=lambda n: n.created, reverse=True)
    return notes


def markdown_section(body: str, heading: str, level: int = 2) -> str:
    """Text under `## heading` up to the next heading of the same or higher level."""
    hashes = "#" * level
    match = re.search(rf"^{hashes}\s+{re.escape(heading)}[^\n]*\n(.*?)(?=^#{{1,{level}}}\s|\Z)", body, re.S | re.M)
    return match.group(1).strip() if match else ""


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[: boundary + 1] if boundary > limit // 2 else cut.rsplit(" ", 1)[0] + "…").strip()


def _clip_block(text: str, limit: int) -> str:
    """Clip multi-line Markdown at a line boundary."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind("\n")].rstrip() + "\n…" if "\n" in cut else _clip(text, limit)


def note_summary(note: VaultNote, limit: int) -> str:
    for candidate in (
        note.meta.get("summary", ""),
        markdown_section(note.body, "Summary"),
        markdown_section(note.body, "Insight"),
        markdown_section(note.body, "Core Insight"),
    ):
        if candidate:
            return _clip(candidate, limit)
    first = next((ln for ln in note.body.splitlines() if ln.strip() and not ln.startswith("#")), "")
    return _clip(first, limit)


# ── Sections ─────────────────────────────────────────────────────────────────

def section_recent_notes(notes: List[VaultNote], cfg: Dict[str, Any], now: datetime) -> Section:
    days = int(cfg["recent_notes_days"])
    cutoff = now - timedelta(days=days)
    section = Section("recent_notes", SECTION_TITLES["recent_notes"].format(days=days))
    for note in notes:
        if note.created < cutoff or note.type == "therapy-index":
            continue
        summary = note_summary(note, int(cfg["recent_note_max_chars"]))
        section.lines.append(f"- {note.created.date().isoformat()} · {note.title}" + (f": {summary}" if summary else ""))
        if len(section.lines) >= int(cfg["recent_notes_limit"]):
            break
    return section


_OPEN_ITEM = re.compile(r"^\s*[-*]\s+\[ \]\s+(.+?)\s*$", re.M)


def section_action_items(notes: List[VaultNote], cfg: Dict[str, Any], now: datetime) -> Section:
    days = int(cfg["action_items_days"])
    cutoff = now - timedelta(days=days)
    section = Section("action_items", SECTION_TITLES["action_items"].format(days=days))
    seen: set[str] = set()
    for note in notes:
        if note.created < cutoff:
            continue
        for item in _OPEN_ITEM.findall(note.body):
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            section.lines.append(f"- {_clip(item, 160)} ({note.created.date().isoformat()}, {note.title})")
            if len(section.lines) >= int(cfg["action_items_limit"]):
                return section
    return section


def section_therapy_index(vault_dir: Path, cfg: Dict[str, Any]) -> Section:
    section = Section("therapy_index", SECTION_TITLES["therapy_index"])
    path = vault_dir / cfg["therapy_index_path"]
    if not path.exists():
        return section
    _meta, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    text = markdown_section(body, cfg["therapy_index_section"])
    if text:
        section.lines.append(_clip_block(text, int(cfg["therapy_index_max_chars"])))
    return section


def section_therapy_sessions(notes: List[VaultNote], cfg: Dict[str, Any]) -> Section:
    section = Section("therapy_sessions", SECTION_TITLES["therapy_sessions"])
    types = set(cfg["therapy_session_types"])
    for note in notes:
        if note.type not in types:
            continue
        summary = note_summary(note, int(cfg["therapy_session_max_chars"]))
        section.lines.append(f"- {note.created.date().isoformat()} · {note.title}" + (f": {summary}" if summary else ""))
        if len(section.lines) >= int(cfg["therapy_session_limit"]):
            break
    return section


def is_speaker_source(note: VaultNote, cfg: Dict[str, Any]) -> bool:
    """True only where the listener is the speaker or the author."""
    if note.type in set(cfg["speaker_types"]):
        return True
    if note.meta.get("routing_reason", "") in set(cfg["speaker_routing_reasons"]):
        return True
    top = note.rel.split("/", 1)[0]
    return top in set(cfg["speaker_dirs"])


def speaker_source_text(notes: List[VaultNote], cfg: Dict[str, Any]) -> str:
    """Summaries (not transcripts) from speaker-only sources, newest first, capped."""
    chunks: List[str] = []
    total = 0
    limit = int(cfg["speaker_source_max_chars"])
    for note in notes:
        if not is_speaker_source(note, cfg):
            continue
        if note.type == "therapy-index":
            text = markdown_section(note.body, cfg["therapy_index_section"]) or note.body
        elif note.rel.split("/", 1)[0] in set(cfg["speaker_dirs"]):
            text = note.body
        else:
            text = note_summary(note, 1200)
        chunk = f"[{note.created.date().isoformat()} · {note.title}]\n{_clip_block(text, 2500)}"
        if total + len(chunk) > limit:
            break
        chunks.append(chunk)
        total += len(chunk)
    return "\n\n".join(chunks)


PEOPLE_PROMPT = """The notes below are the listener's own: therapy notes about them, their own written log, and memos where they are the speaker.

List the people in the listener's household and close circle whose relationship to the listener the notes state in so many words.
Rules:
- Include a person only if the notes say what they are to the listener (e.g. "his wife", "my daughter", "his therapist Richard"). A name with no stated relationship is left out. Never default to "friend" or "colleague", and never infer a relationship from context.
- If the notes mention a household member without a name (e.g. "his wife"), include them with "name": "".
- If one person appears both by name and by role, list them once.
- Relationships are always to the listener (e.g. "wife", "daughter", "therapist").
- "note" is at most one short, plain, current fact (e.g. "at a Montessori school"). Never anything about therapy, mental health, feelings, or conflicts. Leave it empty when unsure.
- Use the native or preferred form of a name when the notes give one.
- At most 12 people.

Return strict JSON only: {{"people": [{{"name": "...", "relationship": "...", "note": "..."}}]}}

Notes:
{sources}
"""


def derive_people(source_text: str, ai: Dict[str, Any], post: Callable[[str, dict, dict], dict], model: str = "") -> List[str]:
    if not source_text.strip():
        return []
    payload = {
        "model": model or ai.get("model") or "gpt-4o-mini",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": PEOPLE_PROMPT.format(sources=source_text)}],
    }
    response = post(
        "https://api.openai.com/v1/chat/completions",
        payload,
        {"Authorization": f"Bearer {ai.get('api_key', '')}"},
    )
    content = response["choices"][0]["message"]["content"]
    people = json.loads(content).get("people") or []
    lines = []
    for person in people[:12]:
        name = str(person.get("name") or "").strip()
        relationship = str(person.get("relationship") or "").strip().lower()
        if not relationship:
            continue
        if "'s " in name or name.lower() in {relationship, f"his {relationship}", f"her {relationship}"}:
            name = ""  # a role, not a name
        note = str(person.get("note") or "").strip()
        if re.search(r"therap|session|anxi|depress|avoid|emotion|feeling", note, re.I):
            note = ""
        who = f"{name}: the listener's {relationship}" if name else f"The listener's {relationship} (name not stated in the notes)"
        lines.append(f"- {who}" + (f"; {note}" if note else ""))
    return lines


def _http_post_json(url: str, payload: dict, headers: dict, timeout: int = 60, method: str = "POST") -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# ── Pack ─────────────────────────────────────────────────────────────────────

def build_pack(
    config: Dict[str, Any],
    now: Optional[datetime] = None,
    people_lines: Optional[List[str]] = None,
    notes: Optional[List[VaultNote]] = None,
) -> Dict[str, Any]:
    """Build the digest. people_lines is passed in (derived separately) so
    building stays offline and testable."""
    cfg = settings(config)
    now = now or datetime.now(timezone.utc)
    vault_dir = _vault_dir(config, cfg)
    notes = notes if notes is not None else load_vault(vault_dir)
    include = [s for s in SECTION_ORDER if s in set(cfg["include"])]

    built: Dict[str, Section] = {}
    if "recent_notes" in include:
        built["recent_notes"] = section_recent_notes(notes, cfg, now)
    if "action_items" in include:
        built["action_items"] = section_action_items(notes, cfg, now)
    if "people_household" in include:
        section = Section("people_household", SECTION_TITLES["people_household"])
        text = _clip_block("\n".join(people_lines or []), int(cfg["people_household_max_chars"]))
        section.lines = text.splitlines() if text else []
        built["people_household"] = section
    if "therapy_index" in include:
        built["therapy_index"] = section_therapy_index(vault_dir, cfg)
    if "therapy_sessions" in include:
        built["therapy_sessions"] = section_therapy_sessions(notes, cfg)

    header = (
        f"# Context pack (built {now.date().isoformat()})\n"
        "The listener's own second brain. Recent notes lead; therapy material is backdrop."
    )
    max_chars = int(cfg["max_chars"])
    parts = [header]
    sources = []
    total = len(header)
    for section_id in include:
        section = built[section_id]
        if not section.lines:
            sources.append({"id": section_id, "label": section.title, "items": 0, "chars": 0, "omitted": "empty"})
            continue
        rendered = section.render()
        room = max_chars - total - 2
        if room < 200:
            sources.append({"id": section_id, "label": section.title, "items": 0, "chars": 0, "omitted": "size_cap"})
            continue
        if len(rendered) > room:
            rendered = _clip_block(rendered, room)
        parts.append(rendered)
        total += len(rendered) + 2
        sources.append({
            "id": section_id,
            "label": section.title,
            "items": sum(1 for line in rendered.splitlines()[1:] if line.startswith("- ")) or 1,
            "chars": len(rendered),
            "omitted": "",
        })
    markdown = "\n\n".join(parts)
    return {
        "markdown": markdown,
        "generated_at": now,
        "total_chars": len(markdown),
        "sources": sources,
        "version": 1,
    }


def _vault_dir(config: Dict[str, Any], cfg: Dict[str, Any]) -> Path:
    raw = Path(str(cfg["vault_dir"])).expanduser()
    if raw.is_absolute():
        return raw
    return (Path(config.get("_config_dir", ".")) / raw).resolve()


# ── Firestore (REST with the Mac's application-default credentials) ──────────

def _fs_value(value: Any) -> dict:
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, datetime):
        return {"timestampValue": value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: _fs_value(v) for k, v in value.items()}}}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_fs_value(v) for v in value]}}
    return {"stringValue": str(value)}


def firestore_document(pack: Dict[str, Any]) -> dict:
    return {"fields": {key: _fs_value(value) for key, value in pack.items()}}


def upload_pack(pack: Dict[str, Any], project: str, uid: str) -> None:
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/datastore"])
    credentials.refresh(google.auth.transport.requests.Request())
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/"
        f"users/{uid}/context_pack/current"
    )
    _http_post_json(
        url,
        firestore_document(pack),
        {"Authorization": f"Bearer {credentials.token}", "x-goog-user-project": project},
        method="PATCH",
    )


# ── Daily run ────────────────────────────────────────────────────────────────

def _state_path(config: Dict[str, Any]) -> Path:
    return Path(config.get("runtime_dir", "./runtime")) / "context_pack_state.json"


def _read_state(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def is_due(state: Dict[str, Any], cfg: Dict[str, Any], now_local: datetime) -> bool:
    if state.get("last_built_date") == now_local.date().isoformat():
        return False
    if now_local.hour < int(cfg["build_after_hour"]):
        return False
    last_attempt = state.get("last_attempt_at")
    if last_attempt:
        try:
            if now_local - datetime.fromisoformat(last_attempt) < timedelta(minutes=int(cfg["retry_minutes"])):
                return False
        except ValueError:
            pass
    return True


def build_and_upload(
    config: Dict[str, Any],
    state: Dict[str, Any],
    now: Optional[datetime] = None,
    post: Callable[..., dict] = _http_post_json,
    upload: Callable[[Dict[str, Any], str, str], None] = upload_pack,
    dry_run: bool = False,
) -> Dict[str, Any]:
    cfg = settings(config)
    now = now or datetime.now(timezone.utc)
    notes = load_vault(_vault_dir(config, cfg))

    people_lines: List[str] = []
    if "people_household" in cfg["include"]:
        source_text = speaker_source_text(notes, cfg)
        digest = hashlib.sha256((cfg["people_model"] + PEOPLE_PROMPT + source_text).encode("utf-8")).hexdigest()
        if state.get("people_source_hash") == digest and state.get("people_lines") is not None:
            people_lines = list(state["people_lines"])
        else:
            try:
                people_lines = derive_people(source_text, config.get("ai") or {}, post, model=cfg["people_model"])
                state["people_source_hash"] = digest
                state["people_lines"] = people_lines
            except Exception as exc:  # the rest of the pack still ships
                print(f"[context_pack] people and household unavailable: {exc}")
                people_lines = list(state.get("people_lines") or [])

    pack = build_pack(config, now=now, people_lines=people_lines, notes=notes)
    if not dry_run:
        upload(pack, cfg["firebase_project"], cfg["uid"])
    return pack


def run_if_due(config: Dict[str, Any], now_local: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Called every minute by the LaunchAgent; builds at most once a day.
    Never raises: a failed pack must not stop voice processing."""
    cfg = settings(config)
    if not cfg.get("enabled") or not cfg.get("uid"):
        return None
    now_local = now_local or datetime.now().astimezone()
    path = _state_path(config)
    state = _read_state(path)
    if not is_due(state, cfg, now_local):
        return None
    state["last_attempt_at"] = now_local.isoformat()
    try:
        pack = build_and_upload(config, state, now=now_local.astimezone(timezone.utc))
    except Exception as exc:
        state["last_error"] = str(exc)[:300]
        _write_state(path, state)
        print(f"[context_pack] build failed: {exc}")
        return None
    state["last_built_date"] = now_local.date().isoformat()
    state["last_error"] = ""
    state["last_sources"] = pack["sources"]
    _write_state(path, state)
    print(f"[context_pack] uploaded {pack['total_chars']} chars from {len(pack['sources'])} sources")
    return pack
