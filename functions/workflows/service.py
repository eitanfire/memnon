from __future__ import annotations

from datetime import datetime
import secrets
import re

from .models import WorkflowArtifact, WorkflowArtifactSection, WorkflowCaptureRecord, WorkflowResultPayload
from .routing import build_source_event, route_text_capture


GENERIC_TITLES = {
    "suggested note",
    "professional note",
    "product idea note",
    "product note",
    "teacher workflow note",
    "memnon workflows note",
}

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


def _title_case_phrase(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip(" .,:;!-"))
    if not cleaned:
        return ""
    words = [word.capitalize() if word.islower() else word for word in cleaned.split()]
    return " ".join(words)


def _extract_source_excerpt(source_text: str, limit: int = 160) -> str:
    normalized = re.sub(r"\s+", " ", source_text or "").strip()
    if not normalized:
        return ""
    sentence_match = re.match(r"(.{1,%d}?[.!?])(?:\s|$)" % limit, normalized)
    if sentence_match:
        return sentence_match.group(1).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _looks_like_document_text(source_text: str, context_hint: str) -> bool:
    normalized = _normalize_text(source_text)
    lower = f"{normalized} {_normalize_text(context_hint)}".lower()
    words = normalized.split()
    hint_count = sum(1 for hint in DOCUMENT_HINTS if hint in lower)
    heading_like = " - " in normalized or ":" in normalized or normalized.count(".") >= 2
    return len(words) >= 18 and (hint_count >= 2 or (hint_count >= 1 and (context_hint or heading_like)))


def _sentence_list(source_text: str) -> list[str]:
    normalized = _normalize_text(source_text)
    if not normalized:
        return []
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", normalized) if sentence.strip()]


def _describe_source_type(input_type: str) -> str:
    if input_type == "text":
        return "Pasted note"
    if input_type == "voice":
        return "Voice note"
    return "Saved note"


def _format_capture_date(created_at: str) -> str:
    normalized = (created_at or "").strip()
    if not normalized:
        return ""
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.strftime("%b %d, %Y")


def _build_metadata_line(input_type: str, created_at: str, context_hint: str) -> str:
    parts = []
    source_type = _describe_source_type(input_type)
    if source_type:
        parts.append(source_type)
    formatted_date = _format_capture_date(created_at)
    if formatted_date:
        parts.append(formatted_date)
    normalized_context = re.sub(r"\s+", " ", (context_hint or "").strip())
    if normalized_context:
        normalized_context = normalized_context[0].upper() + normalized_context[1:]
    if normalized_context:
        parts.append(normalized_context)
    return " · ".join(parts)


def _normalize_clause(text: str) -> str:
    cleaned = _normalize_text(text).strip(" .,:;!-")
    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:]


def _strip_uncertainty_prefix(source_text: str) -> str:
    normalized = _normalize_text(source_text)
    cleaned = re.sub(
        r"^(?:not sure[^.?!]*[.?!]\s*|maybe[^.?!]*[.?!]\s*|trying to make sense[^.?!]*[.?!]\s*)+",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    return cleaned or normalized


def _derive_topic_phrase(source_text: str, context_hint: str) -> str:
    normalized = _strip_uncertainty_prefix(source_text)
    lower = normalized.lower()

    split_match = re.search(
        r"\b([A-Z][A-Za-z0-9_-]+)\s+splits\s+([a-z][a-z\s-]{2,30}?)\s+from\s+([a-z][a-z\s-]{2,30}?)(?:[,.!?]|$)",
        normalized,
    )
    if split_match:
        left = _title_case_phrase(split_match.group(2))
        right = _title_case_phrase(split_match.group(3))
        return f"{split_match.group(1)} {left} vs {right}"

    person_match = re.search(r"\bwith ([A-Z][a-z]+)\b", normalized)
    topic_match = re.search(r"\babout (?:the )?([a-z][a-z\s-]{3,50}?)(?:[,.!?]| and |$)", lower)
    if person_match and topic_match:
        topic = _title_case_phrase(topic_match.group(1))
        if topic:
            return f"{topic} conversation with {person_match.group(1)}"

    follow_up_match = re.search(
        r"\bfollow(?:-| )up(?: (?:with|after|on|about))? ([a-z0-9][a-z0-9\s-]{2,40}?)(?:[,.!?]|$)",
        lower,
    )
    if follow_up_match:
        detail_source = follow_up_match.group(1).strip()
        detail_lower = detail_source.lower().removeprefix("the ").strip()
        if detail_lower in {"tomorrow", "today", "soon", "later"}:
            return "Follow-up"
        if detail_lower == "meeting":
            return "Meeting follow-up"
        detail = _title_case_phrase(detail_lower)
        if detail:
            return f"Follow-up {detail}"

    if "follow up" in lower:
        return "Follow-up"

    if topic_match:
        topic = _title_case_phrase(topic_match.group(1))
        if topic:
            return topic

    if _looks_like_document_text(source_text, context_hint) and context_hint:
        return _title_case_phrase(context_hint)

    if "meeting" in lower and "follow-up" in lower:
        return "Meeting follow-up"
    if "meeting" in lower:
        return "Meeting note"
    if "product" in lower and "direction" in lower:
        return "Product direction"

    context = _title_case_phrase(context_hint)
    if context:
        return context

    words = re.findall(r"[A-Za-z0-9'-]+", normalized)
    if not words:
        return "Saved note"
    return _title_case_phrase(" ".join(words[:6]))


def derive_specific_title(source_text: str, context_hint: str, proposed_title: str, *, suffix: str = "") -> str:
    proposed = re.sub(r"\s+", " ", proposed_title or "").strip(" .")
    if proposed and proposed.lower() not in GENERIC_TITLES:
        return proposed

    topic = _derive_topic_phrase(source_text, context_hint)
    if suffix and not topic.lower().endswith(suffix.lower()):
        return f"{topic} {suffix}".strip()
    return topic


def build_copy_text(title: str, sections: list[WorkflowArtifactSection]) -> str:
    lines = [title.strip()]
    for section in sections:
        if section.text.strip():
            lines.append(f"{section.label}: {section.text.strip()}")
    return "\n\n".join(lines).strip()


def derive_next_step(source_text: str) -> str:
    normalized = _normalize_text(source_text)
    patterns = [
        r"(?:action|next step)\s*:\s*([^.!?]+)",
        r"(follow up with [^.?!]+)",
        r"(follow up [^.?!]+)",
        r"(need to [^.?!]+)",
        r"(revisit [^.?!]+)",
        r"(revise [^.?!]+)",
        r"(?:memnon|the product|this|it)\s+should\s+([^.?!]+)",
        r"should\s+([^.?!]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            text = match.group(1).strip(" .")
            if text:
                return text[0].upper() + text[1:]
    return ""


def _extract_direction_options(source_text: str, context_hint: str, likely_themes: list[str]) -> list[str]:
    normalized = _normalize_text(source_text)
    lower = normalized.lower()
    options: list[str] = []

    def add_option(option: str) -> None:
        if option and option not in options:
            options.append(option)

    if "reflection" in lower:
        add_option("a reflection")

    send_match = re.search(r"(?:send|share)\s+to\s+([A-Z][A-Za-z'-]+)", normalized)
    if send_match:
        add_option(f"a message to {send_match.group(1)}")

    if "product direction" in lower:
        add_option("a product direction note")
    if "follow up" in lower or "follow-up" in lower:
        add_option("a follow-up")
    if "note to myself" in lower or "hold onto" in lower:
        add_option("a note worth revisiting")

    if not options and likely_themes:
        add_option(f"a note about {_title_case_phrase(likely_themes[0])}")

    if not options and context_hint:
        add_option(f"a note for {_title_case_phrase(context_hint)}")

    if not options:
        add_option("a clearer next step")

    return options


def _format_direction_title(option: str) -> str:
    lowered = option.lower()
    if lowered.startswith("a "):
        option = option[2:]
    if option == "note worth revisiting":
        return "Note worth revisiting"
    return option[0].upper() + option[1:]


def _derive_ambiguous_title(source_text: str, context_hint: str, likely_themes: list[str]) -> str:
    options = _extract_direction_options(source_text, context_hint, likely_themes)
    if not options:
        return ""
    labels = [_format_direction_title(option) for option in options[:2]]
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} or {labels[1]}"


def _derive_possible_direction(source_text: str, context_hint: str, likely_themes: list[str]) -> str:
    options = _extract_direction_options(source_text, context_hint, likely_themes)

    if len(options) == 1:
        return f"This could become {options[0]}."
    if len(options) == 2:
        return f"This could become {options[0]} or {options[1]}."
    return f"This could become {options[0]}, {options[1]}, or {options[2]}."


def _looks_generic_key_point(value: str) -> bool:
    lowered = _normalize_text(value).lower()
    return not lowered or "already points toward one useful direction" in lowered


def _derive_grounded_key_point(source_text: str) -> str:
    for sentence in _sentence_list(source_text):
        for pattern in (
            r"(?:the big insight is that|big insight is that|insight is that)\s+(.+)",
            r"(?:thinks the issue is that|issue is that)\s+(.+)",
        ):
            match = re.search(pattern, sentence, flags=re.IGNORECASE)
            if match:
                return _normalize_clause(match.group(1))
        if re.search(r"\bsplits\b.+\bfrom\b", sentence, flags=re.IGNORECASE):
            return _normalize_clause(sentence.rstrip(".!?"))
    return ""


def _looks_generic_next_step(value: str) -> bool:
    lowered = _normalize_text(value).lower()
    return not lowered or "clarify the single action this note is meant to support" in lowered


def _derive_document_key_point(source_text: str, context_hint: str) -> str:
    if not _looks_like_document_text(source_text, context_hint):
        return ""
    normalized_context = _normalize_text(context_hint)
    lower = _normalize_text(source_text).lower()
    labels = []
    for hint, label in (
        ("agendas", "agendas"),
        ("agenda", "agendas"),
        ("feedback", "feedback"),
        ("directory", "reference materials"),
        ("journal", "journal notes"),
        ("notes", "notes"),
        ("summary", "summaries"),
        ("transcript", "transcript material"),
    ):
        if hint in lower and label not in labels:
            labels.append(label)

    subject = "This document"
    if "fellowship" in normalized_context.lower():
        subject = "The fellowship thread"
    elif normalized_context:
        subject = f"{_title_case_phrase(normalized_context)}"

    details = ", ".join(labels[:3]) if labels else "related materials"
    return f"{subject} pulls together {details} that belong in one reusable reference note."


def _derive_document_next_step(source_text: str, context_hint: str) -> str:
    if not _looks_like_document_text(source_text, context_hint):
        return ""
    if "fellowship" in _normalize_text(context_hint).lower():
        return "Consolidate the agendas, feedback, and reference materials into one working fellowship note."
    return "Consolidate the strongest takeaways, decisions, and reference material into one working note."


def _has_explicit_action_marker(source_text: str) -> bool:
    return bool(re.search(r"(?:action|next step)\s*:", _normalize_text(source_text), flags=re.IGNORECASE))


def _looks_extractive_next_step(source_text: str, next_step: str) -> bool:
    normalized_source = _normalize_text(source_text).lower()
    normalized_step = _normalize_text(next_step).lower().strip(".")
    return bool(normalized_step) and normalized_step in normalized_source


def _looks_generic_framing_line(value: str) -> bool:
    lowered = _normalize_text(value).lower()
    if not lowered:
        return True
    generic_markers = (
        "professional note worth shaping",
        "shaped from your note into one practical artifact",
        "practical artifact to review",
        "practical artifact",
    )
    return any(marker in lowered for marker in generic_markers)


def derive_framing_line(
    source_text: str,
    context_hint: str,
    title: str,
    key_point: str,
    next_step: str,
    proposed_framing_line: str,
) -> str:
    if not _looks_generic_framing_line(proposed_framing_line):
        return _normalize_clause(proposed_framing_line)

    normalized_context = _normalize_text(context_hint)
    normalized_title = _normalize_text(title)
    lower_source = _normalize_text(source_text).lower()

    if _looks_like_document_text(source_text, context_hint):
        if normalized_context:
            return f"A reusable reference note for {normalized_context}."
        return "A reusable reference note shaped from the material you pasted."

    if "product direction" in lower_source or "product direction" in normalized_title.lower():
        return "A saved product direction note with one concrete change to carry forward."

    if "conversation with" in normalized_title.lower():
        return f"A saved conversation note with one concrete follow-up to carry forward."

    if next_step:
        return "A saved note shaped around one concrete next step."

    if key_point:
        return "A saved note shaped around one grounded takeaway."

    return "A saved note shaped into something worth reopening."


def derive_key_point(source_text: str, context_hint: str, proposed_key_point: str) -> str:
    document_point = _derive_document_key_point(source_text, context_hint)
    if document_point and _looks_generic_key_point(proposed_key_point):
        return document_point
    if not _looks_generic_key_point(proposed_key_point):
        return _normalize_clause(proposed_key_point)
    grounded = _derive_grounded_key_point(source_text)
    if grounded:
        return grounded
    if document_point:
        return document_point
    return _normalize_clause(proposed_key_point)


def derive_artifact_next_step(source_text: str, context_hint: str, proposed_next_step: str) -> str:
    document_next_step = _derive_document_next_step(source_text, context_hint)
    if document_next_step and (
        _looks_generic_next_step(proposed_next_step)
        or (_looks_extractive_next_step(source_text, proposed_next_step) and not _has_explicit_action_marker(source_text))
    ):
        return document_next_step
    if not _looks_generic_next_step(proposed_next_step):
        return _normalize_clause(proposed_next_step)
    grounded = derive_next_step(source_text)
    if grounded:
        return grounded
    if document_next_step:
        return document_next_step
    return _normalize_clause(proposed_next_step)


class WorkflowService:
    def __init__(self, repository, note_generator, now_provider, api_key_provider):
        self.repository = repository
        self.note_generator = note_generator
        self.now_provider = now_provider
        self.api_key_provider = api_key_provider

    def create_text_capture(self, uid: str, source_text: str, context_hint: str):
        capture_id = f"cap-{secrets.token_hex(6)}"
        now = self.now_provider()
        profile = self.repository.load_user_profile(uid)
        source_event = build_source_event(source_text, context_hint, capture_id, now)
        source_event["profile_snapshot"] = profile
        decision = route_text_capture(source_text, context_hint, profile)
        metadata_line = _build_metadata_line(source_event["input_type"], now, context_hint)

        primary_artifact = None
        saved_note_artifact = None
        if decision.primary_artifact_kind == "professional_note":
            generated = self.note_generator(source_text, context_hint, profile, self.api_key_provider())
            key_point = derive_key_point(source_text, context_hint, generated.get("key_point", ""))
            next_step = derive_artifact_next_step(
                source_text,
                context_hint,
                generated.get("next_step") or derive_next_step(source_text),
            ).strip()
            sections = [WorkflowArtifactSection(label="Key point", text=key_point)]
            if next_step:
                sections.append(WorkflowArtifactSection(label="Next step", text=next_step))
            title = derive_specific_title(source_text, context_hint, generated.get("title", ""))
            if title.lower().endswith(" note"):
                title = title[:-5].rstrip()
            framing_line = derive_framing_line(
                source_text,
                context_hint,
                title,
                key_point,
                next_step,
                generated.get("framing_line", ""),
            )
            source_excerpt = _extract_source_excerpt(source_text)
            primary_artifact = WorkflowArtifact(
                artifact_id=f"{capture_id}-primary",
                kind="professional_note",
                title=title,
                framing_line=framing_line,
                body=build_copy_text(title, sections),
                status="Saved and shaped",
                primary_action="Copy",
                metadata_line=metadata_line,
                source_excerpt=source_excerpt,
                sections=sections,
                copy_text=build_copy_text(title, sections),
                secondary_actions=["Edit", "Regenerate"],
            ).to_dict()
        else:
            if decision.saved_note_state == "needs_direction":
                saved_title = _derive_ambiguous_title(source_text, context_hint, decision.likely_themes)
            else:
                saved_title = derive_specific_title(source_text, context_hint, "", suffix="note")
            saved_excerpt = _extract_source_excerpt(source_text, limit=140)
            saved_next_step = derive_next_step(source_text)
            saved_status = "Saved as a small note"
            saved_framing = "Small note, saved before it slips away."
            saved_sections = []
            if decision.saved_note_state == "weak_signal" and saved_next_step:
                saved_sections.append(
                    WorkflowArtifactSection(
                        label="Next step",
                        text=saved_next_step,
                    )
                )
            elif decision.saved_note_state == "needs_direction":
                saved_status = "Saved, needs direction"
                saved_framing = "Worth keeping. It hints at something real, but not one strong shape yet."
                saved_sections.append(
                    WorkflowArtifactSection(
                        label="Could become",
                        text=_derive_possible_direction(source_text, context_hint, decision.likely_themes),
                    )
                )
            elif decision.likely_themes:
                saved_sections.append(
                    WorkflowArtifactSection(
                        label="Themes",
                        text=", ".join(decision.likely_themes[:3]),
                    )
                )
            saved_note_artifact = WorkflowArtifact(
                artifact_id=f"{capture_id}-saved",
                kind="saved_note",
                title=saved_title,
                framing_line=saved_framing,
                body=saved_excerpt,
                status=saved_status,
                primary_action="",
                metadata_line=metadata_line,
                source_excerpt=saved_excerpt,
                sections=saved_sections,
                copy_text=build_copy_text(saved_title, saved_sections) if saved_sections else saved_excerpt,
                state=decision.saved_note_state,
                secondary_actions=[],
            ).to_dict()

        result = WorkflowResultPayload(
            interpretation_line=decision.interpretation_line,
            route_kind=decision.route_kind,
            primary_artifact=primary_artifact,
            saved_note_artifact=saved_note_artifact,
            secondary_artifacts=[],
            review_queue=[],
            source_preview=source_event["source_preview"],
            likely_themes=decision.likely_themes,
        ).to_dict()

        record = WorkflowCaptureRecord(
            capture_id=capture_id,
            input_type="text",
            context_hint=context_hint,
            source_event=source_event,
            routing=decision.to_dict(),
            result=result,
            event_manifest={
                "source_event": source_event,
                "routing": decision.to_dict(),
                "artifact_count": 1 if primary_artifact else 0,
            },
            created_at=now,
            updated_at=now,
        )
        self.repository.save_capture(uid, record)
        return record

    def get_capture(self, uid: str, capture_id: str):
        return self.repository.get_capture(uid, capture_id)
