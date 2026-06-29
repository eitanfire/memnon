from __future__ import annotations

from datetime import datetime
import inspect
import secrets
import re

from .models import (
    WorkflowArtifact,
    WorkflowArtifactSection,
    WorkflowCaptureRecord,
    WorkflowDecision,
    WorkflowResultPayload,
    WorkflowThreadState,
)
from .quality import (
    best_transcript_sentence,
    has_explicit_action_signal,
    score_transcript_sentence,
    split_transcript_sentences,
    transcript_quality_check,
)
from .routing import build_source_event, route_text_capture


GENERIC_TITLES = {
    "suggested note",
    "professional note",
    "product idea note",
    "product note",
    "teacher workflow note",
    "memnon workflows note",
}

ACTION_FORWARD_TOKENS = (
    "revise",
    "revision",
    "fix",
    "update",
    "clarify",
    "rework",
    "follow up",
    "follow-up",
    "review",
    "send",
    "write",
    "finalize",
    "schedule",
)

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

EDUCATION_DOMAIN_MARKERS = (
    "teacher",
    "teachers",
    "student",
    "students",
    "class",
    "classes",
    "classroom",
    "lesson",
    "lessons",
    "school",
    "schools",
    "curriculum",
    "gradebook",
    "district",
    "principal",
    "ap computer science",
)

EDUCATION_PROFESSIONS = {
    "teacher",
    "educator",
    "education",
    "teacher leader",
    "computer science teacher",
}


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
    sentence_matches = re.findall(r"(.{1,%d}?[.!?])(?:\s|$)" % limit, normalized)
    for candidate in sentence_matches:
        excerpt = candidate.strip()
        if not _looks_like_low_signal_excerpt(excerpt):
            return excerpt
    sentence_match = re.match(r"(.{1,%d}?[.!?])(?:\s|$)" % limit, normalized)
    if sentence_match:
        return sentence_match.group(1).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _extract_voice_source_excerpt(source_text: str, limit: int = 160) -> str:
    ranked = ""
    candidates: list[tuple[int, str]] = []
    for index, sentence in enumerate(split_transcript_sentences(source_text)):
        normalized = sentence.strip()
        if not normalized or _looks_like_low_signal_excerpt(normalized) or _looks_action_like_text(normalized):
            continue
        candidates.append((score_transcript_sentence(normalized, index=index), normalized))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        ranked = candidates[0][1]

    if not ranked:
        ranked = best_transcript_sentence(source_text)
    if ranked:
        excerpt = ranked.strip()
        if len(excerpt) <= limit:
            return excerpt
        return excerpt[: limit - 1].rstrip() + "…"
    return _extract_source_excerpt(source_text, limit=limit)


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


def _looks_like_low_signal_excerpt(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return True
    words = re.findall(r"[A-Za-z0-9'-]+", normalized.lower())
    if len(words) <= 3:
        return True
    number_words = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
    if words and all(word.isdigit() or word in number_words for word in words):
        return True
    return False


def _looks_action_like_text(value: str) -> bool:
    normalized = _normalize_text(value).lower()
    if not normalized:
        return False
    if any(
        normalized.startswith(prefix)
        for prefix in ACTION_FORWARD_TOKENS
    ):
        return True
    action_patterns = (
        r"\baction\s*:",
        r"\bnext step\s*:",
        r"\bbefore the next\b",
        r"\bby end of\b",
        r"\bthis week\b",
        r"\bneed to\b",
        r"\bshould\b",
    )
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in action_patterns)


def _looks_low_quality_title(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value or "").strip(" .")
    if not normalized:
        return True
    lowered = normalized.lower()
    words = re.findall(r"[A-Za-z0-9'-]+", lowered)
    if len(words) > 9 or len(normalized) > 72:
        return True
    if _looks_action_like_text(normalized):
        return True
    weak_title_patterns = (
        r"\bahead of next\b",
        r"\bbefore the next\b",
        r"\bneeded for\b",
        r"\bneeded\b",
    )
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in weak_title_patterns)


def _source_supports_education_context(source_text: str, context_hint: str) -> bool:
    lower = f"{_normalize_text(source_text)} {_normalize_text(context_hint)}".lower()
    return any(marker in lower for marker in EDUCATION_DOMAIN_MARKERS)


def _profile_for_note_generation(profile: dict, source_text: str, context_hint: str) -> dict:
    sanitized = dict(profile or {})
    profession = _normalize_text(str(sanitized.get("profession") or "professional")).lower()
    if profession in EDUCATION_PROFESSIONS and not _source_supports_education_context(source_text, context_hint):
        sanitized["profession"] = "professional"
    return sanitized


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

    reviewer_match = re.search(
        r"\breviewer says (?:the )?([a-z][a-z\s-]{2,40}?)(?: is | feels | seems | looks | can |,| but |$)",
        lower,
    )
    if reviewer_match:
        subject = _title_case_phrase(reviewer_match.group(1))
        if subject:
            return f"{subject} review"

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

    send_match = re.search(r"(?:send|share)\s+to\s+([A-Z][A-Za-z'-]+)", normalized)
    if send_match:
        return f"Message to {send_match.group(1)}"

    if "reflection" in lower:
        return "Reflection"

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

    fallback_source = best_transcript_sentence(normalized) or normalized
    words = re.findall(r"[A-Za-z0-9'-]+", fallback_source)
    if not words:
        return "Saved note"
    return _title_case_phrase(" ".join(words[:6]))


def derive_specific_title(source_text: str, context_hint: str, proposed_title: str, *, suffix: str = "") -> str:
    proposed = re.sub(r"\s+", " ", proposed_title or "").strip(" .")
    if proposed and proposed.lower() not in GENERIC_TITLES and not _looks_low_quality_title(proposed):
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


def _derive_ambiguous_reason(source_text: str, context_hint: str, likely_themes: list[str]) -> str:
    normalized = _normalize_text(source_text)
    lower = normalized.lower()
    topic = _derive_topic_phrase(source_text, context_hint)
    topic_lower = topic.lower().removesuffix(" note")

    if "follow up" in lower or "follow-up" in lower:
        return "This is worth keeping, but the right follow-up direction is not clear yet."
    if "message to " in topic_lower:
        return f"This is worth keeping, but it needs a clearer direction before it becomes a {topic_lower}."
    if topic and topic_lower not in {"follow-up", "reflection", "saved note"}:
        return f"This is worth keeping because it points toward {topic_lower}, but it still needs one clearer direction."
    if "reflection" in lower:
        return "This is worth keeping, but it needs a clearer direction before it becomes a stronger reflection."
    if likely_themes:
        return "This is worth keeping, but it needs a clearer direction before it turns into a stronger note."
    return "This is worth keeping, but the direction is not clear yet."


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


def _derive_source_sentence_key_point(source_text: str) -> str:
    ranked_sentence = best_transcript_sentence(source_text)
    if ranked_sentence:
        normalized_ranked = _normalize_clause(ranked_sentence.rstrip(".!?"))
        lowered_ranked = normalized_ranked.lower()
        if (
            normalized_ranked
            and not _looks_like_low_signal_excerpt(normalized_ranked)
            and not _looks_action_like_text(normalized_ranked)
            and not re.match(r"^(met|talked|spoke|caught up)\b", lowered_ranked)
        ):
            return normalized_ranked

    insight_patterns = (
        r"\bneeds\b",
        r"\bfeels\b",
        r"\bwants\b",
        r"\bthinks\b",
        r"\bsays\b",
        r"\bsaid\b",
        r"\bissue\b",
        r"\bproblem\b",
        r"\bbecause\b",
        r"\bworth it\b",
    )
    for sentence in _sentence_list(source_text):
        normalized = _normalize_clause(sentence.rstrip(".!?"))
        lowered = normalized.lower()
        if _looks_like_low_signal_excerpt(normalized):
            continue
        if _looks_action_like_text(normalized):
            continue
        if re.match(r"^(met|talked|spoke|caught up)\b", lowered):
            continue
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in insight_patterns):
            return normalized
    for sentence in _sentence_list(source_text):
        normalized = _normalize_clause(sentence.rstrip(".!?"))
        lowered = normalized.lower()
        if _looks_like_low_signal_excerpt(normalized):
            continue
        if _looks_action_like_text(normalized):
            continue
        if re.match(r"^(met|talked|spoke|caught up)\b", lowered):
            continue
        return normalized
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
        if not next_step:
            return "A saved product direction note with one grounded takeaway to carry forward."
        return "A saved product direction note with one concrete change to carry forward."

    if "conversation with" in normalized_title.lower():
        if not next_step:
            return "A saved conversation note with the clearest takeaway to carry forward."
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
    if not _looks_generic_key_point(proposed_key_point) and not _looks_action_like_text(proposed_key_point):
        return _normalize_clause(proposed_key_point)
    grounded = _derive_grounded_key_point(source_text)
    if grounded:
        return grounded
    sentence_point = _derive_source_sentence_key_point(source_text)
    if sentence_point:
        return sentence_point
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


def _voice_quality_interpretation(quality: dict) -> str:
    signals = set(quality.get("signals") or [])
    if quality["quality"] == "noisy":
        return "Saved from a voice note that may include background or unrelated audio."
    if "external_media" in signals and "no_first_person_intent" in signals:
        return "Saved from a voice note that sounds more like captured media than a self-authored note."
    if quality["quality"] == "mixed":
        return "Saved from a voice note with mixed audio. Kept the clearest point without guessing the next step."
    return "This looks like a professional note worth shaping."


def _voice_mixed_framing_line() -> str:
    return "A saved voice note with mixed audio. Kept the clearest point without guessing the next step."


def _voice_review_reason(quality: dict) -> str:
    signals = set(quality.get("signals") or [])
    if "outro_language" in signals:
        return "This recording sounds like captured media rather than a self-contained note. Review the source text before acting on it."
    if "production_credits" in signals:
        return "This recording may include background or production audio. Review the source text before turning it into an action."
    if "external_media" in signals and "no_first_person_intent" in signals:
        return "This recording sounds more like captured media than a self-authored note. Review the source text before acting on it."
    return "This recording may contain mixed audio. Review the source text before acting on it."


def _voice_requires_review_save(quality: dict | None) -> bool:
    if not quality:
        return False
    signals = set(quality.get("signals") or [])
    return quality.get("quality") == "noisy" or (
        quality.get("quality") == "mixed"
        and "external_media" in signals
        and "no_first_person_intent" in signals
    )


def _apply_voice_quality(decision: WorkflowDecision, quality: dict) -> WorkflowDecision:
    if _voice_requires_review_save(quality):
        return WorkflowDecision(
            route_kind="saved_note",
            interpretation_line=_voice_quality_interpretation(quality),
            primary_artifact_kind="",
            secondary_artifact_kinds=[],
            likely_themes=decision.likely_themes,
            saved_note_state="needs_direction",
        )
    if quality["quality"] == "mixed":
        return WorkflowDecision(
            route_kind=decision.route_kind,
            interpretation_line=_voice_quality_interpretation(quality),
            primary_artifact_kind=decision.primary_artifact_kind,
            secondary_artifact_kinds=decision.secondary_artifact_kinds,
            likely_themes=decision.likely_themes,
            saved_note_state=decision.saved_note_state,
        )
    return decision


def _context_store(repository) -> dict[str, dict[str, dict]]:
    profiles = getattr(repository, "user_profiles", None)
    if not isinstance(profiles, dict):
        raise AttributeError("Repository does not expose a fallback context store")
    store = profiles.setdefault("__workflow_contexts__", {})
    if not isinstance(store, dict):
        store = {}
        profiles["__workflow_contexts__"] = store
    return store


def _persist_repository_state(repository) -> None:
    persist = getattr(repository, "_persist", None)
    if callable(persist):
        persist()


def _build_related_thread_payload(record: dict, context: dict | None) -> dict:
    threading = record.get("threading") or {}
    return {
        "confirmed_title": context.get("title") if context else None,
        "suggested_title": threading.get("suggested_context_title"),
        "suggestion_active": bool(threading.get("suggestion_active")),
    }


def _thread_state_has_visible_linkage(threading: dict) -> bool:
    if not threading:
        return False
    return bool(threading.get("confirmed_context_id")) or bool(threading.get("suggestion_active"))


class WorkflowService:
    def __init__(self, repository, note_generator, now_provider, api_key_provider):
        self.repository = repository
        self.note_generator = note_generator
        self.now_provider = now_provider
        self.api_key_provider = api_key_provider

    def _call_note_generator(self, source_text: str, context_hint: str, profile: dict, *, allow_next_step: bool) -> dict:
        api_key = self.api_key_provider()
        try:
            signature = inspect.signature(self.note_generator)
        except (TypeError, ValueError):
            signature = None

        if signature:
            supports_allow_next_step = "allow_next_step" in signature.parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if supports_allow_next_step:
                return self.note_generator(
                    source_text,
                    context_hint,
                    profile,
                    api_key,
                    allow_next_step=allow_next_step,
                )

        return self.note_generator(source_text, context_hint, profile, api_key)

    def _repository_create_context(self, uid: str, *, context_id: str, title: str, summary: str, seed_capture_id: str | None, now: str) -> dict:
        if hasattr(self.repository, "create_context"):
            return self.repository.create_context(
                uid,
                context_id=context_id,
                title=title,
                summary=summary,
                seed_capture_id=seed_capture_id,
                now=now,
            )

        store = _context_store(self.repository).setdefault(uid, {})
        context = {
            "context_id": context_id,
            "title": title,
            "summary": summary,
            "status": "active",
            "seed_capture_id": seed_capture_id,
            "created_at": now,
            "updated_at": now,
            "last_activity_at": now,
        }
        store[context_id] = context
        _persist_repository_state(self.repository)
        return context

    def _repository_get_context(self, uid: str, context_id: str | None) -> dict | None:
        if not context_id:
            return None
        if hasattr(self.repository, "get_context"):
            return self.repository.get_context(uid, context_id)
        return _context_store(self.repository).get(uid, {}).get(context_id)

    def _repository_list_contexts(self, uid: str, limit: int | None = 12) -> list[dict]:
        if hasattr(self.repository, "list_contexts"):
            if limit is None:
                return self.repository.list_contexts(uid, limit=1000)
            return self.repository.list_contexts(uid, limit=limit)

        repository_contexts = getattr(self.repository, "contexts", None)
        if isinstance(repository_contexts, dict):
            items = [
                dict(context)
                for (record_uid, _context_id), context in repository_contexts.items()
                if record_uid == uid
            ]
        else:
            store = _context_store(self.repository).get(uid, {})
            items = [dict(context) for context in store.values()]
        items.sort(
            key=lambda item: (
                item.get("last_activity_at", ""),
                item.get("updated_at", ""),
                item.get("created_at", ""),
            ),
            reverse=True,
        )
        if limit is None:
            return items
        return items[:limit]

    def _repository_update_capture_threading(self, uid: str, capture_id: str, threading: dict, now: str) -> None:
        if hasattr(self.repository, "update_capture_threading"):
            self.repository.update_capture_threading(uid, capture_id, threading)
            return

        records = getattr(self.repository, "records", None)
        if not isinstance(records, dict) or (uid, capture_id) not in records:
            raise KeyError(capture_id)
        records[(uid, capture_id)]["threading"] = dict(threading)
        records[(uid, capture_id)]["updated_at"] = now
        _persist_repository_state(self.repository)

    def _repository_touch_context_activity(self, uid: str, context_id: str, now: str) -> dict | None:
        if hasattr(self.repository, "touch_context_activity"):
            return self.repository.touch_context_activity(uid, context_id, now)

        context = self._repository_get_context(uid, context_id)
        if context is None:
            return None
        context["last_activity_at"] = now
        context["updated_at"] = now
        _persist_repository_state(self.repository)
        return context

    def _hydrate_capture_record(self, uid: str, record: dict | None) -> dict | None:
        if record is None:
            return None

        hydrated = dict(record)
        result = dict(hydrated.get("result") or {})
        threading = dict(hydrated.get("threading") or {})
        context = self._repository_get_context(uid, threading.get("confirmed_context_id"))
        if _thread_state_has_visible_linkage(threading) or context:
            result["related_thread"] = _build_related_thread_payload(hydrated, context)
        else:
            result.pop("related_thread", None)
        hydrated["result"] = result
        hydrated["threading"] = threading
        return hydrated

    def create_context(self, uid: str, *, title: str, summary: str = "", seed_capture_id: str | None = None) -> dict:
        context_id = f"ctx-{secrets.token_hex(6)}"
        now = self.now_provider()
        return self._repository_create_context(
            uid,
            context_id=context_id,
            title=title.strip(),
            summary=summary.strip(),
            seed_capture_id=seed_capture_id,
            now=now,
        )

    def list_active_contexts(self, uid: str, limit: int = 12) -> list[dict]:
        return [
            context
            for context in self._repository_list_contexts(uid, limit=None)
            if context.get("status") != "archived"
        ][:limit]

    def apply_context_decision(
        self,
        uid: str,
        capture_id: str,
        *,
        action: str,
        context_id: str | None = None,
        new_context_title: str | None = None,
    ) -> dict:
        if new_context_title is not None:
            raise ValueError("new_context_title is not supported in Task 1")

        record = self.repository.get_capture(uid, capture_id)
        if record is None:
            raise KeyError(capture_id)

        now = self.now_provider()
        current_threading = dict(record.get("threading") or {})

        if action == "confirmed":
            context = self._repository_get_context(uid, context_id)
            if context is None:
                raise ValueError("context_id is required for confirmed decisions")
            threading = WorkflowThreadState(
                confirmed_context_id=context["context_id"],
                context_decision="confirmed",
                context_decision_at=now,
            ).to_dict()
            threading["suggestion_active"] = False
            self._repository_touch_context_activity(uid, context["context_id"], now)
        elif action == "kept_separate":
            context = None
            threading = WorkflowThreadState(
                context_decision="kept_separate",
                context_decision_at=now,
            ).to_dict()
        else:
            raise ValueError(f"Unsupported context action: {action}")

        self._repository_update_capture_threading(uid, capture_id, threading, now)
        updated = self.repository.get_capture(uid, capture_id)
        return self._hydrate_capture_record(uid, updated)

    def create_text_capture(self, uid: str, source_text: str, context_hint: str, *, input_type: str = "text"):
        capture_id = f"cap-{secrets.token_hex(6)}"
        now = self.now_provider()
        profile = self.repository.load_user_profile(uid)
        source_event = build_source_event(
            source_text,
            context_hint,
            capture_id,
            now,
            input_type=input_type,
        )
        source_event["profile_snapshot"] = profile
        decision = route_text_capture(source_text, context_hint, profile)
        voice_quality = None
        allow_next_step = True
        if input_type == "voice":
            voice_quality = transcript_quality_check(source_text)
            decision = _apply_voice_quality(decision, voice_quality)
            allow_next_step = voice_quality["quality"] == "clean" and has_explicit_action_signal(source_text)
        metadata_line = _build_metadata_line(source_event["input_type"], now, context_hint)

        primary_artifact = None
        saved_note_artifact = None
        if decision.primary_artifact_kind == "professional_note":
            note_profile = _profile_for_note_generation(profile, source_text, context_hint)
            generated = self._call_note_generator(
                source_text,
                context_hint,
                note_profile,
                allow_next_step=allow_next_step,
            )
            key_point = derive_key_point(source_text, context_hint, generated.get("key_point", ""))
            next_step = ""
            if allow_next_step:
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
            if input_type == "voice" and voice_quality and voice_quality["quality"] == "mixed":
                framing_line = _voice_mixed_framing_line()
            source_excerpt = (
                _extract_voice_source_excerpt(source_text)
                if input_type == "voice"
                else _extract_source_excerpt(source_text)
            )
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
            saved_title = derive_specific_title(source_text, context_hint, "", suffix="note")
            saved_excerpt = (
                _extract_voice_source_excerpt(source_text, limit=140)
                if input_type == "voice"
                else _extract_source_excerpt(source_text, limit=140)
            )
            saved_next_step = derive_next_step(source_text)
            saved_status = "Saved as a small note"
            saved_framing = "Small note, saved before it slips away."
            saved_sections = []
            if input_type == "voice" and _voice_requires_review_save(voice_quality):
                saved_status = "Saved for review"
                saved_framing = _voice_quality_interpretation(voice_quality)
                saved_sections.append(
                    WorkflowArtifactSection(
                        label="Review source",
                        text=_voice_review_reason(voice_quality),
                    )
                )
            elif decision.saved_note_state == "weak_signal" and saved_next_step:
                saved_sections.append(
                    WorkflowArtifactSection(
                        label="Next step",
                        text=saved_next_step,
                    )
                )
            elif decision.saved_note_state == "needs_direction":
                saved_status = "Saved, needs direction"
                saved_framing = "Saved. The direction is not clear yet, but the thought is worth keeping."
                saved_sections.append(
                    WorkflowArtifactSection(
                        label="Why keep this",
                        text=_derive_ambiguous_reason(source_text, context_hint, decision.likely_themes),
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
            likely_themes=[],
        ).to_dict()

        record = WorkflowCaptureRecord(
            capture_id=capture_id,
            input_type=input_type,
            context_hint=context_hint,
            source_event=source_event,
            routing=decision.to_dict(),
            result=result,
            event_manifest={
                "source_event": source_event,
                "routing": decision.to_dict(),
                "transcript_quality": voice_quality or {},
                "artifact_count": 1 if primary_artifact else 0,
            },
            created_at=now,
            updated_at=now,
            threading={},
        )
        self.repository.save_capture(uid, record)
        return record

    def get_capture(self, uid: str, capture_id: str):
        return self._hydrate_capture_record(uid, self.repository.get_capture(uid, capture_id))

    def list_capture_summaries(self, uid: str, limit: int = 50):
        records = list(self.repository.list_captures(uid, limit=limit))
        records.sort(key=lambda item: self._capture_sort_key(item), reverse=True)
        return [self._build_capture_summary(record) for record in records]

    def _capture_sort_key(self, record: dict) -> str:
        created_at = record.get("created_at")
        if isinstance(created_at, datetime):
            return created_at.isoformat()
        return str(created_at or "")

    def _build_capture_summary(self, record: dict) -> dict:
        result = record.get("result") or {}
        artifact = result.get("primary_artifact") or result.get("saved_note_artifact") or {}
        capture_id = record.get("capture_id", "")
        return {
            "capture_id": capture_id,
            "title": artifact.get("title") or "Saved note",
            "metadata_line": artifact.get("metadata_line") or "",
            "status": artifact.get("status") or "",
            "route_kind": result.get("route_kind") or "",
            "created_at": record.get("created_at"),
            "next_route": f"/workflows/result/{capture_id}",
        }
