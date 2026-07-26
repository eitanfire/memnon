from __future__ import annotations

from datetime import datetime
import re


DAILY_FEED_NOTES_COLLECTION = "daily_feed_notes"


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_reflection_style(value: object) -> str:
    normalized = _normalize_text(value).lower()
    if normalized in {"grounded", "practical", "complete"}:
        return normalized
    return "complete"


def _resolve_include_teaching_context(
    profile: dict[str, object],
    include_teaching_context: bool | None,
) -> bool:
    if include_teaching_context is not None:
        return bool(include_teaching_context)
    persisted = profile.get("include_teaching_context")
    if isinstance(persisted, bool):
        return persisted
    return True


def _format_note_date(created_at: object) -> str:
    normalized = _normalize_text(created_at)
    if not normalized:
        return ""
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return normalized[:10] if len(normalized) >= 10 else normalized
    return parsed.date().isoformat()


def _artifact_section_text(artifact: dict[str, object], label: str) -> str:
    for section in artifact.get("sections") or []:
        if _normalize_text((section or {}).get("label")).lower() == label:
            return _normalize_text((section or {}).get("text"))
    return ""


def build_daily_feed_continuity_note(
    *,
    capture_record: dict[str, object],
    profile: dict[str, object],
    include_teaching_context: bool | None,
) -> dict[str, object]:
    source_event = dict(capture_record.get("source_event") or {})
    result_payload = dict(capture_record.get("result") or {})
    artifact = dict(
        result_payload.get("primary_artifact")
        or result_payload.get("saved_note_artifact")
        or {}
    )

    title = _normalize_text(artifact.get("title")) or "Saved result"
    summary = (
        _normalize_text(artifact.get("framing_line"))
        or _normalize_text(artifact.get("summary"))
        or _normalize_text(result_payload.get("interpretation_line"))
        or _normalize_text(source_event.get("source_preview"))
    )
    insight = (
        _normalize_text(artifact.get("summary"))
        or _artifact_section_text(artifact, "key point")
        or _artifact_section_text(artifact, "why keep this")
        or _normalize_text(artifact.get("source_excerpt"))
        or _normalize_text(source_event.get("source_preview"))
        or title
    )
    next_step = _artifact_section_text(artifact, "next step")
    themes = list(
        dict.fromkeys(
            [
                _normalize_text(item)
                for item in (
                    (capture_record.get("routing") or {}).get("likely_themes")
                    or result_payload.get("likely_themes")
                    or []
                )
                if _normalize_text(item)
            ]
        )
    )

    history_parts = [title, summary, insight]
    if next_step:
        history_parts.append(next_step)
    history_source_text = " | ".join(part for part in history_parts if part)

    created_at = _normalize_text(capture_record.get("created_at"))
    return {
        "title": title,
        "summary": summary[:300],
        "insight": insight[:240],
        "date": _format_note_date(created_at),
        "created_at": created_at,
        "themes": themes[:8],
        "history_source_text": history_source_text[:1200],
        "reflection_style": _normalize_reflection_style(profile.get("reflection_style")),
        "include_teaching_context": _resolve_include_teaching_context(
            profile,
            include_teaching_context,
        ),
        "bridge_capture_id": _normalize_text(capture_record.get("capture_id")),
        "bridge_origin": "workflow_capture",
        "input_type": _normalize_text(
            source_event.get("input_type") or capture_record.get("input_type")
        ) or "text",
    }


def write_firestore_continuity_note(
    *,
    db,
    uid: str,
    capture_record: dict[str, object],
    profile: dict[str, object],
    include_teaching_context: bool | None,
) -> dict[str, object]:
    payload = build_daily_feed_continuity_note(
        capture_record=capture_record,
        profile=profile,
        include_teaching_context=include_teaching_context,
    )
    capture_id = _normalize_text(capture_record.get("capture_id")) or "capture"
    (
        db.collection("users")
        .document(uid)
        .collection(DAILY_FEED_NOTES_COLLECTION)
        .document(capture_id)
        .set(payload, merge=True)
    )
    return payload
