from __future__ import annotations

import re
from typing import Any

from lanes import extract_themes

from .models import WorkflowDecision


DOCUMENT_HINTS = (
    "journal",
    "agenda",
    "agendas",
    "feedback",
    "directory",
    "transcript",
    "minutes",
    "thread",
    "outline",
    "summary",
    "notes",
    "meeting",
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _has_ambiguity_signal(normalized: str) -> bool:
    lower = normalized.lower()
    ambiguity_markers = [
        "not sure",
        "maybe",
        "could be",
        "something about",
        "hold onto",
        "trying to make sense",
    ]
    return any(marker in lower for marker in ambiguity_markers)


def _has_action_signal(normalized: str, context: str) -> bool:
    lower = f"{normalized} {context}".lower()
    action_markers = [
        "action:",
        "next step",
        "follow up",
        "need to",
        "revise",
        "revisit",
        "by end of",
        "this week",
        "before the next",
    ]
    return any(marker in lower for marker in action_markers)


def _looks_like_document_text(normalized: str, context: str) -> bool:
    lower = f"{normalized} {context}".lower()
    words = normalized.split()
    hint_count = sum(1 for hint in DOCUMENT_HINTS if hint in lower)
    heading_like = " - " in normalized or ":" in normalized or normalized.count(".") >= 2
    return len(words) >= 18 and (hint_count >= 2 or (hint_count >= 1 and (bool(context) or heading_like)))


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

    tiny_signal = len(words) <= 4 and not context
    ambiguous_signal = _has_ambiguity_signal(normalized)
    action_signal = _has_action_signal(normalized, context)
    document_like_signal = _looks_like_document_text(normalized, context)

    rich_enough = len(words) >= 12 or bool(context)
    if lane == "reflect" and not context:
        rich_enough = rich_enough and len(words) >= 18

    if tiny_signal:
        return WorkflowDecision(
            route_kind="saved_note",
            interpretation_line="Saved as a small note worth preserving.",
            primary_artifact_kind="",
            secondary_artifact_kinds=[],
            likely_themes=themes,
            saved_note_state="weak_signal",
        )

    if (ambiguous_signal and not document_like_signal) or (not rich_enough and not action_signal):
        return WorkflowDecision(
            route_kind="saved_note",
            interpretation_line="Saved as a note that may need a little direction.",
            primary_artifact_kind="",
            secondary_artifact_kinds=[],
            likely_themes=themes,
            saved_note_state="needs_direction",
        )

    if not rich_enough:
        return WorkflowDecision(
            route_kind="saved_note",
            interpretation_line="Saved as a note worth returning to.",
            primary_artifact_kind="",
            secondary_artifact_kinds=[],
            likely_themes=themes,
            saved_note_state="needs_direction",
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
