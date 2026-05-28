#!/usr/bin/env python3
"""Lean voice-note pipeline for Google Drive -> local transcription -> Obsidian."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_AUDIO_EXTENSIONS = [
    ".m4a",
    ".mp3",
    ".wav",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".webm",
    ".aac",
    ".flac",
]


@dataclass
class ProcessResult:
    status: str
    lane: str
    source_path: str
    archived_audio_path: Optional[str]
    note_path: Optional[str]
    metadata_path: Optional[str]
    gpt_packet_path: Optional[str]
    error: Optional[str]


@dataclass
class CandidateAudio:
    lane: str
    path: Path


def iso_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def slugify(value: str, fallback: str = "voice-note") -> str:
    lowered = value.lower().strip()
    lowered = re.sub(r"[^\w\s-]", "", lowered)
    lowered = re.sub(r"[\s_-]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or fallback


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def yaml_list(items: List[str], indent: int = 2) -> str:
    if not items:
        return (" " * indent) + "[]"
    prefix = " " * indent
    return "\n".join(f"{prefix}- {yaml_quote(item)}" for item in items)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def resolve_path(base_dir: Path, raw_value: Optional[str]) -> Optional[Path]:
    if not raw_value:
        return None
    expanded = Path(os.path.expandvars(os.path.expanduser(raw_value)))
    return expanded if expanded.is_absolute() else (base_dir / expanded).resolve()


def load_config(path: Path) -> Dict[str, Any]:
    config = read_json(path, default=None)
    if config is None:
        raise FileNotFoundError(f"Config file not found: {path}")

    base_dir = path.resolve().parent
    resolved = dict(config)
    resolved["_config_path"] = str(path.resolve())
    resolved["_config_dir"] = str(base_dir)

    runtime_dir = resolve_path(base_dir, resolved.get("runtime_dir", "./runtime"))
    if runtime_dir is None:
        raise ValueError("runtime_dir could not be resolved")

    resolved["runtime_dir"] = str(runtime_dir)
    resolved["state_file"] = str(
        resolve_path(base_dir, resolved.get("state_file", str(runtime_dir / "state.json")))
    )
    resolved["raw_audio_dir"] = str(resolve_path(base_dir, resolved["raw_audio_dir"]))
    resolved["archive_audio_dir"] = str(resolve_path(base_dir, resolved["archive_audio_dir"]))
    resolved["failed_audio_dir"] = str(resolve_path(base_dir, resolved["failed_audio_dir"]))
    resolved["obsidian_inbox_dir"] = str(resolve_path(base_dir, resolved["obsidian_inbox_dir"]))
    resolved["note_template_path"] = str(
        resolve_path(base_dir, resolved.get("note_template_path", "./templates/voice-note.md"))
    )
    tag_reference_dirs = []
    for raw_value in resolved.get("tag_reference_dirs", []):
        resolved_path = resolve_path(base_dir, raw_value)
        if resolved_path is not None:
            tag_reference_dirs.append(str(resolved_path))
    resolved["tag_reference_dirs"] = tag_reference_dirs

    resolved.setdefault("audio_extensions", DEFAULT_AUDIO_EXTENSIONS)
    resolved.setdefault("poll_seconds", 30)
    resolved.setdefault("min_stable_age_seconds", 90)
    resolved.setdefault("archive_subdirs_by_date", True)
    resolved.setdefault("min_transcript_words", 3)
    resolved.setdefault("min_audio_bytes", 4096)

    transcription = dict(resolved.get("transcription", {}))
    transcription.setdefault("backend", "whisper_cpp")
    transcription.setdefault("command", "whisper-cli")
    transcription.setdefault("language", "en")
    transcription.setdefault("extra_args", [])
    resolved["transcription"] = transcription

    ai = dict(resolved.get("ai", {}))
    ai.setdefault("enabled", False)
    ai.setdefault("backend", "ollama_http")
    ai.setdefault("model", "replace-with-installed-model")
    ai.setdefault("base_url", "http://127.0.0.1:11434")
    ai.setdefault("temperature", 0.2)
    ai.setdefault("timeout_seconds", 120)
    ai.setdefault("max_tags", 5)
    resolved["ai"] = ai

    gpt_handoff = dict(resolved.get("gpt_handoff", {}))
    gpt_handoff.setdefault("enabled", False)
    if gpt_handoff.get("audio_dir"):
        gpt_handoff["audio_dir"] = str(resolve_path(base_dir, gpt_handoff["audio_dir"]))
    if gpt_handoff.get("export_dir"):
        gpt_handoff["export_dir"] = str(resolve_path(base_dir, gpt_handoff["export_dir"]))
    else:
        gpt_handoff["export_dir"] = str((Path(resolved["runtime_dir"]) / "gpt-handoff").resolve())
    gpt_handoff["template_path"] = str(
        resolve_path(base_dir, gpt_handoff.get("template_path", "./templates/gpt-handoff.md"))
    )
    gpt_handoff.setdefault("copy_to_clipboard", False)
    gpt_handoff.setdefault(
        "instructions",
        "Continue the active conversation using the transcript as source of truth. "
        "Answer the user's latest question first, then add any useful follow-up or clarifying questions.",
    )
    resolved["gpt_handoff"] = gpt_handoff

    return resolved


def state_file(config: Dict[str, Any]) -> Path:
    return Path(config["state_file"])


def load_state(config: Dict[str, Any]) -> Dict[str, Any]:
    return read_json(state_file(config), default={"processed": {}})


def save_state(config: Dict[str, Any], state: Dict[str, Any]) -> None:
    write_json(state_file(config), state)


def ensure_runtime_dirs(config: Dict[str, Any]) -> None:
    for key in [
        "runtime_dir",
        "raw_audio_dir",
        "archive_audio_dir",
        "failed_audio_dir",
        "obsidian_inbox_dir",
    ]:
        Path(config[key]).mkdir(parents=True, exist_ok=True)
    (Path(config["runtime_dir"]) / "transcripts").mkdir(parents=True, exist_ok=True)
    (Path(config["runtime_dir"]) / "metadata").mkdir(parents=True, exist_ok=True)
    if config["gpt_handoff"].get("enabled"):
        Path(config["gpt_handoff"]["export_dir"]).mkdir(parents=True, exist_ok=True)
        if config["gpt_handoff"].get("audio_dir"):
            Path(config["gpt_handoff"]["audio_dir"]).mkdir(parents=True, exist_ok=True)


def ingest_dirs(config: Dict[str, Any]) -> List[CandidateAudio]:
    items = [CandidateAudio(lane="batch", path=Path(config["raw_audio_dir"]))]
    gpt_handoff = config["gpt_handoff"]
    if gpt_handoff.get("enabled") and gpt_handoff.get("audio_dir"):
        items.append(CandidateAudio(lane="gpt", path=Path(gpt_handoff["audio_dir"])))
    return items


def candidate_files(config: Dict[str, Any]) -> List[CandidateAudio]:
    cutoff = time.time() - int(config["min_stable_age_seconds"])
    extensions = {item.lower() for item in config["audio_extensions"]}
    files: List[CandidateAudio] = []
    for ingest in ingest_dirs(config):
        if not ingest.path.exists():
            continue
        for path in ingest.path.iterdir():
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix.lower() not in extensions:
                continue
            try:
                stats = path.stat()
            except FileNotFoundError:
                continue
            if stats.st_size <= 0:
                continue
            if stats.st_mtime > cutoff:
                continue
            files.append(CandidateAudio(lane=ingest.lane, path=path))
    return sorted(files, key=lambda item: item.path.stat().st_mtime)


def source_key(path: Path) -> str:
    stats = path.stat()
    payload = f"{path.resolve()}::{stats.st_size}::{stats.st_mtime_ns}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def content_hash(path: Path) -> str:
    """SHA-256 of file contents — used to detect duplicate exports."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def archive_destination(config: Dict[str, Any], source_path: Path, title: str = "") -> Path:
    """Return the destination path for an archived audio file.

    When *title* is provided (the AI-generated note title), the archived
    filename becomes a slug of that title — making the audio archive as
    readable as the Obsidian note. Falls back to the original filename.
    """
    archive_root = Path(config["archive_audio_dir"])
    if config.get("archive_subdirs_by_date", True):
        timestamp = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone()
        archive_root = archive_root / timestamp.strftime("%Y") / timestamp.strftime("%m")
    archive_root.mkdir(parents=True, exist_ok=True)
    if title:
        slug = slugify(title)
        filename = f"{slug}{source_path.suffix.lower()}"
    else:
        filename = source_path.name
    return unique_path(archive_root / filename)


def fail_destination(config: Dict[str, Any], source_path: Path) -> Path:
    failed_root = Path(config["failed_audio_dir"])
    failed_root.mkdir(parents=True, exist_ok=True)
    return unique_path(failed_root / source_path.name)


def render_template(template_text: str, values: Dict[str, str]) -> str:
    output = template_text
    for key, value in values.items():
        output = output.replace(f"{{{{{key}}}}}", value)
    return output


def format_action_items(items: List[str]) -> str:
    if not items:
        return "- [ ] None extracted"
    return "\n".join(f"- [ ] {item}" for item in items)


def format_bullet_tags(items: List[str]) -> str:
    if not items:
        return "- none"
    return "\n".join(f"- {item}" for item in items)


def format_numbered_list(items: List[str]) -> str:
    if not items:
        return "1. None extracted"
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def clean_transcript(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


# ---------------------------------------------------------------------------
# Workflow lane detection
# ---------------------------------------------------------------------------

# Built-in keyword fallbacks used when a lane has no explicit keywords in config.
# These are examples — define your own lanes and keywords in config.json instead.
BUILTIN_LANE_KEYWORDS: Dict[str, List[str]] = {}


def detect_workflow(transcript: str, config: Dict[str, Any]) -> tuple:
    """Return (workflow_name, routing_reason, cleaned_transcript).

    Priority:
    1. Voice label — first word(s) of transcript match a lane name or alias
    2. Keyword heuristics — configurable threshold of keyword hits
    3. Default fallback
    """
    lanes: Dict[str, Any] = config.get("lanes", {})
    text = transcript.strip()
    first_line = text.splitlines()[0] if text else ""

    # Build label → lane_name map from config, falling back to lane name itself
    label_map: Dict[str, str] = {}
    for lane_name, lane_cfg in lanes.items():
        label = lane_cfg.get("label", lane_name).lower()
        label_map[label] = lane_name
    # Always include builtin lane names as valid labels
    for lane_name in BUILTIN_LANE_KEYWORDS:
        label_map.setdefault(lane_name, lane_name)

    # 1. Voice label: "professional:" / "build —" / "reflect" at line start
    label_pattern = re.compile(
        r"^(" + "|".join(re.escape(k) for k in sorted(label_map, key=len, reverse=True)) + r")[\s:,.\-—]?",
        re.IGNORECASE,
    )
    match = label_pattern.match(first_line)
    if match:
        detected_label = match.group(1).lower()
        lane_name = label_map[detected_label]
        # Strip the label prefix from the transcript
        stripped = text[match.end():].lstrip(" \t:,.—-")
        if not stripped:
            stripped = text  # label only, keep full text
        return lane_name, "voice_label", stripped

    # 2. Keyword heuristics
    text_lower = text.lower()
    best_lane: Optional[str] = None
    best_count = 0
    for lane_name, lane_cfg in lanes.items():
        keywords = lane_cfg.get("keywords", BUILTIN_LANE_KEYWORDS.get(lane_name, []))
        threshold = int(lane_cfg.get("keyword_threshold", 2))
        matched = [kw for kw in keywords if kw.lower() in text_lower]
        if len(matched) >= threshold and len(matched) > best_count:
            best_lane = lane_name
            best_count = len(matched)

    # Also check builtin keywords for lanes not explicitly configured
    for lane_name, keywords in BUILTIN_LANE_KEYWORDS.items():
        if lane_name in lanes:
            continue  # already checked above
        matched = [kw for kw in keywords if kw.lower() in text_lower]
        if len(matched) >= 2 and len(matched) > best_count:
            best_lane = lane_name
            best_count = len(matched)

    if best_lane:
        return best_lane, f"keywords({best_count})", text

    return "default", "fallback", text


def parse_frontmatter_tags(text: str) -> List[str]:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return []

    tags: List[str] = []
    index = 1
    while index < len(lines):
        line = lines[index]
        if line.strip() == "---":
            break

        key_match = re.match(r"^(tags|suggested_tags):\s*(.*)$", line.strip())
        if key_match:
            remainder = key_match.group(2).strip()
            if remainder.startswith("[") and remainder.endswith("]"):
                raw_items = [item.strip().strip("\"'") for item in remainder[1:-1].split(",")]
                tags.extend(item for item in raw_items if item)
            elif remainder:
                raw_items = [item.strip().strip("\"'") for item in remainder.split(",")]
                tags.extend(item for item in raw_items if item)
            else:
                lookahead = index + 1
                while lookahead < len(lines):
                    candidate = lines[lookahead]
                    if not candidate.startswith("  - ") and not candidate.startswith("- "):
                        break
                    tags.append(candidate.split("-", 1)[1].strip().strip("\"'"))
                    lookahead += 1
                index = lookahead - 1

        index += 1

    return [slugify(item, fallback="") for item in tags if slugify(item, fallback="")]


def collect_preferred_tags(config: Dict[str, Any], limit: int = 25, min_count: int = 3) -> List[str]:
    """Return the most-used tags from the Obsidian vault.

    Only tags that appear in at least *min_count* notes are included — this
    filters out one-off or accidental tags that would otherwise pollute the
    AI suggestion context.
    """
    counts: Dict[str, int] = {}
    hashtag_pattern = re.compile(r"(?<!\w)#([A-Za-z0-9][A-Za-z0-9/_-]*)")

    for raw_dir in config.get("tag_reference_dirs", []):
        directory = Path(raw_dir)
        if not directory.exists():
            continue
        for note_path in directory.rglob("*.md"):
            try:
                text = note_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            for tag in parse_frontmatter_tags(text):
                counts[tag] = counts.get(tag, 0) + 1

            for match in hashtag_pattern.findall(text):
                tag = slugify(match, fallback="")
                if tag:
                    counts[tag] = counts.get(tag, 0) + 1

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, count in ranked if count >= min_count][:limit]


def parse_json_object(raw_text: str) -> Dict[str, Any]:
    text = raw_text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_subprocess(args: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )


def transcribe_with_whisper_cpp(
    config: Dict[str, Any],
    source_path: Path,
    output_prefix: Path,
) -> str:
    transcription = config["transcription"]
    command = transcription["command"]
    if not command_exists(command):
        raise RuntimeError(
            f"Transcription command not found: {command}. Install whisper.cpp or change transcription.command."
        )

    model_path = transcription.get("model_path")
    if not model_path:
        raise RuntimeError("transcription.model_path is required for the whisper_cpp backend.")

    args = [
        command,
        "-m",
        model_path,
        "-f",
        str(source_path),
        "-l",
        transcription.get("language", "en"),
        "-otxt",
        "-of",
        str(output_prefix),
    ]
    args.extend(transcription.get("extra_args", []))

    result = run_subprocess(args)
    transcript_path = output_prefix.with_suffix(".txt")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "whisper.cpp failed")
    if not transcript_path.exists():
        raise RuntimeError(f"Expected transcript file was not created: {transcript_path}")
    return transcript_path.read_text(encoding="utf-8")


def transcribe_with_command(
    config: Dict[str, Any],
    source_path: Path,
    output_prefix: Path,
) -> str:
    transcription = config["transcription"]
    template = transcription.get("command_template", [])
    if not template:
        raise RuntimeError("transcription.command_template is required for the command backend.")

    placeholders = {
        "input_path": str(source_path),
        "output_prefix": str(output_prefix),
        "output_dir": str(output_prefix.parent),
        "language": transcription.get("language", "en"),
        "model_path": transcription.get("model_path", ""),
    }
    args = [piece.format(**placeholders) for piece in template]
    if not args:
        raise RuntimeError("The command backend produced an empty command.")

    result = run_subprocess(args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "command backend failed")

    if transcription.get("stdout_is_transcript", False):
        return result.stdout.strip()

    transcript_template = transcription.get("transcript_file_template", "{output_prefix}.txt")
    transcript_path = Path(transcript_template.format(**placeholders))
    if not transcript_path.exists():
        raise RuntimeError(f"Expected transcript file was not created: {transcript_path}")
    return transcript_path.read_text(encoding="utf-8")


def transcribe_with_mock(config: Dict[str, Any], source_path: Path, output_prefix: Path) -> str:
    del source_path, output_prefix
    transcription = config["transcription"]
    sample = transcription.get("mock_transcript", "")
    if not sample:
        raise RuntimeError("transcription.mock_transcript is required for the mock backend.")
    return sample


def transcribe_audio(config: Dict[str, Any], source_path: Path) -> str:
    transcripts_dir = Path(config["runtime_dir"]) / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = transcripts_dir / source_path.stem
    backend = config["transcription"]["backend"]

    if backend == "whisper_cpp":
        transcript = transcribe_with_whisper_cpp(config, source_path, output_prefix)
    elif backend == "command":
        transcript = transcribe_with_command(config, source_path, output_prefix)
    elif backend == "mock":
        transcript = transcribe_with_mock(config, source_path, output_prefix)
    else:
        raise RuntimeError(f"Unsupported transcription backend: {backend}")

    transcript = clean_transcript(transcript)
    transcript_path = unique_path(transcripts_dir / f"{source_path.stem}.txt")
    transcript_path.write_text(transcript + "\n", encoding="utf-8")
    return transcript


# Per-lane AI summarization hints. Add entries matching your lane names in config.json
# to give the model context about what each lane contains.
# Example: {"journal": "Personal journal entry. Focus on feelings and intentions."}
LANE_AI_INSTRUCTIONS: Dict[str, str] = {}


def ai_prompt(
    transcript: str,
    max_tags: int,
    preferred_tags: List[str],
    workflow: str = "default",
    lane_instruction: str = "",
) -> str:
    preferred_tags_block = ""
    if preferred_tags:
        preferred_tags_block = (
            "Existing vault tags (reuse ONLY if they clearly match this specific transcript — "
            "do not force-fit; inventing from content is better than a poor match):\n"
            f"{', '.join(preferred_tags)}\n\n"
        )

    lane_block = f"Lane context: {lane_instruction}\n\n" if lane_instruction else ""

    return (
        "You organize personal voice notes for Obsidian.\n"
        "Return strict JSON only. Do not wrap it in markdown.\n"
        "Use this schema:\n"
        "{\n"
        '  "title": "short note title",\n'
        '  "summary": "2-5 sentence factual summary",\n'
        '  "action_items": ["task one", "task two"],\n'
        f'  "suggested_tags": ["lowercase-kebab-tag"]\n'
        "}\n"
        f"Rules:\n- Keep suggested_tags to at most {max_tags} items.\n"
        "- Do not invent facts.\n"
        "- If there are no action items, return an empty array.\n"
        "- Tags must be grounded in the actual transcript content.\n"
        "- Tags should describe likely future retrieval, not every topic mentioned.\n"
        "- Make the title specific but concise.\n\n"
        f"{lane_block}"
        f"{preferred_tags_block}"
        "Transcript:\n"
        f"{transcript}\n"
    )


def lane_ai_instruction(config: Dict[str, Any], workflow: str) -> str:
    """Return the ai_instruction string for a lane, from config. Falls back to LANE_AI_INSTRUCTIONS."""
    lane_cfg = config.get("lanes", {}).get(workflow, {})
    return lane_cfg.get("ai_instruction", "") or LANE_AI_INSTRUCTIONS.get(workflow, "")


def run_ai_ollama_http(config: Dict[str, Any], transcript: str, workflow: str = "default") -> Dict[str, Any]:
    ai = config["ai"]
    preferred_tags = collect_preferred_tags(config)
    payload = {
        "model": ai["model"],
        "prompt": ai_prompt(transcript, int(ai.get("max_tags", 5)), preferred_tags, workflow, lane_ai_instruction(config, workflow)),
        "stream": False,
        "format": "json",
        "options": {
            "temperature": ai.get("temperature", 0.2),
        },
    }
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{ai['base_url'].rstrip('/')}/api/generate",
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=ai.get("timeout_seconds", 120)) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Ollama at {ai['base_url']}: {exc}") from exc

    outer = parse_json_object(body)
    raw_response = outer.get("response", "")
    if not raw_response:
        raise RuntimeError(f"Ollama returned an empty response body: {body}")
    parsed = parse_json_object(raw_response)
    return normalize_ai_payload(parsed)


def run_ai_openai_http(config: Dict[str, Any], transcript: str, workflow: str = "default") -> Dict[str, Any]:
    ai = config["ai"]
    preferred_tags = collect_preferred_tags(config)
    api_key = ai.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OpenAI API key not set. Add 'api_key' to config.json or set the OPENAI_API_KEY environment variable."
        )

    payload = {
        "model": ai.get("model", "gpt-4o-mini"),
        "messages": [
            {"role": "user", "content": ai_prompt(transcript, int(ai.get("max_tags", 5)), preferred_tags, workflow, lane_ai_instruction(config, workflow))}
        ],
        "temperature": ai.get("temperature", 0.2),
        "response_format": {"type": "json_object"},
    }
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=encoded,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=ai.get("timeout_seconds", 60)) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise RuntimeError(f"OpenAI API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach OpenAI API: {exc}") from exc

    outer = json.loads(body)
    content = outer["choices"][0]["message"]["content"]
    parsed = parse_json_object(content)
    return normalize_ai_payload(parsed)


def run_ai_mock(config: Dict[str, Any], transcript: str) -> Dict[str, Any]:
    del transcript
    ai = config["ai"]
    payload = ai.get(
        "mock_response",
        {
            "title": "Mock voice note",
            "summary": "This is a mock summary used for validation.",
            "action_items": ["Replace the mock AI backend with Ollama."],
            "suggested_tags": ["mock", "voice-note"],
        },
    )
    return normalize_ai_payload(payload)


def normalize_ai_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    title = str(payload.get("title", "")).strip()
    summary = str(payload.get("summary", "")).strip()
    action_items_raw = payload.get("action_items", [])
    tags_raw = payload.get("suggested_tags", [])

    action_items = []
    for item in action_items_raw if isinstance(action_items_raw, list) else []:
        cleaned = str(item).strip()
        if cleaned:
            action_items.append(cleaned)

    tags = []
    for item in tags_raw if isinstance(tags_raw, list) else []:
        cleaned = slugify(str(item), fallback="")
        if cleaned and cleaned not in tags:
            tags.append(cleaned)

    return {
        "title": title,
        "summary": summary,
        "action_items": action_items,
        "suggested_tags": tags,
    }


def run_ai(config: Dict[str, Any], transcript: str, workflow: str = "default") -> Dict[str, Any]:
    ai = config["ai"]
    if not ai.get("enabled", True):
        return {
            "title": "",
            "summary": "",
            "action_items": [],
            "suggested_tags": [],
        }

    backend = ai.get("backend", "ollama_http")
    if backend == "ollama_http":
        return run_ai_ollama_http(config, transcript, workflow)
    if backend == "openai_http":
        return run_ai_openai_http(config, transcript, workflow)
    if backend == "mock":
        return run_ai_mock(config, transcript)
    raise RuntimeError(f"Unsupported AI backend: {backend}")


def note_title(source_path: Path, ai_payload: Dict[str, Any]) -> str:
    proposed = ai_payload.get("title", "").strip()
    if proposed:
        return proposed
    stem = source_path.stem.replace("_", " ").replace("-", " ").strip()
    return stem or "Voice Note"


def build_note_content(
    config: Dict[str, Any],
    source_path: Path,
    archived_audio_path: Path,
    transcript: str,
    ai_payload: Dict[str, Any],
    workflow: str = "default",
    routing_reason: str = "fallback",
) -> str:
    template_path = Path(config["note_template_path"])
    template = template_path.read_text(encoding="utf-8")
    created_at = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone().replace(microsecond=0)
    processed_at = iso_now()
    title = note_title(source_path, ai_payload)

    values = {
        "title": title,
        "created_at": created_at.isoformat(),
        "processed_at": processed_at,
        "status": "inbox",
        "workflow": workflow,
        "routing_reason": routing_reason,
        "source_audio_name": source_path.name,
        "source_audio_path": str(archived_audio_path),
        "transcript_backend": config["transcription"]["backend"],
        "ai_backend": config["ai"]["backend"] if config["ai"].get("enabled", True) else "disabled",
        "summary": ai_payload.get("summary", "") or "_No AI summary generated._",
        "action_items": format_action_items(ai_payload.get("action_items", [])),
        "suggested_tags_list": format_bullet_tags(ai_payload.get("suggested_tags", [])),
        "suggested_tags_yaml": yaml_list(ai_payload.get("suggested_tags", [])),
        "transcript": transcript,
    }
    return render_template(template, values)


def note_destination(config: Dict[str, Any], title: str, source_path: Path) -> Path:
    inbox_dir = Path(config["obsidian_inbox_dir"])
    created_at = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone()
    slug = slugify(title)
    filename = f"{created_at.strftime('%Y-%m-%d %H%M%S')} {slug}.md"
    return unique_path(inbox_dir / filename)


def gpt_packet_destination(config: Dict[str, Any], title: str, source_path: Path) -> Path:
    export_dir = Path(config["gpt_handoff"]["export_dir"])
    created_at = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone()
    filename = f"{created_at.strftime('%Y-%m-%d %H%M%S')} {slugify(title)}.md"
    return unique_path(export_dir / filename)


def write_note(
    config: Dict[str, Any],
    source_path: Path,
    archived_audio_path: Path,
    transcript: str,
    ai_payload: Dict[str, Any],
    workflow: str = "default",
    routing_reason: str = "fallback",
) -> Path:
    title = note_title(source_path, ai_payload)
    destination = note_destination(config, title, source_path)
    content = build_note_content(
        config, source_path, archived_audio_path, transcript, ai_payload, workflow, routing_reason
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return destination


def build_gpt_handoff_content(
    config: Dict[str, Any],
    source_path: Path,
    archived_audio_path: Path,
    transcript: str,
    ai_payload: Dict[str, Any],
    note_path: Path,
) -> str:
    template_path = Path(config["gpt_handoff"]["template_path"])
    template = template_path.read_text(encoding="utf-8")
    title = note_title(source_path, ai_payload)
    created_at = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone().replace(microsecond=0)

    values = {
        "title": title,
        "created_at": created_at.isoformat(),
        "instructions": config["gpt_handoff"]["instructions"],
        "source_audio_name": source_path.name,
        "source_audio_path": str(archived_audio_path),
        "note_path": str(note_path),
        "summary": ai_payload.get("summary", "") or "_No AI summary generated._",
        "action_items_numbered": format_numbered_list(ai_payload.get("action_items", [])),
        "suggested_tags_list": format_bullet_tags(ai_payload.get("suggested_tags", [])),
        "transcript": transcript,
    }
    return render_template(template, values)


def maybe_copy_to_clipboard(config: Dict[str, Any], text: str) -> None:
    if not config["gpt_handoff"].get("copy_to_clipboard", False):
        return
    if not command_exists("pbcopy"):
        raise RuntimeError("copy_to_clipboard is enabled, but pbcopy is not available.")

    process = subprocess.run(["pbcopy"], input=text, text=True, capture_output=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or "pbcopy failed")


def write_gpt_packet(
    config: Dict[str, Any],
    source_path: Path,
    archived_audio_path: Path,
    transcript: str,
    ai_payload: Dict[str, Any],
    note_path: Path,
) -> Path:
    title = note_title(source_path, ai_payload)
    destination = gpt_packet_destination(config, title, source_path)
    content = build_gpt_handoff_content(
        config=config,
        source_path=source_path,
        archived_audio_path=archived_audio_path,
        transcript=transcript,
        ai_payload=ai_payload,
        note_path=note_path,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    maybe_copy_to_clipboard(config, content)
    return destination


def metadata_destination(config: Dict[str, Any], archived_audio_path: Path) -> Path:
    metadata_dir = Path(config["runtime_dir"]) / "metadata"
    relative_name = archived_audio_path.stem
    return unique_path(metadata_dir / f"{relative_name}.json")


def write_metadata(
    config: Dict[str, Any],
    lane: str,
    source_path: Path,
    archived_audio_path: Path,
    note_path: Path,
    transcript: str,
    ai_payload: Dict[str, Any],
    gpt_packet_path: Optional[Path],
) -> Path:
    destination = metadata_destination(config, archived_audio_path)
    payload = {
        "lane": lane,
        "source_path": str(source_path),
        "archived_audio_path": str(archived_audio_path),
        "note_path": str(note_path),
        "gpt_packet_path": str(gpt_packet_path) if gpt_packet_path else None,
        "processed_at": iso_now(),
        "transcript_backend": config["transcription"]["backend"],
        "ai_backend": config["ai"]["backend"] if config["ai"].get("enabled", True) else "disabled",
        "transcript_preview": transcript[:500],
        "transcript_characters": len(transcript),
        "summary": ai_payload.get("summary", ""),
        "action_items": ai_payload.get("action_items", []),
        "suggested_tags": ai_payload.get("suggested_tags", []),
    }
    write_json(destination, payload)
    return destination


# ---------------------------------------------------------------------------
# Wisdom corpus and reflect synthesis
# ---------------------------------------------------------------------------

def load_corpus_passages(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Load all passages from the wisdom corpus directory.

    Each .md file should have YAML frontmatter (author, work, tradition) and
    sections marked with '## Passage: <title>' headings.
    """
    corpus_dir_raw = config.get("wisdom_corpus_dir", "")
    if not corpus_dir_raw:
        return []
    config_dir = Path(config.get("_config_dir", "."))
    raw = Path(os.path.expanduser(corpus_dir_raw))
    corpus_dir = raw if raw.is_absolute() else (config_dir / raw).resolve()
    if not corpus_dir.exists():
        return []

    passages: List[Dict[str, Any]] = []
    for md_file in sorted(corpus_dir.rglob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        author = tradition = work = ""
        lines = text.splitlines()
        body_start = 0
        if lines and lines[0].strip() == "---":
            i = 1
            while i < len(lines) and lines[i].strip() != "---":
                line = lines[i]
                if line.startswith("author:"):
                    author = line.split(":", 1)[1].strip()
                elif line.startswith("work:"):
                    work = line.split(":", 1)[1].strip()
                elif line.startswith("tradition:"):
                    tradition = line.split(":", 1)[1].strip()
                i += 1
            body_start = i + 1
        body = "\n".join(lines[body_start:])

        for section in re.split(r"^## Passage:", body, flags=re.MULTILINE):
            section = section.strip()
            if not section:
                continue
            section_lines = section.splitlines()
            passage_title = section_lines[0].strip() if section_lines else ""
            passage_body = "\n".join(section_lines[1:]).strip()
            if passage_body:
                passages.append({
                    "author": author,
                    "work": work,
                    "tradition": tradition,
                    "title": passage_title,
                    "text": passage_body,
                })

    return passages


def load_context_feeds(actions: Dict[str, Any]) -> str:
    """Load and concatenate content from context_feeds configured for a lane action.

    Each feed entry supports:
      type: "file"         — reads the full file as plain text
      type: "jsonl_recent" — reads the N most recent entries from a JSONL file,
                             rendering each entry's title + summary + transcript
      path: str            — path to the file (~ expanded)
      label: str           — section heading in the context block
      limit: int           — (jsonl_recent only) number of entries to include (default 5)

    Returns a formatted string to inject into the synthesis prompt, or "" if no feeds.
    """
    feeds = actions.get("context_feeds", [])
    if not feeds:
        return ""

    sections: List[str] = []
    for feed in feeds:
        feed_type = feed.get("type", "file")
        path_raw = feed.get("path", "")
        label = feed.get("label", path_raw)
        if not path_raw:
            continue

        feed_path = Path(os.path.expanduser(path_raw))
        if not feed_path.exists():
            continue

        try:
            if feed_type == "file":
                content = feed_path.read_text(encoding="utf-8").strip()
                if content:
                    sections.append(f"[{label}]\n{content}")

            elif feed_type == "jsonl_recent":
                limit = int(feed.get("limit", 5))
                lines = [l for l in feed_path.read_text(encoding="utf-8").splitlines() if l.strip()]
                recent = lines[-limit:]
                entries = []
                for line in recent:
                    try:
                        entry = json.loads(line)
                        title = entry.get("title", "Untitled")
                        summary = entry.get("summary", "").strip()
                        transcript_text = entry.get("transcript", "").strip()
                        ts = entry.get("timestamp", "")
                        parts = [f"• {title} ({ts[:10] if ts else ''})"]
                        if summary:
                            parts.append(f"  Summary: {summary}")
                        if transcript_text:
                            parts.append(f"  Transcript: {transcript_text}")
                        entries.append("\n".join(parts))
                    except (json.JSONDecodeError, KeyError):
                        continue
                if entries:
                    sections.append(f"[{label}]\n" + "\n\n".join(entries))

        except OSError:
            continue

    return "\n\n".join(sections)


def coaching_analysis_prompt(transcript: str, context: str = "") -> str:
    """Build the prompt for professional lane life-coach analysis."""
    context_block = (
        f"--- BACKGROUND CONTEXT (use to deepen relevance, do not summarise back) ---\n{context}\n\n"
        if context.strip() else ""
    )
    return (
        "You are an expert executive and life coach with deep experience in career transitions, "
        "professional relationships, and purposeful growth. You receive a voice note from a client "
        "and optional background context about their situation.\n\n"
        "Return strict JSON only. Do not wrap in markdown fences.\n"
        "Schema:\n"
        "{\n"
        '  "coaching_title": "short title that captures the core theme of this note",\n'
        '  "observations": "2-3 paragraphs. Speak directly to the person (use \'you\'). '
        "Identify patterns, name what's really going on beneath the surface, and connect "
        'this moment to their broader trajectory. Warm but direct tone.",\n'
        '  "next_steps": ["specific, concrete action — not generic advice"],\n'
        '  "reflection_questions": ["a question worth sitting with"],\n'
        '  "todo_items": ["actionable task phrased as a checkbox item"]\n'
        "}\n\n"
        "Rules:\n"
        "- next_steps should be 2-4 items: bold, specific, doable within days or weeks\n"
        "- reflection_questions should be 1-3 items: open-ended, not rhetorical\n"
        "- todo_items are practical tasks extracted or inferred from the transcript — "
        "things the person clearly needs to do (can overlap with next_steps if they are tasks)\n"
        "- Do not invent facts; stay grounded in what was said\n"
        "- If context is provided, use it to sharpen relevance — but don't repeat it back\n\n"
        f"{context_block}"
        "--- VOICE NOTE TRANSCRIPT ---\n"
        f"{transcript}\n"
    )


def run_ai_coaching_analysis(
    config: Dict[str, Any],
    transcript: str,
    context: str = "",
) -> Dict[str, Any]:
    """Call the AI for a professional life-coach analysis of a voice note."""
    ai = config.get("ai", {})
    if not ai.get("enabled", True):
        return {}

    prompt = coaching_analysis_prompt(transcript, context)
    backend = ai.get("backend", "ollama_http")

    if backend == "openai_http":
        api_key = ai.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OpenAI API key not set for coaching analysis.")
        payload = {
            "model": ai.get("model", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        encoded = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=encoded,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=ai.get("timeout_seconds", 60)) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach OpenAI for coaching analysis: {exc}") from exc
        outer = parse_json_object(body)
        choices = outer.get("choices", [])
        if not choices:
            return {}
        return parse_json_object(choices[0].get("message", {}).get("content", "{}"))

    if backend == "ollama_http":
        payload = {
            "model": ai["model"],
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.3},
        }
        encoded = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{ai['base_url'].rstrip('/')}/api/generate",
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=ai.get("timeout_seconds", 120)) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach Ollama: {exc}") from exc
        outer = parse_json_object(body)
        return parse_json_object(outer.get("response", "{}"))

    return {}


def write_coaching_note(
    config: Dict[str, Any],
    source_note_path: Path,
    transcript: str,
    file_mtime: datetime,
    coaching: Dict[str, Any],
) -> Optional[Path]:
    """Render and write the coaching analysis note to Obsidian."""
    if not coaching:
        return None

    title = coaching.get("coaching_title", "Coaching Note")
    observations = coaching.get("observations", "").strip()
    next_steps = coaching.get("next_steps") or []
    reflection_questions = coaching.get("reflection_questions") or []
    raw_todos = coaching.get("todo_items") or []

    # Strip any leading checkbox syntax the AI may have added (e.g. "[ ] task")
    def _clean(s: str) -> str:
        return re.sub(r"^\[[ x]\]\s*", "", s.strip())

    todo_items = [_clean(t) for t in raw_todos if t]

    next_steps_md = "\n".join(f"- {s}" for s in next_steps) if next_steps else "_None identified._"
    questions_md = "\n".join(f"- {q}" for q in reflection_questions) if reflection_questions else "_None identified._"
    todo_md = "\n".join(f"- [ ] {t}" for t in todo_items) if todo_items else "_No tasks extracted._"

    processed_at = iso_now()
    content = f"""---
title: {title}
type: coaching-note
status: inbox
workflow: professional
source_note: {source_note_path}
created: {file_mtime.isoformat()}
generated: {processed_at}
---

# {title}

## Coaching Perspective

{observations}

---

## Next Steps

{next_steps_md}

---

## Reflection Questions

{questions_md}

---

## To-Do

{todo_md}

---

*Generated from voice note on {file_mtime.strftime('%Y-%m-%d')}*
"""

    inbox_dir = Path(config.get("professional_obsidian_dir") or config["obsidian_inbox_dir"])
    inbox_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(title)
    filename = f"{file_mtime.strftime('%Y-%m-%d %H%M%S')} coaching-{slug}.md"
    dest = unique_path(inbox_dir / filename)
    dest.write_text(content, encoding="utf-8")
    return dest


def wisdom_synthesis_prompt(
    transcript: str,
    passages: List[Dict[str, Any]],
    context: str = "",
) -> str:
    corpus_lines = []
    for p in passages:
        source = f"{p['author']}, {p['work']}" if p.get("work") else p.get("author", "Unknown")
        corpus_lines.append(f"[{source}]\n{p['text']}")
    corpus_block = "\n\n---\n\n".join(corpus_lines)

    context_block = (
        f"--- ADDITIONAL CONTEXT (use to deepen relevance, do not summarise back) ---\n{context}\n\n"
        if context.strip() else ""
    )

    return (
        "You are a personal wisdom synthesizer. You receive a voice reflection "
        "from someone about their life, a library of passages from philosophical "
        "and sacred texts, and optionally additional context about their life circumstances.\n\n"
        "Return strict JSON only. Do not wrap in markdown fences.\n"
        "Schema:\n"
        "{\n"
        '  "title": "a short evocative title for this wisdom note",\n'
        '  "selected_passages": [\n'
        '    {"source": "Author, Work", "quote": "exact verbatim quote", '
        '"why": "one sentence on why this resonates with the reflection"}\n'
        "  ],\n"
        '  "podcast_script": "3-5 paragraphs. Warm, personal voice. Weave the person\'s '
        "themes with the selected wisdom. Speak to the person directly — not about them. "
        'No academic tone.\",\n'
        '  "meditation_script": "Three parts clearly separated by blank lines: '
        "OPENING (ground and arrive, 1 paragraph), BODY (weave the day's themes with "
        "the passages, 2-3 paragraphs), CLOSING (integration and intention, 1 paragraph). "
        'Use present tense. Gentle, unhurried pace.",\n'
        '  "traditions": ["list", "of", "traditions", "drawn", "from"],\n'
        '  "music_mood": "one word from: peaceful | melancholic | hopeful | contemplative | healing | wonder | grounded | expansive"\n'
        "}\n\n"
        "Rules:\n"
        "- Select 3 to 5 passages that most resonate with what the person is going through right now\n"
        "- Use the additional context to sharpen relevance — but do not repeat it back verbatim\n"
        "- Quote verbatim — do not paraphrase the quotes themselves\n"
        "- The podcast script should feel like a thoughtful friend speaking, not a lecturer\n"
        "- The meditation should be practical and grounded, not generic spa music copy\n"
        "- If the reflection is short or unclear, err toward stillness and acceptance themes\n\n"
        "--- VOICE REFLECTION ---\n"
        f"{transcript}\n\n"
        f"{context_block}"
        "--- WISDOM CORPUS ---\n"
        f"{corpus_block}\n"
    )


def run_ai_wisdom_synthesis(
    config: Dict[str, Any],
    transcript: str,
    passages: List[Dict[str, Any]],
    context: str = "",
) -> Dict[str, Any]:
    """Call the AI to select passages and generate podcast + meditation scripts."""
    ai = config["ai"]
    if not ai.get("enabled", True) or not passages:
        return {}

    prompt = wisdom_synthesis_prompt(transcript, passages, context)
    backend = ai.get("backend", "ollama_http")

    if backend == "openai_http":
        api_key = ai.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OpenAI API key not set for wisdom synthesis.")
        payload = {
            "model": ai.get("model", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,  # slightly more creative than summarization
            "response_format": {"type": "json_object"},
        }
        encoded = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=encoded,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=ai.get("timeout_seconds", 90)) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"OpenAI API error {exc.code}: {exc.read().decode()}") from exc
        outer = json.loads(body)
        content = outer["choices"][0]["message"]["content"]
        return parse_json_object(content)

    if backend == "ollama_http":
        payload = {
            "model": ai["model"],
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.5},
        }
        encoded = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{ai['base_url'].rstrip('/')}/api/generate",
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=ai.get("timeout_seconds", 120)) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach Ollama: {exc}") from exc
        outer = parse_json_object(body)
        return parse_json_object(outer.get("response", "{}"))

    return {}


def write_wisdom_note(
    config: Dict[str, Any],
    source_note_path: Path,
    transcript: str,
    file_mtime: datetime,
    synthesis: Dict[str, Any],
) -> Optional[Path]:
    """Render and write the wisdom synthesis note to Obsidian."""
    if not synthesis:
        return None

    template_path = Path(os.path.expanduser(config.get("wisdom_template_path", "./templates/wisdom-note.md")))
    if not template_path.is_absolute():
        template_path = (Path(config["_config_dir"]) / template_path).resolve()
    if not template_path.exists():
        return None

    template = template_path.read_text(encoding="utf-8")
    title = synthesis.get("title", "Wisdom Note")
    processed_at = iso_now()

    # Format selected passages as blockquotes
    passages_md_lines = []
    for p in synthesis.get("selected_passages", []):
        quote = p.get("quote", "").strip()
        source = p.get("source", "").strip()
        why = p.get("why", "").strip()
        if quote:
            passages_md_lines.append(f"> {quote}\n>\n> — *{source}*")
            if why:
                passages_md_lines.append(f"\n{why}\n")
    passages_md = "\n\n".join(passages_md_lines) if passages_md_lines else "_No passages selected._"

    traditions = synthesis.get("traditions") or []
    if isinstance(traditions, str):
        traditions = [t.strip() for t in traditions.split(",") if t.strip()]
    traditions_str = ", ".join(traditions) if traditions else ""

    tags = [slugify(t) for t in traditions if t]
    tags_yaml = yaml_list(tags)

    values = {
        "title": title,
        "source_note": str(source_note_path),
        "created_at": file_mtime.isoformat(),
        "processed_at": processed_at,
        "traditions": traditions_str,
        "suggested_tags_yaml": tags_yaml,
        "selected_passages": passages_md,
        "podcast_script": synthesis.get("podcast_script", "").strip(),
        "meditation_script": synthesis.get("meditation_script", "").strip(),
    }
    content = render_template(template, values)

    inbox_dir = Path(config.get("wisdom_obsidian_dir") or config["obsidian_inbox_dir"])
    inbox_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(title)
    filename = f"{file_mtime.strftime('%Y-%m-%d %H%M%S')} wisdom-{slug}.md"
    dest = unique_path(inbox_dir / filename)
    dest.write_text(content, encoding="utf-8")
    return dest



# ---------------------------------------------------------------------------
# Music library — mood → track filename mapping.
# Tracks live in the output_dir (or a sibling "music" subdir).
# Each mood maps to an ordered list; first existing file wins.
# ---------------------------------------------------------------------------
MUSIC_LIBRARY: Dict[str, List[str]] = {
    # Gentle stillness — for presence, acceptance, everyday gratitude
    "peaceful": ["meditation-impromptu.mp3", "constancy-part-one.mp3", "heartwarming.mp3"],
    # Introspective sadness, loss, ending of chapters
    "melancholic": ["mourning-song.mp3", "dreamy-flashback.mp3", "slow-burn.mp3"],
    # Forward motion, possibility, new beginnings
    "hopeful": ["heartwarming.mp3", "leaving-home.mp3", "comfortable-mystery.mp3"],
    # Deep thinking, philosophy, uncertainty
    "contemplative": ["comfortable-mystery.mp3", "perspectives.mp3", "slow-burn.mp3"],
    # Recovery, care, emotional tenderness
    "healing": ["healing.mp3", "long-note-two.mp3", "relaxing-piano.mp3"],
    # Awe, curiosity, intellectual delight
    "wonder": ["peaceful-desolation.mp3", "dreamy-flashback.mp3", "perspectives.mp3"],
    # Earth, body, rootedness
    "grounded": ["constancy-part-one.mp3", "long-note-one.mp3", "meditation-impromptu.mp3"],
    # Spacious, open horizon, transition
    "expansive": ["leaving-home.mp3", "long-note-one.mp3", "peaceful-desolation.mp3"],
    # Forward momentum, focus, start of day
    "energizing": ["deliberate-thought.mp3", "call-to-adventure.mp3", "rising.mp3"],
}

_MOOD_FALLBACK = ["meditation-impromptu.mp3", "slow-burn.mp3", "relaxing-piano.mp3"]


def select_music_for_mood(mood: str, audio_dir: Path) -> Optional[Path]:
    """Return the first existing file for *mood* from MUSIC_LIBRARY, else fallback.

    *audio_dir* is the directory that contains all track files.
    The mood value is normalised to lowercase and stripped before lookup.
    """
    normalised = (mood or "").strip().lower()
    candidates = MUSIC_LIBRARY.get(normalised, []) + _MOOD_FALLBACK
    for filename in candidates:
        track = audio_dir / filename
        if track.exists():
            return track
    return None


def mix_meditation_with_music(
    narration_path: Path,
    music_path: Path,
    output_path: Path,
    music_volume: float = 0.15,
    fade_out_seconds: int = 4,
) -> Path:
    """Mix a narration MP3 with ambient music using ffmpeg.

    The music is looped to match the narration length, ducked to music_volume,
    and faded out over the final fade_out_seconds. Requires ffmpeg in PATH.
    """
    ffmpeg = "/opt/homebrew/bin/ffmpeg"
    if not Path(ffmpeg).exists():
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    filter_graph = (
        f"[1:a]volume={music_volume},aloop=loop=-1:size=2147483647[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition={fade_out_seconds}[out]"
    )
    cmd = [
        ffmpeg, "-y",
        "-i", str(narration_path),
        "-i", str(music_path),
        "-filter_complex", filter_graph,
        "-map", "[out]",
        "-b:a", "128k",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mixing failed: {result.stderr.strip()}")
    return output_path


def generate_wisdom_audio(
    actions: Dict[str, Any],
    synthesis: Dict[str, Any],
    note_path: Path,
) -> Dict[str, str]:
    """Generate MP3 audio for podcast and meditation scripts using edge-tts.

    Requires: pip install edge-tts
    Config (under lane_actions.<lane>.generate_wisdom_audio):
      enabled:            bool   — master switch (default false)
      voice:              str    — edge-tts voice name (default en-US-JennyNeural)
      meditation_rate:    str    — speaking rate for meditation (default -20%)
      output_dir:         str    — output directory (default ~/.codex/wisdom/audio)
      ambient_music_path: str    — optional path to ambient music file for meditation
      music_volume:       float  — music level relative to narration (default 0.15)
      fade_out_seconds:   int    — fade duration at end of meditation (default 4)

    Returns a dict with keys "podcast_audio" and/or "meditation_audio" pointing to
    the generated file paths. Appends audio links to the bottom of the wisdom note.
    """
    audio_cfg = actions.get("generate_wisdom_audio", {})
    if not audio_cfg.get("enabled", False):
        return {}

    try:
        import asyncio
        import edge_tts  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "edge-tts is not installed. Run: pip install edge-tts"
        ) from exc

    output_dir = Path(os.path.expanduser(audio_cfg.get("output_dir", "~/.codex/wisdom/audio")))
    output_dir.mkdir(parents=True, exist_ok=True)
    # Music library dir — where source tracks live (may differ from output_dir)
    music_lib_dir = Path(os.path.expanduser(
        audio_cfg.get("music_library_dir", "~/.codex/wisdom/audio")
    ))

    voice = audio_cfg.get("voice", "en-US-JennyNeural")
    meditation_voice = audio_cfg.get("meditation_voice", voice)
    meditation_rate = audio_cfg.get("meditation_rate", "-20%")
    # Strip leading timestamp and "wisdom-" prefix, e.g.
    # "2026-05-26 080657 wisdom-embracing-the-threshold" → "embracing-the-threshold"
    raw_stem = note_path.stem
    stem = re.sub(r"^\d{4}-\d{2}-\d{2} \d{6} ", "", raw_stem)
    stem = re.sub(r"^wisdom-", "", stem)

    async def synthesize(text: str, v: str, rate: str, dest: Path) -> None:
        communicate = edge_tts.Communicate(text, v, rate=rate)
        await communicate.save(str(dest))

    results: Dict[str, str] = {}

    podcast_text = synthesis.get("podcast_script", "").strip()
    if podcast_text:
        podcast_path = output_dir / f"podcast-{stem}.mp3"
        asyncio.run(synthesize(podcast_text, voice, "+0%", podcast_path))
        results["podcast_audio"] = str(podcast_path)

    meditation_text = synthesis.get("meditation_script", "").strip()
    if meditation_text:
        narration_path = output_dir / f"meditation-{stem}-narration.mp3"
        asyncio.run(synthesize(meditation_text, meditation_voice, meditation_rate, narration_path))

        # Select ambient music: mood-based first, then fixed fallback, then skip
        mood = synthesis.get("music_mood", "")
        music_path: Optional[Path] = None
        if mood:
            music_path = select_music_for_mood(mood, music_lib_dir)
            if music_path:
                print(f"[wisdom] music mood '{mood}' → {music_path.name}")
            else:
                print(f"[wisdom] music mood '{mood}' — no matching track found, trying fallback")
        if music_path is None:
            music_raw = audio_cfg.get("ambient_music_path", "")
            if music_raw:
                candidate = Path(os.path.expanduser(music_raw))
                if candidate.exists():
                    music_path = candidate
        if music_path is not None:
            mixed_path = output_dir / f"meditation-{stem}.mp3"
            mix_meditation_with_music(
                narration_path=narration_path,
                music_path=music_path,
                output_path=mixed_path,
                music_volume=float(audio_cfg.get("music_volume", 0.15)),
                fade_out_seconds=int(audio_cfg.get("fade_out_seconds", 4)),
            )
            narration_path.unlink(missing_ok=True)  # remove bare narration
            results["meditation_audio"] = str(mixed_path)
        else:
            # No music — rename narration to final path
            final_path = output_dir / f"meditation-{stem}.mp3"
            narration_path.rename(final_path)
            results["meditation_audio"] = str(final_path)

    # Append audio links to the bottom of the wisdom note
    if results and note_path.exists():
        lines = ["\n\n---\n\n## Audio\n"]
        if "podcast_audio" in results:
            p = Path(results["podcast_audio"])
            lines.append(f"**Podcast:** [{p.name}]({p})\n")
        if "meditation_audio" in results:
            p = Path(results["meditation_audio"])
            lines.append(f"**Meditation:** [{p.name}]({p})\n")
        with note_path.open("a", encoding="utf-8") as f:
            f.writelines(lines)

    return results


def load_recent_wisdom_notes(config: Dict[str, Any], lookback_days: int = 7) -> List[Dict[str, Any]]:
    """Scan the Obsidian inbox for wisdom notes created in the last *lookback_days* days.

    Returns a list of dicts sorted newest-first:
      {title, created, podcast_script, traditions, path}
    The most recent note gets a 'is_most_recent' flag for prompt weighting.
    Caps at 5 notes total to keep the prompt manageable; older ones are summarised
    to title + traditions only in the calling prompt.
    """
    inbox = Path(config.get("wisdom_obsidian_dir") or config["obsidian_inbox_dir"])
    cutoff = datetime.now().astimezone() - __import__("datetime").timedelta(days=lookback_days)
    notes = []

    for md_file in sorted(inbox.glob("*wisdom-*.md"), reverse=True):
        try:
            text = md_file.read_text(encoding="utf-8")
            # Parse created date from frontmatter
            created_match = re.search(r"^created:\s*(.+)$", text, re.MULTILINE)
            if not created_match:
                continue
            created_str = created_match.group(1).strip()
            try:
                created = datetime.fromisoformat(created_str)
            except ValueError:
                continue
            if created < cutoff:
                continue

            title_match = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else md_file.stem

            traditions_match = re.search(r"^traditions:\s*(.+)$", text, re.MULTILINE)
            traditions = traditions_match.group(1).strip() if traditions_match else ""

            # Extract podcast script section
            podcast_match = re.search(
                r"##\s+Podcast Script\s*\n\n(.*?)(?=\n---|\Z)", text, re.DOTALL
            )
            podcast_script = podcast_match.group(1).strip() if podcast_match else ""

            notes.append({
                "title": title,
                "created": created,
                "traditions": traditions,
                "podcast_script": podcast_script,
                "path": str(md_file),
                "is_most_recent": False,
            })
        except (OSError, ValueError):
            continue

    notes.sort(key=lambda n: n["created"], reverse=True)
    if notes:
        notes[0]["is_most_recent"] = True
    return notes[:5]  # cap at 5


def morning_briefing_prompt(transcript: str, professional_context: str) -> str:
    """Prompt for the professional morning briefing script."""
    context_block = (
        f"--- PROFESSIONAL CONTEXT ---\n{professional_context}\n\n"
        if professional_context.strip() else ""
    )
    transcript_block = (
        f"--- ADDITIONAL MORNING NOTES (spoken by the user) ---\n{transcript.strip()}\n\n"
        if transcript.strip() else ""
    )
    return (
        "You are a warm, experienced mentor and executive coach preparing a personalised "
        "morning briefing for your client. Your tone is encouraging but direct — you see "
        "the whole person, not just their task list.\n\n"
        "Return strict JSON only. Do not wrap in markdown fences.\n"
        "Schema:\n"
        "{\n"
        '  "briefing_script": "A spoken 2-3 minute script. Structure: (1) briefly '
        "acknowledge where they've been and what they've been carrying — one or two "
        "sentences that show you've been paying attention; (2) name the one or two things "
        "that genuinely matter most today and why; (3) close with a single energising "
        'sentence that sends them into the day with momentum.",\n'
        '  "focus_theme": "three words or fewer — the core theme of today (e.g. \'follow-through\', \'making contact\', \'clearing the deck\')"\n'
        "}\n\n"
        "Rules:\n"
        "- Speak directly to the person using 'you'\n"
        "- Be specific — name actual people, projects, and situations from the context\n"
        "- Do not produce a generic motivational speech\n"
        "- The closing sentence should feel like a friend sending you off, not a slogan\n"
        "- If context is sparse, focus on intentions and energy over specific tasks\n\n"
        f"{context_block}"
        f"{transcript_block}"
    )


def morning_reflection_prompt(
    transcript: str,
    recent_notes: List[Dict[str, Any]],
) -> str:
    """Prompt for the weekly reflection synthesis in the morning podcast."""
    if not recent_notes:
        return ""

    notes_block_parts = []
    for note in recent_notes:
        if note["is_most_recent"]:
            header = f"[MOST RECENT — {note['created'].strftime('%A %b %d')}] {note['title']}"
            body = note["podcast_script"] or f"Traditions drawn from: {note['traditions']}"
            notes_block_parts.append(f"{header}\n{body}")
        else:
            # Older notes: title + traditions only to save tokens
            header = f"[Earlier this week — {note['created'].strftime('%A %b %d')}] {note['title']}"
            notes_block_parts.append(f"{header}\nTraditions: {note['traditions']}")

    notes_block = "\n\n---\n\n".join(notes_block_parts)

    transcript_block = (
        f"--- MORNING VOICE NOTE ---\n{transcript.strip()}\n\n"
        if transcript.strip() else ""
    )

    return (
        "You are writing the reflective segment of a personalised daily podcast. "
        "You have access to wisdom reflections from the past week.\n\n"
        "Return strict JSON only. Do not wrap in markdown fences.\n"
        "Schema:\n"
        "{\n"
        '  "reflection_script": "A spoken 3-4 minute script. Draw primarily from the '
        "MOST RECENT reflection — let its themes, passages, and insights carry the most "
        "weight. Weave in earlier themes from the week only where they deepen or connect. "
        "If there is no most recent note, synthesise the week's arc as a whole. "
        "Speak directly to the person. Warm, unhurried, thoughtful — not a summary, "
        'but a living conversation with the week.",\n'
        '  "music_mood": "one word from: peaceful | melancholic | hopeful | contemplative | healing | wonder | grounded | expansive"\n'
        "}\n\n"
        "Rules:\n"
        "- The most recent reflection is primary; earlier ones are context\n"
        "- Do not list bullet points or recite events — weave them into a spoken narrative\n"
        "- Close with one question or intention worth carrying into the day\n\n"
        f"{transcript_block}"
        "--- RECENT REFLECTIONS ---\n\n"
        f"{notes_block}\n"
    )


def run_ai_morning(
    config: Dict[str, Any],
    transcript: str,
    professional_context: str,
    recent_notes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Two AI calls: one for the professional briefing, one for the weekly reflection.

    Returns:
      {
        "briefing_script":   str,
        "focus_theme":       str,
        "reflection_script": str,   # empty string if no recent notes
        "music_mood":        str,
      }
    """
    ai = config.get("ai", {})
    if not ai.get("enabled", True):
        return {}

    def _openai_call(prompt: str, temperature: float = 0.3) -> Dict[str, Any]:
        api_key = ai.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        payload = {
            "model": ai.get("model", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=ai.get("timeout_seconds", 60)) as resp:
            outer = parse_json_object(resp.read().decode("utf-8"))
        return parse_json_object(outer.get("choices", [{}])[0].get("message", {}).get("content", "{}"))

    result: Dict[str, Any] = {
        "briefing_script": "",
        "focus_theme": "",
        "reflection_script": "",
        "music_mood": "hopeful",
    }

    # --- Briefing ---
    try:
        briefing = _openai_call(morning_briefing_prompt(transcript, professional_context), temperature=0.3)
        result["briefing_script"] = briefing.get("briefing_script", "").strip()
        result["focus_theme"] = briefing.get("focus_theme", "").strip()
        print(f"[morning] briefing focus: {result['focus_theme']}")
    except Exception as exc:
        print(f"[morning] briefing AI call failed: {exc}")

    # --- Reflection (only if notes exist) ---
    if recent_notes:
        try:
            ref_prompt = morning_reflection_prompt(transcript, recent_notes)
            reflection = _openai_call(ref_prompt, temperature=0.4)
            result["reflection_script"] = reflection.get("reflection_script", "").strip()
            result["music_mood"] = reflection.get("music_mood", "hopeful").strip()
            print(f"[morning] reflection mood: {result['music_mood']} | {len(recent_notes)} notes used")
        except Exception as exc:
            print(f"[morning] reflection AI call failed: {exc}")
    else:
        print("[morning] no recent wisdom notes found — skipping reflection segment")

    return result


def generate_morning_audio(
    actions: Dict[str, Any],
    morning: Dict[str, Any],
    date_str: str,
) -> Dict[str, str]:
    """Generate morning briefing audio and optionally combine with reflection.

    Produces up to three files in output_dir:
      briefing-YYYY-MM-DD.mp3        — briefing with energizing music
      reflection-YYYY-MM-DD.mp3      — reflection podcast (no music)
      daily-YYYY-MM-DD.mp3           — combined: briefing + bridge + reflection

    Returns dict with keys: briefing_audio, reflection_audio, daily_audio.
    Any segment that fails is omitted from the dict; the others still land.
    """
    try:
        import asyncio
        import edge_tts  # type: ignore
    except ImportError as exc:
        raise RuntimeError("edge-tts is not installed. Run: pip install edge-tts") from exc

    output_dir = Path(os.path.expanduser(actions.get("output_dir", "~/.codex/wisdom/audio")))
    output_dir.mkdir(parents=True, exist_ok=True)
    music_lib = Path(os.path.expanduser(actions.get("music_library_dir", "~/.codex/wisdom/audio")))

    briefing_voice = actions.get("briefing_voice", "en-US-AndrewNeural")
    reflection_voice = actions.get("reflection_voice", "en-US-JennyNeural")
    music_volume = float(actions.get("music_volume", 0.12))
    fade_out = int(actions.get("fade_out_seconds", 4))

    async def synthesize(text: str, voice: str, rate: str, dest: Path) -> None:
        comm = edge_tts.Communicate(text, voice, rate=rate)
        await comm.save(str(dest))

    results: Dict[str, str] = {}
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

    # ------------------------------------------------------------------ briefing
    briefing_text = morning.get("briefing_script", "").strip()
    if briefing_text:
        try:
            narration = output_dir / f"briefing-{date_str}-narration.mp3"
            asyncio.run(synthesize(briefing_text, briefing_voice, "+0%", narration))
            music_path = select_music_for_mood("energizing", music_lib)
            if music_path:
                final = output_dir / f"briefing-{date_str}.mp3"
                mix_meditation_with_music(narration, music_path, final,
                                          music_volume=music_volume,
                                          fade_out_seconds=fade_out)
                narration.unlink(missing_ok=True)
                print(f"[morning] briefing audio → {final.name}")
            else:
                final = output_dir / f"briefing-{date_str}.mp3"
                narration.rename(final)
                print(f"[morning] briefing audio (no music) → {final.name}")
            results["briefing_audio"] = str(final)
        except Exception as exc:
            print(f"[morning] briefing audio failed: {exc}")

    # ---------------------------------------------------------------- reflection
    reflection_text = morning.get("reflection_script", "").strip()
    if reflection_text:
        try:
            ref_path = output_dir / f"reflection-{date_str}.mp3"
            asyncio.run(synthesize(reflection_text, reflection_voice, "+0%", ref_path))
            print(f"[morning] reflection audio → {ref_path.name}")
            results["reflection_audio"] = str(ref_path)
        except Exception as exc:
            print(f"[morning] reflection audio failed: {exc}")

    # ----------------------------------------------------------- combined podcast
    if "briefing_audio" in results and "reflection_audio" in results and actions.get("combine_podcast", True):
        try:
            # Short spoken bridge between segments
            bridge_text = (
                f"That's your briefing for today. "
                f"Now, a reflection on the themes of your week."
            )
            bridge_path = output_dir / f"bridge-{date_str}.mp3"
            asyncio.run(synthesize(bridge_text, reflection_voice, "-10%", bridge_path))

            # Write ffmpeg concat list
            concat_list = output_dir / f"concat-{date_str}.txt"
            segments = [
                results["briefing_audio"],
                str(bridge_path),
                results["reflection_audio"],
            ]
            concat_list.write_text(
                "\n".join(f"file '{s}'" for s in segments), encoding="utf-8"
            )
            daily_path = output_dir / f"daily-{date_str}.mp3"
            cmd = [
                ffmpeg, "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c:a", "libmp3lame", "-b:a", "128k",
                str(daily_path),
            ]
            result_proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            bridge_path.unlink(missing_ok=True)
            concat_list.unlink(missing_ok=True)
            if result_proc.returncode == 0:
                print(f"[morning] daily podcast → {daily_path.name}")
                results["daily_audio"] = str(daily_path)
            else:
                print(f"[morning] ffmpeg combine failed: {result_proc.stderr.strip()}")
        except Exception as exc:
            print(f"[morning] combine failed: {exc}")

    return results


def write_last_run(config: Dict[str, Any], results: List[ProcessResult]) -> None:
    """Write runtime/last-run.json after every poll cycle for observability."""
    runtime_dir = Path(config["runtime_dir"])
    runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": iso_now(),
        "files_processed": len(results),
        "last_status": results[-1].status if results else "idle",
        "last_error": next((r.error for r in reversed(results) if r.error), None),
        "results": [r.__dict__ for r in results],
    }
    write_json(runtime_dir / "last-run.json", payload)


def run_lane_actions(
    config: Dict[str, Any],
    workflow: str,
    source_path: Path,
    transcript: str,
    ai_payload: Dict[str, Any],
    note_path: Path,
    entry_id: str = "",
    file_mtime: Optional[datetime] = None,
    archived_audio_path: Optional[Path] = None,
) -> None:
    """Execute any downstream actions configured for a workflow lane.

    Currently supports:
      append_jsonl: path — appends a structured entry to a JSONL intake file.

    Schema (v1):
      schema_version, id, timestamp, workflow, title, summary,
      action_items, tags, transcript, note_path, source_audio_path

    Note: source_path may no longer exist on disk when this is called (the
    file has been moved to the archive). Do NOT call source_path.stat() here.
    Use file_mtime (captured before the move) for the timestamp.
    """
    lane_actions = config.get("lane_actions", {})
    actions = lane_actions.get(workflow, {})
    if not actions:
        return

    append_path_raw = actions.get("append_jsonl")
    if append_path_raw:
        append_path = Path(os.path.expanduser(append_path_raw))
        append_path.parent.mkdir(parents=True, exist_ok=True)
        title = note_title(source_path, ai_payload)
        # Use the pre-captured mtime; fall back to now() if not provided
        created_at = file_mtime or datetime.now().astimezone().replace(microsecond=0)
        entry = {
            "schema_version": 1,
            "id": entry_id,
            "timestamp": created_at.isoformat(),
            "workflow": workflow,
            "title": title,
            "summary": ai_payload.get("summary", "").strip(),
            "action_items": ai_payload.get("action_items", []),
            "tags": ai_payload.get("suggested_tags", []),
            "transcript": transcript.strip(),
            "note_path": str(note_path),
            "source_audio_path": str(archived_audio_path) if archived_audio_path else "",
        }
        with append_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Professional coaching analysis — triggered by generate_coaching_note: true
    if actions.get("generate_coaching_note"):
        context = load_context_feeds(actions)
        coaching = run_ai_coaching_analysis(config, transcript, context)
        coaching_path = write_coaching_note(
            config,
            source_note_path=note_path,
            transcript=transcript,
            file_mtime=file_mtime or datetime.now().astimezone().replace(microsecond=0),
            coaching=coaching,
        )
        if coaching_path:
            print(f"[coaching] note → {coaching_path}")

    # Wisdom synthesis — triggered by generate_wisdom_note: true in lane_actions
    if actions.get("generate_wisdom_note"):
        passages = load_corpus_passages(config)
        if passages:
            context = load_context_feeds(actions)
            synthesis = run_ai_wisdom_synthesis(config, transcript, passages, context)
            wisdom_path = write_wisdom_note(
                config,
                source_note_path=note_path,
                transcript=transcript,
                file_mtime=file_mtime or datetime.now().astimezone().replace(microsecond=0),
                synthesis=synthesis,
            )
            if wisdom_path:
                generate_wisdom_audio(actions, synthesis, wisdom_path)

    # Morning briefing — triggered by generate_daily_briefing: true in lane_actions
    if actions.get("generate_daily_briefing"):
        professional_context = load_context_feeds(actions)
        lookback = int(actions.get("reflection_lookback_days", 7))
        recent_notes = load_recent_wisdom_notes(config, lookback_days=lookback)
        morning = run_ai_morning(config, transcript, professional_context, recent_notes)
        if morning:
            date_str = (file_mtime or datetime.now().astimezone()).strftime("%Y-%m-%d")
            generate_morning_audio(actions, morning, date_str)


def is_locally_readable(path: Path) -> bool:
    """Return False if the file is an iCloud stub that has not been downloaded yet.

    Two checks, in order:
    1. Shadow file — macOS creates ".{name}.icloud" next to stubs that are
       fully evicted. If that exists, the file has no local bytes.
    2. Read probe — attempt to open the file and read 1 byte. iCloud VFS
       raises OSError (errno 11 EDEADLK or errno 6 ENXIO) when the file
       is not yet materialised on disk. Any OSError means "skip for now".
    """
    shadow = path.parent / f".{path.name}.icloud"
    if shadow.exists():
        return False
    try:
        with path.open("rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


def move_to_archive(source_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(source_path), str(destination)))


def move_to_failed(config: Dict[str, Any], source_path: Path) -> Path:
    destination = fail_destination(config, source_path)
    return move_to_archive(source_path, destination)


def process_file(config: Dict[str, Any], source_path: Path, lane: str = "batch") -> ProcessResult:
    # archive_path is computed after AI summarization so the filename reflects
    # the note title rather than the original (often meaningless) iPhone filename.
    archive_path: Optional[Path] = None
    transcript: Optional[str] = None
    ai_payload: Dict[str, Any] = {
        "title": "",
        "summary": "",
        "action_items": [],
        "suggested_tags": [],
    }
    note_path: Optional[Path] = None
    metadata_path: Optional[Path] = None
    archived_audio_path: Optional[Path] = None
    gpt_packet_path: Optional[Path] = None

    try:
        # Minimum audio size check — reject obvious accidental recordings before transcription
        min_bytes = int(config.get("min_audio_bytes", 4096))
        try:
            audio_bytes = source_path.stat().st_size
        except FileNotFoundError:
            audio_bytes = 0
        if audio_bytes < min_bytes:
            raise RuntimeError(
                f"Audio file too small ({audio_bytes} bytes < {min_bytes} minimum). "
                "Likely an accidental or empty recording."
            )

        transcript = transcribe_audio(config, source_path)

        # Minimum transcript length check — reject near-silent or noise-only recordings
        min_words = int(config.get("min_transcript_words", 3))
        word_count = len(transcript.split()) if transcript else 0
        if word_count < min_words:
            raise RuntimeError(
                f"Transcript too short ({word_count} words < {min_words} minimum). "
                "Likely an accidental or empty recording."
            )

        workflow = "default"
        routing_reason = "fallback"
        if transcript:
            workflow, routing_reason, transcript = detect_workflow(transcript, config)
            ai_payload = run_ai(config, transcript, workflow)
        # Compute archive path now that we have the AI title — audio file gets
        # a slug filename that matches the Obsidian note instead of the iPhone name.
        title = note_title(source_path, ai_payload)
        archive_path = archive_destination(config, source_path, title=title)
        note_path = write_note(
            config, source_path, archive_path, transcript, ai_payload, workflow, routing_reason
        )
        # Capture all stat()-derived values before the move — the file will
        # not exist at source_path once move_to_archive() is called.
        entry_id = source_key(source_path)
        file_mtime = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone().replace(microsecond=0)
        archived_audio_path = move_to_archive(source_path, archive_path)
        run_lane_actions(
            config, workflow, source_path, transcript, ai_payload, note_path,
            entry_id=entry_id,
            file_mtime=file_mtime,
            archived_audio_path=archived_audio_path,
        )
        if lane == "gpt" and config["gpt_handoff"].get("enabled"):
            gpt_packet_path = write_gpt_packet(
                config=config,
                source_path=source_path,
                archived_audio_path=archived_audio_path,
                transcript=transcript,
                ai_payload=ai_payload,
                note_path=note_path,
            )
        metadata_path = write_metadata(
            config,
            lane=lane,
            source_path=source_path,
            archived_audio_path=archived_audio_path,
            note_path=note_path,
            transcript=transcript,
            ai_payload=ai_payload,
            gpt_packet_path=gpt_packet_path,
        )
        return ProcessResult(
            status="done",
            lane=lane,
            source_path=str(source_path),
            archived_audio_path=str(archived_audio_path),
            note_path=str(note_path),
            metadata_path=str(metadata_path),
            gpt_packet_path=str(gpt_packet_path) if gpt_packet_path else None,
            error=None,
        )
    except Exception as exc:  # pylint: disable=broad-except
        error_text = str(exc)
        if source_path.exists():
            archived_audio_path = move_to_failed(config, source_path)
        return ProcessResult(
            status="error",
            lane=lane,
            source_path=str(source_path),
            archived_audio_path=str(archived_audio_path) if archived_audio_path else None,
            note_path=str(note_path) if note_path else None,
            metadata_path=str(metadata_path) if metadata_path else None,
            gpt_packet_path=str(gpt_packet_path) if gpt_packet_path else None,
            error=error_text,
        )


def process_pending(config: Dict[str, Any]) -> List[ProcessResult]:
    ensure_runtime_dirs(config)
    state = load_state(config)
    processed = state.setdefault("processed", {})
    # size_snapshots: path -> size from previous poll, used to confirm file is stable
    size_snapshots = state.setdefault("size_snapshots", {})
    # content_hashes: set of SHA-256 digests of already-processed files for dedup
    seen_hashes: set = set(state.setdefault("content_hashes", []))

    # Prune stale size snapshot entries for files that no longer exist
    stale_keys = [k for k in size_snapshots if not Path(k).exists()]
    for k in stale_keys:
        del size_snapshots[k]

    candidates = candidate_files(config)
    needs_state_save = bool(stale_keys)

    results: List[ProcessResult] = []
    for candidate in candidates:
        path_key = str(candidate.path)
        try:
            current_size = candidate.path.stat().st_size
        except FileNotFoundError:
            continue

        # iCloud materialization gate: skip files whose bytes are not yet
        # resident on disk (stubs, in-progress downloads, evicted files).
        # We do this BEFORE the size-stability check because a stub can
        # report a stable size while having no local data at all.
        if not is_locally_readable(candidate.path):
            continue

        # Size-stability check: skip if size differs from last poll snapshot
        last_size = size_snapshots.get(path_key)
        size_snapshots[path_key] = current_size
        if last_size != current_size:
            # First time seen, or still growing — revisit next poll
            needs_state_save = True
            continue

        # Content-hash deduplication: skip if we have already processed this exact file
        try:
            chash = content_hash(candidate.path)
        except OSError:
            continue
        if chash in seen_hashes:
            continue

        key = source_key(candidate.path)
        existing = processed.get(key)
        if existing and existing.get("status") == "done":
            continue

        result = process_file(config, candidate.path, lane=candidate.lane)
        processed[key] = {
            "status": result.status,
            "lane": result.lane,
            "source_path": result.source_path,
            "archived_audio_path": result.archived_audio_path,
            "note_path": result.note_path,
            "metadata_path": result.metadata_path,
            "gpt_packet_path": result.gpt_packet_path,
            "error": result.error,
            "recorded_at": iso_now(),
        }
        if result.status == "done":
            seen_hashes.add(chash)
            state["content_hashes"] = list(seen_hashes)
        save_state(config, state)
        results.append(result)

    if needs_state_save:
        save_state(config, state)

    write_last_run(config, results)
    return results


def command_watch(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    if args.once:
        results = process_pending(config)
        for item in results:
            print(json.dumps(item.__dict__, ensure_ascii=False))
        return 0

    poll_seconds = int(config.get("poll_seconds", 30))
    while True:
        results = process_pending(config)
        for item in results:
            print(json.dumps(item.__dict__, ensure_ascii=False))
        time.sleep(poll_seconds)


def command_process_file(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    ensure_runtime_dirs(config)
    source_path = Path(args.source_path).expanduser().resolve()
    result = process_file(config, source_path, lane=args.lane)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0 if result.status == "done" else 1


def command_validate(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    ensure_runtime_dirs(config)

    checks = []
    transcription = config["transcription"]
    ai = config["ai"]

    checks.append(("raw_audio_dir", Path(config["raw_audio_dir"]).exists()))
    checks.append(("archive_audio_dir", Path(config["archive_audio_dir"]).exists()))
    checks.append(("failed_audio_dir", Path(config["failed_audio_dir"]).exists()))
    checks.append(("obsidian_inbox_dir", Path(config["obsidian_inbox_dir"]).exists()))
    checks.append(("note_template_path", Path(config["note_template_path"]).exists()))
    if config.get("tag_reference_dirs"):
        checks.append(("tag_reference_dirs", all(Path(item).exists() for item in config["tag_reference_dirs"])))
    if config["gpt_handoff"].get("enabled"):
        checks.append(("gpt template", Path(config["gpt_handoff"]["template_path"]).exists()))
        checks.append(("gpt export dir", Path(config["gpt_handoff"]["export_dir"]).exists()))
        if config["gpt_handoff"].get("audio_dir"):
            checks.append(("gpt audio dir", Path(config["gpt_handoff"]["audio_dir"]).exists()))

    if transcription["backend"] == "whisper_cpp":
        checks.append(("whisper command", command_exists(transcription["command"])))
        checks.append(("model_path", bool(transcription.get("model_path"))))
    elif transcription["backend"] == "command":
        checks.append(("command_template", bool(transcription.get("command_template"))))

    if ai.get("enabled", True) and ai.get("backend") == "ollama_http":
        checks.append(("ollama model configured", ai.get("model") != "replace-with-installed-model"))
    if ai.get("enabled", True) and ai.get("backend") == "openai_http":
        has_key = bool(ai.get("api_key") or os.environ.get("OPENAI_API_KEY", ""))
        checks.append(("openai api_key set", has_key))

    failed = False
    for label, ok in checks:
        status = "OK" if ok else "MISSING"
        print(f"{status:8} {label}")
        if not ok:
            failed = True

    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process voice notes into Obsidian inbox notes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    watch_parser = subparsers.add_parser("watch", help="Process all stable files in the raw folder.")
    watch_parser.add_argument("--config", required=True, help="Path to config.json")
    watch_parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    watch_parser.set_defaults(func=command_watch)

    file_parser = subparsers.add_parser("process-file", help="Process one specific audio file.")
    file_parser.add_argument("--config", required=True, help="Path to config.json")
    file_parser.add_argument("--lane", choices=["batch", "gpt"], default="batch", help="Processing lane")
    file_parser.add_argument("source_path", help="Path to the audio file")
    file_parser.set_defaults(func=command_process_file)

    validate_parser = subparsers.add_parser("validate", help="Check config paths and key dependencies.")
    validate_parser.add_argument("--config", required=True, help="Path to config.json")
    validate_parser.set_defaults(func=command_validate)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
