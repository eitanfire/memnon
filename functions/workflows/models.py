from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

RouteKind = Literal["direct_professional_note", "saved_note"]
ArtifactKind = Literal["professional_note", ""]


@dataclass
class WorkflowDecision:
    route_kind: RouteKind
    interpretation_line: str
    primary_artifact_kind: ArtifactKind
    secondary_artifact_kinds: list[ArtifactKind] = field(default_factory=list)
    likely_themes: list[str] = field(default_factory=list)

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
    secondary_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowResultPayload:
    interpretation_line: str
    route_kind: RouteKind
    primary_artifact: dict[str, Any] | None
    secondary_artifacts: list[dict[str, Any]]
    review_queue: list[dict[str, Any]]
    source_preview: str
    likely_themes: list[str]

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
