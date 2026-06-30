from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from .models import AnalysisResult, ArtifactBundle, SourceEvent, WorkflowJob


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def _write_text(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def write_boulderjs_recap_packet(
    job: WorkflowJob,
    event: SourceEvent,
    analysis: AnalysisResult,
    config: dict[str, Any],
) -> ArtifactBundle:
    packet_dir = Path(config["boulderjs_dir"]) / event.source_event_id
    packet_dir.mkdir(parents=True, exist_ok=True)

    event_payload = {
        "number": config.get("default_boulderjs_event_number"),
        "title": event.title,
        "url": next((link for link in event.source_links if "/events/issues/" in link), ""),
        "date": "",
        "time": "",
        "location": "galvanize",
        "description": event.summary,
    }
    talk_payload = {
        "number": config.get("default_boulderjs_talk_number"),
        "title": event.title,
        "url": next((link for link in event.source_links if "/talks/issues/" in link), ""),
        "speaker": analysis.named_people[0] if analysis.named_people else "",
        "speakerName": analysis.named_people[0] if analysis.named_people else "",
        "speakerUrl": "",
        "abstract": analysis.product_feedback[0] if analysis.product_feedback else event.summary,
        "labels": ["Talk: Feature 🎦"],
    }
    thoughts_lines = analysis.publishable_angles or analysis.product_feedback

    files = [
        _write_json(packet_dir / "event.json", event_payload),
        _write_json(packet_dir / "talk.json", talk_payload),
        _write_text(packet_dir / "abstract.txt", (talk_payload["abstract"] or "") + "\n"),
        _write_text(packet_dir / "thoughts.txt", "\n".join(thoughts_lines) + "\n"),
        _write_json(packet_dir / "source-links.json", event.source_links),
    ]

    notes = ["optional social-agent CLI handoff"]
    command = build_social_agent_command(packet_dir, config)
    if command:
        notes.append(f"draft command: {shlex.join(command)}")

    return ArtifactBundle(
        workflow_type=job.workflow_type,
        output_path=str(packet_dir),
        files=files,
        notes=notes,
    )


def build_social_agent_command(packet_dir: Path, config: dict[str, Any]) -> list[str] | None:
    if not config.get("run_social_agent_cli"):
        return None

    repo_dir = config.get("social_agent_repo_dir")
    if not repo_dir:
        return None

    event_number = config.get("default_boulderjs_event_number")
    talk_number = config.get("default_boulderjs_talk_number")
    if not event_number or not talk_number:
        return None

    return [
        "npm",
        "--prefix",
        str(repo_dir),
        "run",
        "draft",
        "--",
        f"--event={event_number}",
        f"--talk={talk_number}",
        "--post-type=recap",
        f"--talk-abstract-file={packet_dir / 'abstract.txt'}",
        f"--thought-file={packet_dir / 'thoughts.txt'}",
    ]
