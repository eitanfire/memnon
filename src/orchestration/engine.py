from __future__ import annotations

from pathlib import Path
from typing import Any

from .analysis import analyze_source_event
from .boulderjs import write_boulderjs_recap_packet
from .config import build_orchestration_config
from .destinations import write_artifact_bundle, write_event_manifest, write_review_queue_entry
from .models import AnalysisResult
from .policies import apply_hard_rules, apply_suppressors, select_workflow_jobs
from .source_events import load_source_event


def _build_workflow_hints(analysis: AnalysisResult) -> dict[str, dict[str, object]]:
    hints: dict[str, dict[str, object]] = {}
    if analysis.follow_up_requests or analysis.commitments:
        hints["follow_up_bundle"] = {
            "confidence": 0.84,
            "reason": "explicit follow-up request",
        }
    if analysis.research_signals or analysis.product_feedback or analysis.publishable_angles:
        hints["research_note"] = {
            "confidence": 0.55,
            "reason": "product feedback adjacent",
        }
    return hints


def orchestrate_from_metadata(metadata_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    orchestration = build_orchestration_config(config)
    event = load_source_event(Path(metadata_path))
    analysis = analyze_source_event(event, config)
    suppressors = apply_suppressors(event, analysis)
    hard_rules = apply_hard_rules(event, analysis)
    llm_output = analysis.llm_hints or _build_workflow_hints(analysis)
    jobs = select_workflow_jobs(event, analysis, llm_output)

    bundles = []
    for job in jobs:
        if job.workflow_type == "boulderjs_recap_packet":
            bundles.append(write_boulderjs_recap_packet(job, event, analysis, orchestration))
        else:
            bundles.append(write_artifact_bundle(job, event, analysis, orchestration))

    manifest_path = write_event_manifest(
        event,
        analysis,
        suppressors,
        hard_rules,
        llm_output,
        jobs,
        bundles,
        orchestration,
    )
    review_queue_path = write_review_queue_entry(event, jobs, bundles, orchestration)

    return {
        "source_event_id": event.source_event_id,
        "manifest_path": manifest_path,
        "review_queue_path": review_queue_path,
        "artifact_bundles": [bundle.to_dict() for bundle in bundles],
    }
