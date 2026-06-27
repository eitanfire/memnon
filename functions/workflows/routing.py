from __future__ import annotations

import re
from typing import Any

from lanes import extract_themes

from .models import WorkflowDecision


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def build_source_event(source_text: str, context_hint: str, capture_id: str, created_at: str) -> dict[str, Any]:
    normalized = _normalize_text(source_text)
    preview = normalized[:240]
    themes = sorted(extract_themes(f"{normalized} {context_hint}".strip()))[:6]
    return {
        "capture_id": capture_id,
        "input_type": "text",
        "source_text": normalized,
        "source_preview": preview,
        "context_hint": _normalize_text(context_hint),
        "likely_themes": themes,
        "created_at": created_at,
    }


def route_text_capture(source_text: str, context_hint: str, profile: dict[str, Any]) -> WorkflowDecision:
    normalized = _normalize_text(source_text)
    context = _normalize_text(context_hint)
    words = normalized.split()
    themes = sorted(extract_themes(f"{normalized} {context}".strip()))[:6]
    lane = _normalize_text(str(profile.get("lane") or "professional")) or "professional"
    profession = _normalize_text(str(profile.get("profession") or "professional")) or "professional"

    rich_enough = len(words) >= 12 or bool(context)
    if lane == "reflect" and not context:
        rich_enough = rich_enough and len(words) >= 18

    if not rich_enough:
        return WorkflowDecision(
            route_kind="saved_note",
            interpretation_line="Saved as a note with likely themes.",
            primary_artifact_kind="",
            secondary_artifact_kinds=[],
            likely_themes=themes,
        )

    interpretation_line = "This looks like a professional note worth shaping."
    if lane != "professional":
        interpretation_line = f"This looks like a {profession or 'professional'} note worth shaping."

    return WorkflowDecision(
        route_kind="direct_professional_note",
        interpretation_line=interpretation_line,
        primary_artifact_kind="professional_note",
        secondary_artifact_kinds=[],
        likely_themes=themes,
    )
