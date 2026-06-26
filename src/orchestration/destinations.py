from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import AnalysisResult, ArtifactBundle, ReviewQueueEntry, SourceEvent, WorkflowJob


def _write_text(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def write_artifact_bundle(
    job: WorkflowJob,
    event: SourceEvent,
    analysis: AnalysisResult,
    config: dict[str, Any],
) -> ArtifactBundle:
    slug = event.source_event_id
    if job.workflow_type in {"reflect_note_bundle", "professional_note_bundle"}:
        bundle_dir = Path(config["note_bundles_dir"]) / job.workflow_type / slug
        files = [
            _write_json(
                bundle_dir / "bundle.json",
                {
                    "source_event_id": event.source_event_id,
                    "workflow_type": job.workflow_type,
                    "note_path": event.note_path,
                    "title": event.title,
                },
            ),
        ]
        return ArtifactBundle(job.workflow_type, str(bundle_dir), files)

    if job.workflow_type == "research_note":
        bundle_dir = Path(config["research_dir"]) / slug
        files = [
            _write_json(
                bundle_dir / "note.json",
                {
                    "source_event_id": event.source_event_id,
                    "title": event.title,
                    "summary": event.summary,
                    "research_signals": analysis.research_signals,
                    "product_feedback": analysis.product_feedback,
                    "source_note_path": event.note_path,
                },
            ),
        ]
        return ArtifactBundle(
            job.workflow_type,
            str(bundle_dir),
            files,
            notes=["append-only local research artifact"],
        )

    if job.workflow_type == "follow_up_bundle":
        bundle_dir = Path(config["outbox_dir"]) / slug
        files = [
            _write_text(
                bundle_dir / "email.md",
                f"Subject: Follow-up on {event.title}\n\nI'd love to continue the conversation.",
            ),
            _write_text(bundle_dir / "slack.md", f"Following up on {event.title}."),
            _write_text(bundle_dir / "discord.md", f"Quick follow-up on {event.title}."),
            _write_text(bundle_dir / "linkedin-dm.md", f"Enjoyed the conversation around {event.title}."),
            _write_text(bundle_dir / "text.md", f"Following up on {event.title}."),
        ]
        return ArtifactBundle(
            job.workflow_type,
            str(bundle_dir),
            files,
            notes=["drafts only; do not auto-send"],
        )

    raise ValueError(f"Unsupported local bundle type: {job.workflow_type}")


def write_event_manifest(
    event: SourceEvent,
    analysis: AnalysisResult,
    suppressors: list[str],
    hard_rules: list[str],
    llm_output: dict[str, Any],
    jobs: list[WorkflowJob],
    bundles: list[ArtifactBundle],
    config: dict[str, Any],
) -> str:
    return _write_json(
        Path(config["manifests_dir"]) / f"{event.source_event_id}.json",
        {
            "source_event": event.to_dict(),
            "analysis": analysis.to_dict(),
            "applied_suppressors": suppressors,
            "applied_hard_rules": hard_rules,
            "llm_output": llm_output,
            "workflow_jobs": [job.to_dict() for job in jobs],
            "artifact_bundles": [bundle.to_dict() for bundle in bundles],
            "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        },
    )


def write_review_queue_entry(
    event: SourceEvent,
    jobs: list[WorkflowJob],
    bundles: list[ArtifactBundle],
    config: dict[str, Any],
) -> str:
    payload = ReviewQueueEntry(
        source_event_id=event.source_event_id,
        created_at=datetime.now().astimezone().replace(microsecond=0).isoformat(),
        workflow_jobs=[job.to_dict() for job in jobs],
        artifacts_generated=[bundle.output_path for bundle in bundles],
        needs_review=any(job.needs_review for job in jobs),
        review_priority="high" if any(job.review_priority == "high" for job in jobs) else "normal",
        external_drafts_pending=any(
            job.workflow_type in {"follow_up_bundle", "boulderjs_recap_packet"} for job in jobs
        ),
        forced_by_rule_jobs=[job.workflow_type for job in jobs if job.forced_by_rule],
        notes=[],
    )
    return _write_json(Path(config["review_queue_dir"]) / f"{event.source_event_id}.json", payload.to_dict())
