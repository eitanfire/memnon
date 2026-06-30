from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

WorkflowType = Literal[
    "reflect_note_bundle",
    "professional_note_bundle",
    "research_note",
    "boulderjs_recap_packet",
    "follow_up_bundle",
]


@dataclass
class SourceEvent:
    source_event_id: str
    lane: str
    workflow: str
    routing_reason: str
    title: str
    transcript: str
    transcript_path: str
    transcript_preview: str
    note_path: str
    archived_audio_path: str
    metadata_path: str
    processed_at: str
    summary: str
    action_items: list[str]
    suggested_tags: list[str]
    source_project: str = ""
    source_context: str = ""
    source_links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    event_type: str
    named_people: list[str] = field(default_factory=list)
    named_orgs: list[str] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    follow_up_requests: list[str] = field(default_factory=list)
    product_feedback: list[str] = field(default_factory=list)
    research_signals: list[str] = field(default_factory=list)
    publishable_angles: list[str] = field(default_factory=list)
    reflection_signals: list[str] = field(default_factory=list)
    professional_signals: list[str] = field(default_factory=list)
    llm_reasoning: str = ""
    llm_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowJob:
    workflow_type: WorkflowType
    confidence: float
    reason: str
    status: str
    destination: str
    needs_review: bool = False
    review_priority: str = "normal"
    forced_by_rule: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactBundle:
    workflow_type: WorkflowType
    output_path: str
    files: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewQueueEntry:
    source_event_id: str
    created_at: str
    workflow_jobs: list[dict[str, Any]]
    artifacts_generated: list[str]
    needs_review: bool
    review_priority: str
    external_drafts_pending: bool
    forced_by_rule_jobs: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrchestrationResult:
    source_event: SourceEvent
    analysis_result: AnalysisResult | None = None
    workflow_jobs: list[WorkflowJob] = field(default_factory=list)
    artifact_bundles: list[ArtifactBundle] = field(default_factory=list)
    review_queue_entry: ReviewQueueEntry | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
