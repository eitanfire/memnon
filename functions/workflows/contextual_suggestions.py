from __future__ import annotations

import re


DRAFT_SOCIAL_POST = "draft_social_post"
ANALYZE_PROFESSIONALLY = "analyze_professionally"
CONTEXTUAL_SUGGESTION_TYPES = (
    DRAFT_SOCIAL_POST,
    ANALYZE_PROFESSIONALLY,
)

_SOCIAL_EVENT_MARKERS = (
    "meetup",
    "event",
    "conference",
    "workshop",
    "talk",
    "announcement",
    "recap",
    "community update",
    "boulderjs",
)
_SOCIAL_PUBLIC_MARKERS = (
    "community",
    "members",
    "attendees",
    "everyone who came",
    "public",
    "share the highlights",
    "next week",
)
_SOCIAL_DISTRIBUTION_MARKERS = (
    "join us",
    "share",
    "register",
    "come",
    "see you",
    "tonight",
    "this week",
    "invite",
    "post the announcement",
)
_PROFESSIONAL_INPUT_MARKERS = (
    "article",
    "podcast",
    "transcript",
    "research",
    "industry",
    "notes",
)
_PROFESSIONAL_ANALYSIS_MARKERS = (
    "product strategy",
    "strategy",
    "positioning",
    "market",
    "pricing",
    "onboarding",
    "retention",
    "activation",
    "career",
    "networking",
    "product",
)
_PRIVATE_OR_SENSITIVE_MARKERS = (
    "my wife",
    "my husband",
    "my kid",
    "my kids",
    "family",
    "pickup",
    "dropoff",
    "dinner",
    "school forms",
    "household",
    "private",
    "sensitive",
    "medical",
)
_LOGISTICAL_MARKERS = (
    "logistics",
    "pickup",
    "dropoff",
    "coordinate",
    "schedule",
    "groceries",
    "dinner",
)
_EMOTIONAL_MARKERS = (
    "i feel",
    "feel raw",
    "feel upset",
    "feel hurt",
    "sit with it",
    "emotional",
    "overwhelmed",
)
_AMBIGUITY_MARKERS = (
    "not sure",
    "maybe",
    "trying to make sense",
    "hold onto",
    "something about",
    "could be",
)
_ALREADY_SOCIAL_MARKERS = (
    "#",
    "register here",
    "see you there",
    "join us at",
)
_ALREADY_ANALYSIS_MARKERS = (
    "professional analysis",
    "market analysis",
    "here's my analysis",
    "analysis of",
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _contains_any(lower_text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in lower_text for marker in markers)


def _count_any(lower_text: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in lower_text)


def _source_bundle(record: dict) -> str:
    source_event = record.get("source_event") or {}
    result = record.get("result") or {}
    artifact = result.get("primary_artifact") or {}
    parts = [
        _normalize_text(source_event.get("source_text") or ""),
        _normalize_text(source_event.get("context_hint") or record.get("context_hint") or ""),
        _normalize_text(artifact.get("title") or ""),
        _normalize_text(artifact.get("framing_line") or ""),
    ]
    return " ".join(part for part in parts if part)


def _base_suppression_reason(record: dict, lower_text: str) -> str | None:
    event_manifest = record.get("event_manifest") or {}
    suggestion_meta = event_manifest.get("contextual_suggestions") or {}
    if suggestion_meta.get("origin") == "derived_result":
        return "derived_result"

    result = record.get("result") or {}
    if result.get("route_kind") != "direct_professional_note":
        return "non_primary_result"
    if not result.get("primary_artifact"):
        return "missing_primary_artifact"

    input_type = (record.get("source_event") or {}).get("input_type") or record.get("input_type") or ""
    transcript_quality = (event_manifest.get("transcript_quality") or {}).get("quality") or ""
    if input_type == "voice" and transcript_quality != "clean":
        return "unclean_voice_transcript"

    if len(lower_text.split()) < 12:
        return "thin_capture"

    if _contains_any(lower_text, _PRIVATE_OR_SENSITIVE_MARKERS):
        return "private_or_sensitive"
    if _contains_any(lower_text, _LOGISTICAL_MARKERS):
        return "logistical_capture"
    if _contains_any(lower_text, _EMOTIONAL_MARKERS):
        return "emotional_capture"
    if _contains_any(lower_text, _AMBIGUITY_MARKERS):
        return "ambiguous_capture"

    return None


def _evaluate_social_post(lower_text: str) -> tuple[bool, str]:
    if _contains_any(lower_text, _ALREADY_SOCIAL_MARKERS):
        return False, "already_social_shaped"

    event_score = _count_any(lower_text, _SOCIAL_EVENT_MARKERS)
    public_score = _count_any(lower_text, _SOCIAL_PUBLIC_MARKERS)
    distribution_score = _count_any(lower_text, _SOCIAL_DISTRIBUTION_MARKERS)

    if event_score < 1:
        return False, "missing_event_signal"
    if public_score < 1 and distribution_score < 1:
        return False, "missing_public_distribution_signal"
    if event_score + public_score + distribution_score < 2:
        return False, "insufficient_social_signal"

    return True, ""


def _evaluate_professional_analysis(lower_text: str) -> tuple[bool, str]:
    if _contains_any(lower_text, _ALREADY_ANALYSIS_MARKERS):
        return False, "already_analysis_shaped"

    input_score = _count_any(lower_text, _PROFESSIONAL_INPUT_MARKERS)
    analysis_score = _count_any(lower_text, _PROFESSIONAL_ANALYSIS_MARKERS)
    document_signal = int("notes" in lower_text or "transcript" in lower_text or "research" in lower_text)

    if input_score < 1:
        return False, "missing_professional_input_signal"
    if analysis_score < 1:
        return False, "missing_analysis_signal"
    if input_score + analysis_score + document_signal < 2:
        return False, "insufficient_professional_signal"

    return True, ""


def evaluate_contextual_suggestions(record: dict) -> dict:
    lower_text = _source_bundle(record).lower()
    suggestion_meta = {
        "origin": ((record.get("event_manifest") or {}).get("contextual_suggestions") or {}).get("origin") or "original_capture",
        "considered_types": list(CONTEXTUAL_SUGGESTION_TYPES),
        "shown_types": [],
        "suppression_reasons": {},
    }

    base_reason = _base_suppression_reason(record, lower_text)
    if base_reason:
        for suggestion_type in CONTEXTUAL_SUGGESTION_TYPES:
            suggestion_meta["suppression_reasons"][suggestion_type] = base_reason
        return suggestion_meta

    social_allowed, social_reason = _evaluate_social_post(lower_text)
    professional_allowed, professional_reason = _evaluate_professional_analysis(lower_text)

    if social_allowed:
        suggestion_meta["shown_types"].append(DRAFT_SOCIAL_POST)
    else:
        suggestion_meta["suppression_reasons"][DRAFT_SOCIAL_POST] = social_reason

    if professional_allowed:
        suggestion_meta["shown_types"].append(ANALYZE_PROFESSIONALLY)
    else:
        suggestion_meta["suppression_reasons"][ANALYZE_PROFESSIONALLY] = professional_reason

    suggestion_meta["shown_types"] = suggestion_meta["shown_types"][:2]
    return suggestion_meta


def build_visible_contextual_suggestions(meta: dict | None) -> list[dict]:
    if not isinstance(meta, dict):
        return []
    if meta.get("origin") != "original_capture":
        return []

    shown_types = list(meta.get("shown_types") or [])[:2]
    visible = []
    for suggestion_type in shown_types:
        if suggestion_type == DRAFT_SOCIAL_POST:
            visible.append(
                {
                    "type": DRAFT_SOCIAL_POST,
                    "copy": "This could become a social post.",
                    "action_label": "Draft social post",
                }
            )
        elif suggestion_type == ANALYZE_PROFESSIONALLY:
            visible.append(
                {
                    "type": ANALYZE_PROFESSIONALLY,
                    "copy": "Analyze this through your professional lens.",
                    "action_label": "Analyze professionally",
                }
            )
    return visible
