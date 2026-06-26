from __future__ import annotations

from typing import cast

from .models import AnalysisResult, SourceEvent, WorkflowJob, WorkflowType

V1_WORKFLOW_TYPES: tuple[WorkflowType, ...] = (
    "reflect_note_bundle",
    "professional_note_bundle",
    "research_note",
    "boulderjs_recap_packet",
    "follow_up_bundle",
)


def apply_suppressors(event: SourceEvent, analysis: AnalysisResult) -> list[str]:
    suppressed: list[str] = []
    if not (analysis.named_people or analysis.named_orgs or analysis.commitments):
        suppressed.append("follow_up_bundle")
    if analysis.event_type in {"reflection", "private_reflection"}:
        suppressed.append("boulderjs_recap_packet")
    if len(event.transcript.split()) < 8:
        suppressed.append("research_note")
    return suppressed


def apply_hard_rules(event: SourceEvent, analysis: AnalysisResult) -> list[str]:
    forced: list[str] = []
    if analysis.event_type == "boulderjs_demo":
        forced.append("boulderjs_recap_packet")
    if event.workflow == "professional":
        forced.append("professional_note_bundle")
    if event.workflow == "reflect":
        forced.append("reflect_note_bundle")
    return forced


def select_workflow_jobs(
    event: SourceEvent,
    analysis: AnalysisResult,
    llm_output: dict[str, dict[str, object]],
) -> list[WorkflowJob]:
    suppressed = set(apply_suppressors(event, analysis))
    forced = set(apply_hard_rules(event, analysis)) - suppressed
    jobs: list[WorkflowJob] = []

    for workflow_type in sorted(forced):
        llm_confidence = float(llm_output.get(workflow_type, {}).get("confidence", 0.0))
        jobs.append(
            WorkflowJob(
                workflow_type=workflow_type,
                confidence=max(
                    llm_confidence,
                    1.0 if workflow_type.endswith("_note_bundle") else llm_confidence,
                ),
                reason="mandatory hard rule",
                status="ready",
                destination="runtime",
                needs_review=llm_confidence < 0.50,
                review_priority="high" if llm_confidence < 0.50 else "normal",
                forced_by_rule=llm_confidence < 0.50,
            )
        )

    for workflow_type, payload in llm_output.items():
        if workflow_type not in V1_WORKFLOW_TYPES:
            continue
        if workflow_type in suppressed or workflow_type in forced:
            continue
        confidence = float(payload.get("confidence", 0.0))
        if confidence < 0.50:
            continue
        jobs.append(
            WorkflowJob(
                workflow_type=cast(WorkflowType, workflow_type),
                confidence=confidence,
                reason=str(payload.get("reason", "")),
                status="ready",
                destination="runtime",
                needs_review=confidence < 0.80,
                review_priority="normal",
                forced_by_rule=False,
            )
        )

    return jobs
