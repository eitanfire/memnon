from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

RouteKind = Literal["direct_professional_note", "saved_note"]
ArtifactKind = Literal["professional_note", "social_post", "professional_analysis", ""]
SavedNoteState = Literal["weak_signal", "needs_direction", ""]


@dataclass
class WorkflowDecision:
    route_kind: RouteKind
    interpretation_line: str
    primary_artifact_kind: ArtifactKind
    secondary_artifact_kinds: list[ArtifactKind] = field(default_factory=list)
    likely_themes: list[str] = field(default_factory=list)
    saved_note_state: SavedNoteState = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowArtifactSection:
    label: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowArtifact:
    artifact_id: str
    kind: str
    title: str
    framing_line: str
    body: str
    status: str
    primary_action: str
    metadata_line: str = ""
    summary: str = ""
    source_excerpt: str = ""
    sections: list[WorkflowArtifactSection] = field(default_factory=list)
    copy_text: str = ""
    state: str = ""
    secondary_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowResultPayload:
    interpretation_line: str
    route_kind: RouteKind
    primary_artifact: dict[str, Any] | None
    saved_note_artifact: dict[str, Any] | None
    secondary_artifacts: list[dict[str, Any]]
    review_queue: list[dict[str, Any]]
    source_preview: str
    likely_themes: list[str]
    related_thread: dict[str, Any] | None = None
    contextual_suggestions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowCaptureRecord:
    capture_id: str
    input_type: str
    context_hint: str
    source_event: dict[str, Any]
    routing: dict[str, Any]
    result: dict[str, Any]
    event_manifest: dict[str, Any]
    created_at: str
    updated_at: str
    threading: dict[str, Any] = field(default_factory=dict)
    feedback_choice: str | None = None
    feedback_note: str | None = None
    feedback_updated_at: str | None = None
    generator: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowThreadState:
    confirmed_context_id: str | None = None
    suggested_context_id: str | None = None
    suggested_context_title: str | None = None
    suggestion_active: bool = False
    context_decision: str | None = None
    suggestion_basis: str | None = None
    suggested_at: str | None = None
    context_decision_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            key: value
            for key, value in payload.items()
            if value not in (None, False, "")
        }
