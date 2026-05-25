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


def archive_destination(config: Dict[str, Any], source_path: Path) -> Path:
    archive_root = Path(config["archive_audio_dir"])
    if config.get("archive_subdirs_by_date", True):
        timestamp = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone()
        archive_root = archive_root / timestamp.strftime("%Y") / timestamp.strftime("%m")
    archive_root.mkdir(parents=True, exist_ok=True)
    return unique_path(archive_root / source_path.name)


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

BUILTIN_LANE_KEYWORDS: Dict[str, List[str]] = {
    "reflect": [
        "feeling", "feelings", "insight", "insights", "pattern", "patterns",
        "gratitude", "philosophical", "meditation", "mindset", "emotion",
        "awareness", "therapy", "intention", "journal",
    ],
    "professional": [
        "meeting", "networking", "opportunity", "career", "colleague",
        "client", "hire", "interview", "salary", "resume", "linkedin",
        "contract", "proposal", "follow up", "follow-up",
    ],
    "build": [
        "code", "bug", "feature", "deploy", "refactor", "architecture",
        "engineering", "sprint", "pull request", "github", "function",
        "api", "database", "frontend", "backend", "typescript", "python",
    ],
}


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


def collect_preferred_tags(config: Dict[str, Any], limit: int = 25) -> List[str]:
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
    return [tag for tag, _count in ranked[:limit]]


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


LANE_AI_INSTRUCTIONS: Dict[str, str] = {
    "reflect": (
        "This is a personal reflection or philosophical note. "
        "Focus on insights, recurring patterns, emotional themes, and any intentions or realisations expressed."
    ),
    "professional": (
        "This is a professional note. "
        "Extract people mentioned, companies, opportunities, relationship context, and concrete next steps."
    ),
    "build": (
        "This is a software or product development note. "
        "Extract technical decisions, open questions, blockers, ideas, and specific action items."
    ),
    "default": "",
}


def ai_prompt(transcript: str, max_tags: int, preferred_tags: List[str], workflow: str = "default") -> str:
    preferred_tags_block = ""
    if preferred_tags:
        preferred_tags_block = (
            "Prefer these existing tags when they genuinely fit the transcript:\n"
            f"{', '.join(preferred_tags)}\n\n"
        )

    lane_instruction = LANE_AI_INSTRUCTIONS.get(workflow, "")
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
        "- Tags should describe likely future retrieval, not every topic.\n"
        "- Make the title specific but concise.\n\n"
        f"{lane_block}"
        f"{preferred_tags_block}"
        "Transcript:\n"
        f"{transcript}\n"
    )


def run_ai_ollama_http(config: Dict[str, Any], transcript: str, workflow: str = "default") -> Dict[str, Any]:
    ai = config["ai"]
    preferred_tags = collect_preferred_tags(config)
    payload = {
        "model": ai["model"],
        "prompt": ai_prompt(transcript, int(ai.get("max_tags", 5)), preferred_tags, workflow),
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
            {"role": "user", "content": ai_prompt(transcript, int(ai.get("max_tags", 5)), preferred_tags, workflow)}
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
) -> None:
    """Execute any downstream actions configured for a workflow lane.

    Currently supports:
      append_jsonl: path — appends a formatted entry to a JSONL intake file.
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
        created_at = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone().replace(microsecond=0)
        action_items = ai_payload.get("action_items", [])
        action_items_text = "\n".join(f"- {item}" for item in action_items) if action_items else "None"
        content = (
            f"Voice note ({workflow}) — {created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"Title: {title}\n\n"
            f"Summary: {ai_payload.get('summary', '').strip() or 'None'}\n\n"
            f"Action items:\n{action_items_text}\n\n"
            f"Transcript:\n{transcript.strip()}"
        )
        entry = json.dumps(
            {"role": "user", "content": content, "source": "memnon", "created": created_at.isoformat()},
            ensure_ascii=False,
        )
        with append_path.open("a", encoding="utf-8") as f:
            f.write(entry + "\n")


def move_to_archive(source_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(source_path), str(destination)))


def move_to_failed(config: Dict[str, Any], source_path: Path) -> Path:
    destination = fail_destination(config, source_path)
    return move_to_archive(source_path, destination)


def process_file(config: Dict[str, Any], source_path: Path, lane: str = "batch") -> ProcessResult:
    archive_path = archive_destination(config, source_path)
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
        note_path = write_note(
            config, source_path, archive_path, transcript, ai_payload, workflow, routing_reason
        )
        run_lane_actions(config, workflow, source_path, transcript, ai_payload, note_path)
        if lane == "gpt" and config["gpt_handoff"].get("enabled"):
            gpt_packet_path = write_gpt_packet(
                config=config,
                source_path=source_path,
                archived_audio_path=archive_path,
                transcript=transcript,
                ai_payload=ai_payload,
                note_path=note_path,
            )
        archived_audio_path = move_to_archive(source_path, archive_path)
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
