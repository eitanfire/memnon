from __future__ import annotations

from datetime import datetime
import hashlib
import inspect
import secrets
import re

try:
    from google.api_core.exceptions import FailedPrecondition as FirestoreFailedPrecondition
except Exception:  # pragma: no cover - local fallback when google client libs are unavailable
    FirestoreFailedPrecondition = ()

from .models import (
    WorkflowArtifact,
    WorkflowArtifactSection,
    WorkflowCaptureRecord,
    WorkflowDecision,
    WorkflowResultPayload,
    WorkflowThreadState,
)
from .contextual_suggestions import (
    ANALYZE_PROFESSIONALLY,
    CONTEXTUAL_SUGGESTION_TYPES,
    DRAFT_SOCIAL_POST,
    build_visible_contextual_suggestions,
    evaluate_contextual_suggestions,
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
    "saved",
    "saved note",
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
    "ap computer science",
)

PRINCIPAL_EDUCATION_CONTEXT_MARKERS = (
    "school",
    "schools",
    "district",
    "teacher",
    "teachers",
    "student",
    "students",
    "classroom",
    "lesson",
    "lessons",
    "curriculum",
    "gradebook",
    "ap computer science",
)

EDUCATION_PROFESSIONS = {
    "teacher",
    "educator",
    "education",
    "teacher leader",
    "computer science teacher",
}

EDUCATION_OUTPUT_MARKERS = (
    "teacher",
    "teachers",
    "student",
    "students",
    "classroom",
    "lesson",
    "lessons",
    "school",
    "schools",
    "curriculum",
    "district",
    "gradebook",
    "principal",
    "teaching",
    "ap computer science",
)

COLLOQUIAL_NOISE_TOKENS = {
    "yeah",
    "yep",
    "nope",
    "anyway",
    "anyways",
    "kinda",
    "sorta",
    "like",
    "uh",
    "um",
}

INVALID_PERSON_TOKENS = {
    "no",
    "yeah",
    "yes",
    "right",
    "okay",
    "ok",
    "anyway",
    "anyways",
    "good",
    "important",
}

VALID_FEEDBACK_CHOICES = {"useful", "not_useful"}
SOURCE_METADATA_FIELDS = (
    "source_filename",
    "source_file_type",
    "source_file_extension",
    "source_file_size_bytes",
    "source_audio_storage_path",
    "source_audio_content_type",
    "source_audio_filename",
    "source_audio_size_bytes",
)


def _title_case_phrase(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip(" .,:;!-"))
    if not cleaned:
        return ""
    words = [word.capitalize() if word.islower() else word for word in cleaned.split()]
    return " ".join(words)


def _truncate_with_word_boundary(text: str, limit: int) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    slice_end = max(1, limit - 1)
    trimmed = normalized[:slice_end]
    if len(normalized) > slice_end and normalized[slice_end].isalnum() and trimmed[-1].isalnum():
        last_space = trimmed.rfind(" ")
        if last_space > 20:
            trimmed = trimmed[:last_space]
    return trimmed.rstrip() + "..."


def _ensure_excerpt_starts_on_word_boundary(source_text: str, excerpt: str) -> str:
    normalized_source = _normalize_text(source_text)
    normalized_excerpt = _normalize_text(excerpt)
    if not normalized_source or not normalized_excerpt:
        return normalized_excerpt

    idx = normalized_source.find(normalized_excerpt)
    if idx <= 0:
        return normalized_excerpt

    if normalized_source[idx - 1].isalnum() and normalized_excerpt[0].isalnum():
        start = idx
        while start > 0 and normalized_source[start - 1].isalnum():
            start -= 1
        end = min(len(normalized_source), start + len(normalized_excerpt))
        normalized_excerpt = normalized_source[start:end].strip()

    return normalized_excerpt


def _extract_source_excerpt(source_text: str, limit: int = 160) -> str:
    normalized = re.sub(r"\s+", " ", source_text or "").strip()
    if not normalized:
        return ""
    sentences = _sentence_list(normalized)
    for sentence in sentences:
        excerpt = _truncate_with_word_boundary(sentence, limit)
        if not _looks_like_low_signal_excerpt(excerpt):
            return _ensure_excerpt_starts_on_word_boundary(normalized, excerpt)
    if sentences:
        return _ensure_excerpt_starts_on_word_boundary(normalized, _truncate_with_word_boundary(sentences[0], limit))
    if len(normalized) <= limit:
        return normalized
    return _truncate_with_word_boundary(normalized, limit)


def _looks_like_verbatim_source_quote(source_text: str, quote: str) -> bool:
    normalized_source = _normalize_text(source_text).lower()
    normalized_quote = _normalize_text(quote).strip(" .\"'‘’“”").lower()
    if len(normalized_quote) < 12:
        return False
    return normalized_quote in normalized_source


_QUOTE_ALIGNMENT_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "so", "to", "of", "in", "on",
    "for", "with", "at", "by", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "as", "from", "into", "about",
    "we", "you", "i", "they", "he", "she", "them", "his", "her", "their", "our",
    "your", "my", "not", "do", "does", "did", "have", "has", "had", "will",
    "would", "can", "could", "should", "just", "like", "get", "got", "one",
}


def _quote_alignment_words(value: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", (value or "").lower())
    return {word for word in words if word not in _QUOTE_ALIGNMENT_STOPWORDS and len(word) > 2}


def _quote_aligns_with_summary(quote: str, summary: str) -> bool:
    summary_words = _quote_alignment_words(summary)
    quote_words = _quote_alignment_words(quote)
    if not summary_words or not quote_words:
        return False
    return len(summary_words & quote_words) >= 1


_MIN_CANDIDATES_FOR_STRICT_ALIGNMENT = 4


def _select_quote_aligned_with_summary(source_text: str, summary: str, limit: int = 200) -> str:
    summary_words = _quote_alignment_words(summary)

    candidates: list[str] = []
    for sentence in split_transcript_sentences(source_text):
        normalized = sentence.strip()
        if not normalized or _looks_like_low_signal_excerpt(normalized) or _looks_action_like_text(normalized):
            continue
        candidates.append(normalized)

    if not candidates:
        return ""

    best_sentence = ""
    best_overlap = 0
    if summary_words:
        for candidate in candidates:
            overlap = len(summary_words & _quote_alignment_words(candidate))
            if overlap > best_overlap:
                best_overlap = overlap
                best_sentence = candidate

    if best_overlap >= 1:
        excerpt = best_sentence
    elif len(candidates) < _MIN_CANDIDATES_FOR_STRICT_ALIGNMENT:
        # Few enough candidates that there's no real risk of grabbing the wrong
        # part of the source, so fall back to the best-scored sentence instead
        # of omitting the quote entirely.
        excerpt = sorted(candidates, key=score_transcript_sentence, reverse=True)[0]
    else:
        return ""

    if len(excerpt) <= limit:
        return _ensure_excerpt_starts_on_word_boundary(source_text, excerpt)
    return _ensure_excerpt_starts_on_word_boundary(source_text, _truncate_with_word_boundary(excerpt, limit))


def _resolve_source_quote(source_text: str, proposed_quote: str, summary: str, *, limit: int = 200) -> str:
    if _looks_like_verbatim_source_quote(source_text, proposed_quote) and _quote_aligns_with_summary(
        proposed_quote, summary
    ):
        cleaned = _normalize_text(proposed_quote).strip(" .\"'‘’“”")
        normalized_source = _normalize_text(source_text)
        return _ensure_excerpt_starts_on_word_boundary(
            normalized_source, _truncate_with_word_boundary(cleaned, limit)
        )
    return _select_quote_aligned_with_summary(source_text, summary, limit=limit)


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
            return _ensure_excerpt_starts_on_word_boundary(source_text, excerpt)
        return _ensure_excerpt_starts_on_word_boundary(source_text, _truncate_with_word_boundary(excerpt, limit))
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
    if input_type == "file":
        return "Uploaded file"
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


def _clean_recording_filename_stem(filename: str) -> str:
    stem = _normalize_text(filename)
    if not stem:
        return ""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", stem)
    stem = re.sub(r"\((?:\d+)\)$", "", stem).strip()
    stem = re.sub(r"\s*[-–—]?\s*(?:direct|copy|take|v\d+)(?:[-_ ]?\d+)?$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s*[-_]\d+$", "", stem)
    # Remove leading session/code prefixes like "ZR 7-13" before readability checks.
    stem = re.sub(r"^[A-Z]{2,5}\s+\d+(?:-\d+)*\s+", "", stem)
    stem = re.sub(r"\s+", " ", stem)
    return _normalize_text(stem).strip(" .,:;!-")


def _file_context_label(source_filename: str) -> str:
    filename = _normalize_text(source_filename)
    if not filename:
        return ""

    stem = _clean_recording_filename_stem(filename)
    if not stem:
        return ""

    looks_like_recording = bool(
        re.search(r"\b(?:new|audio) recording\b", filename, flags=re.IGNORECASE)
        or re.search(r"\b\d+\b", filename)
        or re.search(r"-(?:\d+|direct|copy|take|v\d+)\b", filename, flags=re.IGNORECASE)
    )
    if not looks_like_recording:
        return ""

    # Suppress camera-roll or generic recorder defaults.
    if re.match(r"^(?:img|dsc|pxl)[-_ ]?\d+$", stem, flags=re.IGNORECASE):
        return ""
    if re.match(r"^(?:new|audio) recording(?:\s+\d+|\s+\d{4}.*)?$", stem, flags=re.IGNORECASE):
        return ""

    words = re.findall(r"[A-Za-z][A-Za-z0-9&'-]*", stem)
    if not words:
        return ""

    generic_words = {"recording", "audio", "voice", "capture", "note", "file", "untitled"}
    if len(words) == 1 and words[0].lower() in generic_words:
        return ""

    # Conservative privacy guard: suppress likely person-name-only labels.
    if re.match(r"^[A-Z][a-z]+(?: [A-Z][a-z]+){1,2}$", stem):
        return ""

    if len(stem) > 42:
        return ""
    return stem


def _build_metadata_line(input_type: str, created_at: str, context_hint: str, *, source_filename: str = "") -> str:
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


def _build_compact_file_metadata_line(source_event: dict[str, object], created_at: str) -> str:
    parts = []
    filename = _normalize_text(str(source_event.get("source_filename") or ""))
    file_label = _file_context_label(filename)
    if file_label:
        parts.append(file_label)
    else:
        source_type = _describe_source_type(str(source_event.get("input_type") or ""))
        if source_type:
            parts.append(source_type)
    formatted_date = _format_capture_date(created_at)
    if formatted_date:
        parts.append(formatted_date)
    return " · ".join(parts)


_DEV_FIXTURE_FILENAMES = {"sample-note.txt", "live-question.txt"}
_DEV_FIXTURE_TITLES = {"saved", "saved note", "product direction i think note"}


def _looks_like_dev_capture(record: dict) -> bool:
    source_event = record.get("source_event") or {}
    filename = _normalize_text(str(source_event.get("source_filename") or "")).lower()
    if filename in _DEV_FIXTURE_FILENAMES:
        return True
    if filename:
        return False
    result = record.get("result") or {}
    artifact = result.get("primary_artifact") or result.get("saved_note_artifact") or {}
    title = _normalize_text(str(artifact.get("title") or "")).lower()
    return title in _DEV_FIXTURE_TITLES


def _duplicate_content_hash(source_text: str) -> str:
    normalized = _normalize_text(source_text).lower()
    if len(normalized) < 40:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _duplicate_filename_key(source_event: dict) -> str:
    return _normalize_text(str(source_event.get("source_filename") or "")).lower()


def _normalize_clause(text: str) -> str:
    cleaned = _normalize_text(text).strip(" .,:;!-")
    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:]


def _looks_like_low_signal_excerpt(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return True
    if re.search(r"\[[^\[\]]+\]", normalized):
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
    colloquial_count = sum(1 for word in words if word in COLLOQUIAL_NOISE_TOKENS)
    if colloquial_count >= 2:
        return True
    if len(words) >= 5 and colloquial_count >= 1:
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


def _normalize_topic_candidate(value: str) -> str:
    cleaned = _normalize_text(value)
    if not cleaned:
        return ""
    cleaned = re.split(
        r"\b(?:rather than|instead of|anyway|anyways|you know|i mean)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = cleaned.strip(" .,:;!-")
    return cleaned


def _looks_valid_person_token(value: str) -> bool:
    token = _normalize_text(value)
    if not token:
        return False
    lower = token.lower()
    if lower in INVALID_PERSON_TOKENS:
        return False
    if len(lower) < 3:
        return False
    return True


def _source_supports_education_context(source_text: str, context_hint: str) -> bool:
    lower = f"{_normalize_text(source_text)} {_normalize_text(context_hint)}".lower()
    if not lower:
        return False
    for marker in EDUCATION_DOMAIN_MARKERS:
        if marker in {"class", "classes"}:
            if re.search(rf"(?<!-)\b{re.escape(marker)}\b", lower):
                return True
            continue
        if re.search(rf"\b{re.escape(marker)}\b", lower):
            return True
    if re.search(r"\bprincipal\b", lower):
        if any(re.search(rf"\b{re.escape(marker)}\b", lower) for marker in PRINCIPAL_EDUCATION_CONTEXT_MARKERS):
            return True
        if re.search(r"\b(?:school|assistant|vice)\s+principal\b", lower):
            return True
    return False


def _contains_any_marker(value: str, markers: tuple[str, ...]) -> bool:
    lower = _normalize_text(value).lower()
    if not lower:
        return False
    return any(re.search(rf"\b{re.escape(marker)}\b", lower) for marker in markers)


def _generated_text_leaks_education_context(value: str, source_text: str, context_hint: str) -> bool:
    if _source_supports_education_context(source_text, context_hint):
        return False
    return _contains_any_marker(value, EDUCATION_OUTPUT_MARKERS)


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

    person_match = re.search(r"\bwith ([A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})?)\b", normalized)
    topic_match = re.search(r"\babout (?:the )?([a-z][a-z\s-]{3,50}?)(?:[,.!?]| and |$)", lower)
    if person_match and topic_match and _looks_valid_person_token(person_match.group(1)):
        person = person_match.group(1)
        has_person_context = bool(
            re.search(
                rf"\b(?:conversation|talked|spoke|met|meeting|call)\s+with\s+{re.escape(person)}\b",
                normalized,
                flags=re.IGNORECASE,
            )
        )
        if not has_person_context:
            person = ""
        topic_candidate = _normalize_topic_candidate(topic_match.group(1))
        topic = _title_case_phrase(topic_candidate)
        if topic and person:
            return f"{topic} conversation with {person}"

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
        topic_candidate = _normalize_topic_candidate(topic_match.group(1))
        topic = _title_case_phrase(topic_candidate)
        if topic:
            return topic

    send_object_match = re.search(
        r"\b(?:send|share)\s+(?:the |a |an |my |our )?([a-z][a-z\s'-]{2,30}?)\s+to\s+([A-Z][A-Za-z'-]{1,30})\b",
        normalized,
    )
    if send_object_match and _looks_valid_person_token(send_object_match.group(2)):
        obj = _title_case_phrase(_normalize_topic_candidate(send_object_match.group(1)))
        person = send_object_match.group(2)
        if obj:
            return f"{obj} for {person}"
        return f"Message to {person}"

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


def _title_seed_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9'-]+", text or "")


def derive_specific_title(
    source_text: str,
    context_hint: str,
    proposed_title: str,
    *,
    suffix: str = "",
    summary_hint: str = "",
) -> str:
    proposed = re.sub(r"\s+", " ", proposed_title or "").strip(" .")
    if _generated_text_leaks_education_context(proposed, source_text, context_hint):
        proposed = ""
    if proposed and proposed.lower() not in GENERIC_TITLES and not _looks_low_quality_title(proposed):
        return proposed

    topic = _derive_topic_phrase(source_text, context_hint)
    if _looks_low_quality_title(topic) or topic.lower() in GENERIC_TITLES:
        seed_words = _title_seed_words(summary_hint) or _title_seed_words(
            _extract_source_excerpt(source_text, limit=90)
        )
        seed_topic = _title_case_phrase(" ".join(seed_words[:6]))
        topic = (
            _title_case_phrase(context_hint)
            or (seed_topic if seed_topic.lower() not in GENERIC_TITLES else "")
            or "Saved note"
        )
    if suffix and not topic.lower().endswith(suffix.lower()):
        return f"{topic} {suffix}".strip()
    return topic


def build_copy_text(title: str, sections: list[WorkflowArtifactSection], *, summary: str = "") -> str:
    lines = [title.strip()]
    if summary.strip():
        lines.append(summary.strip())
    for section in sections:
        if section.text.strip():
            lines.append(f"{section.label}: {section.text.strip()}")
    return "\n\n".join(lines).strip()


def _split_compound_commitment_text(text: str) -> tuple[str, str]:
    parts = re.split(r"\s+and\s+", text, maxsplit=1)
    if len(parts) == 2 and _looks_action_like_text(parts[1]):
        return parts[0].strip(" ."), parts[1].strip(" .")
    return text.strip(" ."), ""


def _derive_next_step_with_secondary(source_text: str) -> tuple[str, str]:
    normalized = _normalize_text(source_text)
    patterns = [
        r"(?:action|next step)\s*:\s*([^.!?]+)",
        r"^(send [^.?!]+)",
        r"^(ask [^.?!]+)",
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
            if not text:
                continue
            primary, secondary = _split_compound_commitment_text(text)
            if primary:
                primary = primary[0].upper() + primary[1:]
            if secondary:
                secondary = secondary[0].upper() + secondary[1:]
            return primary, secondary
    return "", ""


def derive_next_step(source_text: str) -> str:
    primary, _secondary = _derive_next_step_with_secondary(source_text)
    return primary


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
        return "This note is worth keeping, but the follow-up direction is not clear yet."
    if "message to " in topic_lower:
        return f"This note is worth keeping, but it needs clearer direction before turning into a {topic_lower}."
    if topic and topic_lower not in {"follow-up", "reflection", "saved note"}:
        return f"This note appears related to {topic_lower}, but it needs one clear direction before acting on it."
    if "reflection" in lower:
        return "This reflection is worth keeping, but it needs one clear direction before acting on it."
    if likely_themes:
        return "This note is worth keeping, but it needs clearer direction before acting on it."
    return "This note is worth keeping, but the direction is not clear yet."


def _looks_generic_summary(value: str) -> bool:
    lowered = _normalize_text(value).lower()
    return not lowered or "already points toward one useful direction" in lowered


def _normalize_summary_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in (text or "").splitlines()]
    cleaned_lines = [line for line in lines if line]
    if not cleaned_lines:
        return ""
    normalized = "\n".join(cleaned_lines)
    return normalized[0].upper() + normalized[1:]


def _summary_unavailable_floor() -> str:
    return "A summary isn't available for this one — see the excerpt below from the original capture."


def _derive_grounded_key_point(source_text: str) -> str:
    for sentence in _sentence_list(source_text):
        for pattern in (
            r"\bthe key is that\s+(.+)",
            r"\bthe key is\s+(.+)",
            r"\bwhat matters is\s+(.+)",
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


def _has_explicit_action_marker(source_text: str) -> bool:
    return bool(re.search(r"(?:action|next step)\s*:", _normalize_text(source_text), flags=re.IGNORECASE))


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


def derive_interpretation_line(
    source_text: str,
    context_hint: str,
    title: str,
    summary: str,
    next_step: str,
    proposed_interpretation_line: str,
) -> str:
    if _generated_text_leaks_education_context(proposed_interpretation_line, source_text, context_hint):
        proposed_interpretation_line = ""

    lowered = _normalize_text(proposed_interpretation_line).lower()
    if lowered and "professional note worth shaping" not in lowered and "looks like a" not in lowered:
        return _normalize_clause(proposed_interpretation_line)

    normalized_title = _normalize_text(title).lower()
    lower_source = _normalize_text(source_text).lower()

    if "product direction" in lower_source or "product direction" in normalized_title:
        if next_step:
            return "Saved as a product direction note with one clear change to carry forward."
        return "Saved as a product direction note worth revisiting."

    if "conversation with" in normalized_title:
        if next_step:
            return "Saved from a conversation with one clear next step to carry forward."
        return "Saved from a conversation worth carrying forward."

    if next_step:
        return "Saved as a note with one clear next step."

    if summary:
        return "Saved as a note with a grounded summary."

    return "Saved as a note worth keeping."


def derive_framing_line(
    source_text: str,
    context_hint: str,
    title: str,
    summary: str,
    next_step: str,
    proposed_framing_line: str,
) -> str:
    if _generated_text_leaks_education_context(proposed_framing_line, source_text, context_hint):
        proposed_framing_line = ""
    if not _looks_generic_framing_line(proposed_framing_line):
        return _normalize_clause(proposed_framing_line)

    normalized_title = _normalize_text(title)
    lower_source = _normalize_text(source_text).lower()

    if "product direction" in lower_source or "product direction" in normalized_title.lower():
        if not next_step:
            return "Saved as a product direction note worth revisiting."
        return "Saved as a product direction note with one concrete change to carry forward."

    if "conversation with" in normalized_title.lower():
        if not next_step:
            return "Saved from a conversation worth carrying forward."
        return "Saved from a conversation with one clear next step to carry forward."

    if next_step:
        return "Saved as a note with one clear next step."

    if summary:
        return "Saved as a note with a grounded summary."

    return "Saved as a note worth reopening."


def derive_summary(source_text: str, context_hint: str, proposed_summary: str) -> str:
    if _generated_text_leaks_education_context(proposed_summary, source_text, context_hint):
        proposed_summary = ""
    if not _looks_generic_summary(proposed_summary):
        return _normalize_summary_text(proposed_summary)
    grounded = _derive_grounded_key_point(source_text)
    if grounded:
        return grounded
    sentence_point = _derive_source_sentence_key_point(source_text)
    if sentence_point:
        return sentence_point
    return ""


def derive_artifact_next_step(source_text: str, context_hint: str, proposed_next_step: str) -> str:
    if _generated_text_leaks_education_context(proposed_next_step, source_text, context_hint):
        proposed_next_step = ""
    grounded = derive_next_step(source_text)
    if not _looks_generic_next_step(proposed_next_step):
        return _normalize_clause(proposed_next_step)
    if grounded:
        return grounded
    return _normalize_clause(proposed_next_step)


def _source_supports_next_step(source_text: str, context_hint: str, *, input_type: str) -> bool:
    normalized = _normalize_text(source_text)
    if not normalized:
        return False
    if _looks_like_document_text(source_text, context_hint):
        return True
    if _has_explicit_action_marker(source_text):
        return True
    if re.match(r"^(send|ask|revise|write|finalize|schedule|create)\b", normalized, flags=re.IGNORECASE):
        return True
    if re.match(r"^review\b(?!\s+of\b)", normalized, flags=re.IGNORECASE):
        return True
    if re.match(r"^(need to|have to|must)\b", normalized, flags=re.IGNORECASE):
        return True
    if re.search(r"\b(?:by|on) (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow)\b", normalized, flags=re.IGNORECASE):
        return True
    if input_type == "voice":
        return has_explicit_action_signal(source_text)
    return False


def _should_surface_next_step(source_text: str, context_hint: str, next_step: str, *, input_type: str) -> bool:
    if not _source_supports_next_step(source_text, context_hint, input_type=input_type):
        return False
    return bool(next_step.strip())


def _build_primary_sections(next_step: str) -> list[WorkflowArtifactSection]:
    sections = []
    if next_step:
        sections.append(WorkflowArtifactSection(label="Next step", text=next_step))
    return sections


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


def _threading_without_suggestion(threading: dict) -> dict:
    if not threading:
        return {}
    hidden_keys = {
        "suggested_context_id",
        "suggested_context_title",
        "suggestion_active",
        "suggestion_basis",
        "suggested_at",
    }
    return {
        key: value
        for key, value in threading.items()
        if key not in hidden_keys
    }


def _thread_title_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z0-9]+", (value or "").lower())
        if len(token) >= 3
    ]


def _thread_named_terms(value: str) -> set[str]:
    return {
        match.lower()
        for match in re.findall(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)*\b", value or "")
        if len(match) >= 4
    }


def _token_overlap(left: str, right: str) -> int:
    return len(set(_thread_title_tokens(left)) & set(_thread_title_tokens(right)))


def _near_title_match(left: str, right: str) -> bool:
    left_tokens = set(_thread_title_tokens(left))
    right_tokens = set(_thread_title_tokens(right))
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    return overlap >= min(len(left_tokens), len(right_tokens))


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _should_suppress_thread_suggestion(record: dict) -> bool:
    result = record.get("result") or {}
    route_kind = result.get("route_kind") or ""
    saved_note = result.get("saved_note_artifact") or {}
    if route_kind not in {"direct_professional_note", "saved_note"}:
        return True
    if route_kind == "saved_note" and saved_note.get("state") in {"weak_signal", "needs_direction"}:
        return True

    source_event = record.get("source_event") or {}
    input_type = source_event.get("input_type") or record.get("input_type") or ""
    quality = (record.get("event_manifest") or {}).get("transcript_quality") or {}
    if input_type == "voice" and quality.get("quality") != "clean":
        return True

    source_text = source_event.get("source_text") or ""
    if _looks_like_low_signal_excerpt(source_text):
        return True

    return False


class WorkflowService:
    def __init__(
        self,
        repository,
        note_generator,
        now_provider,
        api_key_provider,
        continuity_bridge_writer=None,
        social_post_generator=None,
        professional_analysis_generator=None,
        generator_label: str = "llm",
    ):
        self.repository = repository
        self.note_generator = note_generator
        self.now_provider = now_provider
        self.api_key_provider = api_key_provider
        self.generator_label = generator_label
        self.continuity_bridge_writer = continuity_bridge_writer
        self.social_post_generator = social_post_generator
        self.professional_analysis_generator = professional_analysis_generator

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

    def _call_structured_generator(self, generator, source_text: str, context_hint: str, profile: dict) -> dict:
        if generator is None:
            raise ValueError("suggestion generator unavailable")
        return generator(source_text, context_hint, profile, self.api_key_provider())

    def _contextual_suggestion_meta(self, record: dict) -> dict:
        event_manifest = record.get("event_manifest") or {}
        meta = event_manifest.get("contextual_suggestions") or {}
        if meta:
            return dict(meta)
        return evaluate_contextual_suggestions(record)

    def _attach_visible_contextual_suggestions(self, payload: dict, *, include_contextual_suggestions: bool) -> dict:
        result = dict(payload.get("result") or {})
        result.pop("contextual_suggestions", None)
        if include_contextual_suggestions:
            visible = build_visible_contextual_suggestions(self._contextual_suggestion_meta(payload))
            if visible:
                result["contextual_suggestions"] = visible
        payload["result"] = result
        return payload

    def _build_derived_artifact_sections(self, generated: dict) -> list[WorkflowArtifactSection]:
        sections = []
        body = _normalize_text(str(generated.get("body") or ""))
        if body:
            sections.append(
                WorkflowArtifactSection(
                    label=str(generated.get("body_label") or "Draft").strip() or "Draft",
                    text=body,
                )
            )
        for item in generated.get("sections") or []:
            if not isinstance(item, dict):
                continue
            label = _normalize_text(str(item.get("label") or ""))
            text = _normalize_text(str(item.get("text") or ""))
            if label and text:
                sections.append(WorkflowArtifactSection(label=label, text=text))
        return sections

    def _build_derived_primary_artifact(
        self,
        *,
        capture_id: str,
        suggestion_type: str,
        metadata_line: str,
        source_excerpt: str,
        generated: dict,
    ) -> dict:
        sections = self._build_derived_artifact_sections(generated)
        title = _normalize_text(str(generated.get("title") or "Saved result")) or "Saved result"
        framing_line = _normalize_text(str(generated.get("framing_line") or "Saved as a result worth keeping.")) or "Saved as a result worth keeping."
        body = _normalize_text(str(generated.get("body") or ""))
        copy_text = _normalize_text(str(generated.get("copy_text") or body))
        kind = "social_post" if suggestion_type == DRAFT_SOCIAL_POST else "professional_analysis"
        if not copy_text:
            copy_text = build_copy_text(title, sections)
        return WorkflowArtifact(
            artifact_id=f"{capture_id}-primary",
            kind=kind,
            title=title,
            framing_line=framing_line,
            body=body,
            status="Saved and shaped",
            primary_action="Copy",
            metadata_line=metadata_line,
            source_excerpt=source_excerpt,
            sections=sections,
            copy_text=copy_text,
            secondary_actions=[],
        ).to_dict()

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
        if hasattr(self.repository, "list_active_contexts"):
            effective_limit = 1000 if limit is None else limit
            try:
                return self.repository.list_active_contexts(uid, limit=effective_limit)
            except FirestoreFailedPrecondition:
                return []
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

    def _repository_update_capture_result(
        self,
        uid: str,
        capture_id: str,
        result: dict,
        routing: dict,
        event_manifest: dict,
        now: str,
        generator: str = "",
    ) -> None:
        if hasattr(self.repository, "update_capture_result"):
            self.repository.update_capture_result(uid, capture_id, result, routing, event_manifest, now, generator)
            return

        records = getattr(self.repository, "records", None)
        if not isinstance(records, dict) or (uid, capture_id) not in records:
            raise KeyError(capture_id)
        records[(uid, capture_id)]["result"] = result
        records[(uid, capture_id)]["routing"] = routing
        records[(uid, capture_id)]["event_manifest"] = event_manifest
        records[(uid, capture_id)]["generator"] = generator
        records[(uid, capture_id)]["updated_at"] = now
        _persist_repository_state(self.repository)

    def _repository_update_capture_feedback(
        self, uid: str, capture_id: str, feedback_choice: str, feedback_note: str, now: str
    ) -> None:
        if hasattr(self.repository, "update_capture_feedback"):
            update_feedback = self.repository.update_capture_feedback
            try:
                signature = inspect.signature(update_feedback)
            except (TypeError, ValueError):
                signature = None

            if signature and "feedback_updated_at" in signature.parameters:
                update_feedback(uid, capture_id, feedback_choice, feedback_note, now)
            else:
                update_feedback(uid, capture_id, feedback_choice, feedback_note)
            return

        records = getattr(self.repository, "records", None)
        if not isinstance(records, dict) or (uid, capture_id) not in records:
            raise KeyError(capture_id)
        records[(uid, capture_id)]["feedback_choice"] = feedback_choice
        records[(uid, capture_id)]["feedback_note"] = feedback_note
        records[(uid, capture_id)]["feedback_updated_at"] = now
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

    def _hydrate_capture_record(
        self,
        uid: str,
        record: dict | None,
        *,
        include_suggestion: bool = True,
        include_contextual_suggestions: bool = False,
    ) -> dict | None:
        if record is None:
            return None

        hydrated = dict(record)
        result = dict(hydrated.get("result") or {})
        result.pop("contextual_suggestions", None)
        raw_threading = dict(hydrated.get("threading") or {})
        threading = dict(raw_threading if include_suggestion else _threading_without_suggestion(raw_threading))
        context = self._repository_get_context(uid, threading.get("confirmed_context_id"))
        if _thread_state_has_visible_linkage(threading) or context:
            payload_record = dict(hydrated)
            payload_record["threading"] = threading
            result["related_thread"] = _build_related_thread_payload(payload_record, context)
        else:
            result.pop("related_thread", None)
        hydrated["result"] = result
        hydrated["threading"] = threading
        return self._attach_visible_contextual_suggestions(
            hydrated,
            include_contextual_suggestions=include_contextual_suggestions,
        )

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
        record = self.repository.get_capture(uid, capture_id)
        if record is None:
            raise KeyError(capture_id)

        now = self.now_provider()

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
        elif action == "selected_different_context":
            context = self._repository_get_context(uid, context_id)
            if context is None:
                raise ValueError("context_id is required for selected_different_context decisions")
            threading = WorkflowThreadState(
                confirmed_context_id=context["context_id"],
                context_decision="selected_different_context",
                context_decision_at=now,
            ).to_dict()
            threading["suggestion_active"] = False
            self._repository_touch_context_activity(uid, context["context_id"], now)
        elif action == "created_new_context":
            title = (new_context_title or "").strip()
            if len(title) < 2:
                raise ValueError("new_context_title is required for created_new_context decisions")
            context = self.create_context(
                uid,
                title=title,
                summary="",
                seed_capture_id=capture_id,
            )
            threading = WorkflowThreadState(
                confirmed_context_id=context["context_id"],
                context_decision="created_new_context",
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
        return self._hydrate_capture_record(
            uid,
            updated,
            include_contextual_suggestions=True,
        )

    def apply_feedback_choice(
        self,
        uid: str,
        capture_id: str,
        *,
        feedback_choice: str,
        feedback_note: str = "",
    ) -> dict:
        if feedback_choice not in VALID_FEEDBACK_CHOICES:
            raise ValueError("invalid feedback choice")

        record = self.repository.get_capture(uid, capture_id)
        if record is None:
            raise KeyError(capture_id)

        now = self.now_provider()
        self._repository_update_capture_feedback(uid, capture_id, feedback_choice, feedback_note, now)
        updated = self.repository.get_capture(uid, capture_id)
        return self._hydrate_capture_record(
            uid,
            updated,
            include_contextual_suggestions=True,
        )

    def regenerate_capture(self, uid: str, capture_id: str) -> dict:
        record = self.repository.get_capture(uid, capture_id)
        if record is None:
            raise KeyError(capture_id)

        source_event = dict(record.get("source_event") or {})
        source_text = str(source_event.get("source_text") or "")
        if not source_text.strip():
            raise ValueError("no source text available to regenerate")
        context_hint = str(record.get("context_hint") or "")
        input_type = str(record.get("input_type") or source_event.get("input_type") or "text")

        now = self.now_provider()
        profile = self.repository.load_user_profile(uid)
        source_event["profile_snapshot"] = profile

        decision, voice_quality, primary_artifact, saved_note_artifact, result = self._build_capture_result(
            source_text, context_hint, input_type, profile, source_event, capture_id, now
        )

        event_manifest = dict(record.get("event_manifest") or {})
        event_manifest["source_event"] = source_event
        event_manifest["routing"] = decision.to_dict()
        event_manifest["transcript_quality"] = voice_quality or {}
        event_manifest["artifact_count"] = 1 if primary_artifact else 0
        merged_record = dict(record)
        merged_record["result"] = result
        merged_record["routing"] = decision.to_dict()
        merged_record["event_manifest"] = event_manifest
        merged_record["generator"] = self.generator_label
        event_manifest["contextual_suggestions"] = evaluate_contextual_suggestions(merged_record)

        self._repository_update_capture_result(
            uid, capture_id, result, decision.to_dict(), event_manifest, now, self.generator_label
        )
        updated = self.repository.get_capture(uid, capture_id)
        return self._hydrate_capture_record(
            uid,
            updated,
            include_contextual_suggestions=True,
        )

    def apply_contextual_suggestion(
        self,
        uid: str,
        capture_id: str,
        *,
        suggestion_type: str,
    ) -> dict:
        if suggestion_type not in CONTEXTUAL_SUGGESTION_TYPES:
            raise ValueError("invalid suggestion type")

        parent = self.repository.get_capture(uid, capture_id)
        if parent is None:
            raise KeyError(capture_id)

        stored_parent_meta = dict(((parent.get("event_manifest") or {}).get("contextual_suggestions") or {}))
        if not stored_parent_meta:
            raise ValueError("suggestion unavailable")
        parent_meta = stored_parent_meta
        if parent_meta.get("origin") == "derived_result":
            raise ValueError("suggestion unavailable")
        if suggestion_type not in (parent_meta.get("shown_types") or []):
            raise ValueError("suggestion unavailable")

        profile = self.repository.load_user_profile(uid)
        source_event = dict(parent.get("source_event") or {})
        source_text = str(source_event.get("source_text") or "")
        context_hint = str(parent.get("context_hint") or source_event.get("context_hint") or "")
        input_type = str(source_event.get("input_type") or parent.get("input_type") or "text")
        now = self.now_provider()
        new_capture_id = f"cap-{secrets.token_hex(6)}"

        if suggestion_type == DRAFT_SOCIAL_POST:
            generated = self._call_structured_generator(
                self.social_post_generator,
                source_text,
                context_hint,
                profile,
            )
            interpretation_line = "Saved as a social-ready draft."
        else:
            generated = self._call_structured_generator(
                self.professional_analysis_generator,
                source_text,
                context_hint,
                profile,
            )
            interpretation_line = "Saved as a professional analysis."

        derived_source_event = dict(source_event)
        derived_source_event["capture_id"] = new_capture_id
        derived_source_event["created_at"] = now
        derived_source_event["context_hint"] = context_hint
        derived_source_event["profile_snapshot"] = profile
        derived_source_event["source_preview"] = _normalize_text(
            str(derived_source_event.get("source_preview") or source_text)
        )[:240]

        metadata_line = _build_metadata_line(
            input_type,
            now,
            context_hint,
            source_filename=str(source_event.get("source_filename") or ""),
        )
        source_excerpt = (
            _extract_voice_source_excerpt(source_text)
            if input_type == "voice"
            else _extract_source_excerpt(source_text)
        )
        primary_artifact = self._build_derived_primary_artifact(
            capture_id=new_capture_id,
            suggestion_type=suggestion_type,
            metadata_line=metadata_line,
            source_excerpt=source_excerpt,
            generated=generated,
        )

        routing = WorkflowDecision(
            route_kind="direct_professional_note",
            interpretation_line=interpretation_line,
            primary_artifact_kind=primary_artifact.get("kind") or "professional_note",
            secondary_artifact_kinds=[],
            likely_themes=[],
        ).to_dict()
        result = WorkflowResultPayload(
            interpretation_line=interpretation_line,
            route_kind="direct_professional_note",
            primary_artifact=primary_artifact,
            saved_note_artifact=None,
            secondary_artifacts=[],
            review_queue=[],
            source_preview=derived_source_event["source_preview"],
            likely_themes=[],
        ).to_dict()
        result.pop("related_thread", None)
        result.pop("contextual_suggestions", None)

        record = WorkflowCaptureRecord(
            capture_id=new_capture_id,
            input_type=input_type,
            context_hint=context_hint,
            source_event=derived_source_event,
            routing=routing,
            result=result,
            event_manifest={
                "source_event": derived_source_event,
                "routing": routing,
                "transcript_quality": dict((parent.get("event_manifest") or {}).get("transcript_quality") or {}),
                "artifact_count": 1,
                "contextual_suggestions": {
                    "origin": "derived_result",
                    "parent_capture_id": capture_id,
                    "invoked_type": suggestion_type,
                    "considered_types": list(CONTEXTUAL_SUGGESTION_TYPES),
                    "shown_types": [],
                    "suppression_reasons": {
                        DRAFT_SOCIAL_POST: "derived_result",
                        ANALYZE_PROFESSIONALLY: "derived_result",
                    },
                },
            },
            created_at=now,
            updated_at=now,
            threading={},
            generator=self.generator_label,
        )
        self.repository.save_capture(uid, record)
        return self._hydrate_capture_record(
            uid,
            self.repository.get_capture(uid, new_capture_id),
            include_contextual_suggestions=False,
        )

    def _context_recency_boost(self, thread: dict) -> int:
        last_activity_at = _parse_iso_timestamp(thread.get("last_activity_at") or thread.get("updated_at"))
        current_time = _parse_iso_timestamp(self.now_provider())
        if not last_activity_at or not current_time:
            return 0
        age_seconds = (current_time - last_activity_at).total_seconds()
        if age_seconds < 0:
            return 0
        return 2 if age_seconds <= 60 * 60 * 24 * 30 else 0

    def _prior_confirmed_match_boosts(self, uid: str, record: dict, thread: dict) -> tuple[int, int]:
        records = self.repository.list_captures(uid, limit=50)
        current_source = (record.get("source_event") or {}).get("source_text") or ""
        target_context_id = thread.get("context_id")
        current_named_terms = _thread_named_terms(current_source)
        for prior in records:
            prior_threading = prior.get("threading") or {}
            if prior_threading.get("confirmed_context_id") != target_context_id:
                continue
            prior_source = (prior.get("source_event") or {}).get("source_text") or ""
            pattern_boost = 2 if _token_overlap(current_source, prior_source) >= 2 else 0
            named_entity_boost = 4 if current_named_terms & _thread_named_terms(prior_source) else 0
            if pattern_boost or named_entity_boost:
                return pattern_boost, named_entity_boost
        return 0, 0

    def _score_context_match(self, uid: str, record: dict, thread: dict) -> int:
        source_event = record.get("source_event") or {}
        source_text = source_event.get("source_text") or ""
        context_hint = record.get("context_hint") or source_event.get("context_hint") or ""
        artifact = (record.get("result") or {}).get("primary_artifact") or {}
        artifact_title = artifact.get("title") or ""
        thread_title = thread.get("title") or ""

        score = 0

        normalized_hint = _normalize_text(context_hint).lower()
        normalized_title = _normalize_text(thread_title).lower()
        if normalized_hint and normalized_title and (
            normalized_hint == normalized_title
            or _near_title_match(context_hint, thread_title)
        ):
            score += 5

        title_overlap = max(
            _token_overlap(thread_title, source_text),
            _token_overlap(thread_title, artifact_title),
            _token_overlap(thread_title, context_hint),
        )
        if title_overlap >= 2:
            score += 4

        if _thread_named_terms(source_text) & _thread_named_terms(thread_title):
            score += 4

        score += self._context_recency_boost(thread)
        pattern_boost, named_entity_boost = self._prior_confirmed_match_boosts(uid, record, thread)
        score += pattern_boost
        score += named_entity_boost
        return score

    def suggest_context_for_capture(self, uid: str, record: dict) -> dict | None:
        if _should_suppress_thread_suggestion(record):
            return None

        threads = self.list_active_contexts(uid, limit=12)
        if not threads:
            return None

        scored = [
            (self._score_context_match(uid, record, thread), thread)
            for thread in threads
        ]
        scored.sort(key=lambda item: item[0], reverse=True)

        best_score, best_thread = scored[0]
        runner_up_score = scored[1][0] if len(scored) > 1 else -1

        if best_score < 5:
            return None
        if best_score - runner_up_score < 2:
            return None

        return {
            "suggested_context_id": best_thread["context_id"],
            "suggested_context_title": best_thread["title"],
            "suggestion_active": True,
            "suggestion_basis": "ranked_match",
            "suggested_at": self.now_provider(),
        }

    def _build_capture_result(
        self,
        source_text: str,
        context_hint: str,
        input_type: str,
        profile: dict,
        source_event: dict,
        capture_id: str,
        now: str,
    ):
        decision = route_text_capture(source_text, context_hint, profile)
        voice_quality = None
        allow_next_step = True
        if input_type == "voice":
            voice_quality = transcript_quality_check(source_text)
            decision = _apply_voice_quality(decision, voice_quality)
            allow_next_step = voice_quality["quality"] == "clean" and has_explicit_action_signal(source_text)
        metadata_line = _build_metadata_line(
            source_event["input_type"],
            now,
            context_hint,
            source_filename=str(source_event.get("source_filename") or ""),
        )

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
            if _looks_generic_summary(generated.get("summary", "")):
                retry = self._call_note_generator(
                    source_text,
                    context_hint,
                    note_profile,
                    allow_next_step=allow_next_step,
                )
                if not _looks_generic_summary(retry.get("summary", "")):
                    generated = retry
            summary = derive_summary(source_text, context_hint, generated.get("summary", ""))
            if not summary.strip():
                summary = _summary_unavailable_floor()
            next_step = ""
            if allow_next_step:
                next_step = derive_artifact_next_step(
                    source_text,
                    context_hint,
                    generated.get("next_step") or derive_next_step(source_text),
                ).strip()
            if not _should_surface_next_step(source_text, context_hint, next_step, input_type=input_type):
                next_step = ""
            sections = _build_primary_sections(next_step)
            title = derive_specific_title(source_text, context_hint, generated.get("title", ""), summary_hint=summary)
            if title.lower().endswith(" note"):
                title = title[:-5].rstrip()
            framing_line = derive_framing_line(
                source_text,
                context_hint,
                title,
                summary,
                next_step,
                generated.get("framing_line", ""),
            )
            if input_type == "voice" and voice_quality and voice_quality["quality"] == "mixed":
                framing_line = _voice_mixed_framing_line()
            source_excerpt = _resolve_source_quote(
                source_text, generated.get("source_quote", ""), summary
            )
            primary_artifact = WorkflowArtifact(
                artifact_id=f"{capture_id}-primary",
                kind="professional_note",
                title=title,
                framing_line=framing_line,
                body=build_copy_text(title, sections, summary=summary),
                status="Saved and shaped",
                primary_action="Copy",
                metadata_line=metadata_line,
                summary=summary,
                source_excerpt=source_excerpt,
                sections=sections,
                copy_text=build_copy_text(title, sections, summary=summary),
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
                saved_framing = "Saved for now. The direction is not clear yet, but the note is worth keeping."
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
            interpretation_line=derive_interpretation_line(
                source_text,
                context_hint,
                title if primary_artifact else saved_title,
                summary if primary_artifact else "",
                next_step if primary_artifact else "",
                decision.interpretation_line,
            ),
            route_kind=decision.route_kind,
            primary_artifact=primary_artifact,
            saved_note_artifact=saved_note_artifact,
            secondary_artifacts=[],
            review_queue=[],
            source_preview=source_event["source_preview"],
            likely_themes=[],
        ).to_dict()
        result.pop("related_thread", None)
        result.pop("contextual_suggestions", None)
        return decision, voice_quality, primary_artifact, saved_note_artifact, result

    def create_text_capture(
        self,
        uid: str,
        source_text: str,
        context_hint: str,
        *,
        capture_id: str | None = None,
        input_type: str = "text",
        source_metadata: dict[str, object] | None = None,
        include_teaching_context: bool | None = None,
    ):
        capture_id = capture_id or f"cap-{secrets.token_hex(6)}"
        now = self.now_provider()
        profile = self.repository.load_user_profile(uid)
        source_event = build_source_event(
            source_text,
            context_hint,
            capture_id,
            now,
            input_type=input_type,
        )
        if source_metadata:
            for key in SOURCE_METADATA_FIELDS:
                value = source_metadata.get(key)
                if value not in (None, ""):
                    source_event[key] = value
        source_event["profile_snapshot"] = profile

        decision, voice_quality, primary_artifact, saved_note_artifact, result = self._build_capture_result(
            source_text, context_hint, input_type, profile, source_event, capture_id, now
        )

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
            generator=self.generator_label,
        )
        record.event_manifest["contextual_suggestions"] = evaluate_contextual_suggestions(record.to_dict())
        suggestion = self.suggest_context_for_capture(uid, record.to_dict())
        if suggestion:
            record.threading = suggestion
            record.result["related_thread"] = {
                "confirmed_title": None,
                "suggested_title": suggestion["suggested_context_title"],
                "suggestion_active": True,
            }
        self.repository.save_capture(uid, record)
        if self.continuity_bridge_writer is not None:
            effective_include_teaching_context = include_teaching_context
            if effective_include_teaching_context is None:
                persisted_context_value = profile.get("include_teaching_context")
                effective_include_teaching_context = (
                    persisted_context_value
                    if isinstance(persisted_context_value, bool)
                    else True
                )
            try:
                self.continuity_bridge_writer(
                    uid=uid,
                    profile=profile,
                    capture_record=record.to_dict(),
                    include_teaching_context=effective_include_teaching_context,
                )
            except Exception as exc:
                print(f"[{uid}] Warning: continuity bridge write failed: {exc}")
        visible_contextual_suggestions = build_visible_contextual_suggestions(
            record.event_manifest.get("contextual_suggestions"),
        )
        if visible_contextual_suggestions:
            record.result["contextual_suggestions"] = visible_contextual_suggestions
        return record

    def get_capture(self, uid: str, capture_id: str):
        return self._hydrate_capture_record(
            uid,
            self.repository.get_capture(uid, capture_id),
            include_suggestion=False,
            include_contextual_suggestions=False,
        )

    def list_capture_summaries(self, uid: str, limit: int = 50):
        records = list(self.repository.list_captures(uid, limit=limit))
        records.sort(key=lambda item: self._capture_sort_key(item), reverse=True)

        hash_counts: dict[str, int] = {}
        filename_counts: dict[str, int] = {}
        for record in records:
            source_event = record.get("source_event") or {}
            content_hash = _duplicate_content_hash(str(source_event.get("source_text") or ""))
            if content_hash:
                hash_counts[content_hash] = hash_counts.get(content_hash, 0) + 1
            filename_key = _duplicate_filename_key(source_event)
            if filename_key:
                filename_counts[filename_key] = filename_counts.get(filename_key, 0) + 1

        return [
            self._build_capture_summary(record, hash_counts=hash_counts, filename_counts=filename_counts)
            for record in records
        ]

    def _capture_sort_key(self, record: dict) -> str:
        created_at = record.get("created_at")
        if isinstance(created_at, datetime):
            return created_at.isoformat()
        return str(created_at or "")

    def _build_capture_summary(
        self,
        record: dict,
        *,
        hash_counts: dict[str, int] | None = None,
        filename_counts: dict[str, int] | None = None,
    ) -> dict:
        result = record.get("result") or {}
        artifact = result.get("primary_artifact") or result.get("saved_note_artifact") or {}
        capture_id = record.get("capture_id", "")
        source_event = record.get("source_event") or {}
        metadata_line = artifact.get("metadata_line") or ""
        if source_event.get("input_type") == "file":
            metadata_line = _build_compact_file_metadata_line(source_event, str(record.get("created_at") or ""))

        content_hash = _duplicate_content_hash(str(source_event.get("source_text") or ""))
        filename_key = _duplicate_filename_key(source_event)
        looks_like_possible_duplicate = (
            bool(content_hash) and (hash_counts or {}).get(content_hash, 0) > 1
        ) or (bool(filename_key) and (filename_counts or {}).get(filename_key, 0) > 1)

        return {
            "capture_id": capture_id,
            "title": artifact.get("title") or "Saved note",
            "metadata_line": metadata_line,
            "status": artifact.get("status") or "",
            "route_kind": result.get("route_kind") or "",
            "created_at": record.get("created_at"),
            "next_route": f"/workflows/result/{capture_id}",
            "feedback_choice": record.get("feedback_choice") or "",
            "feedback_note": record.get("feedback_note") or "",
            "looks_like_dev_data": _looks_like_dev_capture(record),
            "looks_like_possible_duplicate": looks_like_possible_duplicate,
        }
