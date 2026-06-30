from __future__ import annotations

import re


ACTION_SIGNAL_PATTERNS = (
    r"\baction\s*:",
    r"\baction item\s*:",
    r"\bnext step\s*:",
    r"\bi need to\b",
    r"\bi should\b",
    r"\bi want to\b",
    r"\bfollow up\b",
    r"\breach out\b",
    r"\bremind me\b",
    r"\bdon'?t forget\b",
    r"\bmake sure\b",
    r"\bbefore the demo\b",
    r"\bby end of week\b",
    r"\bthis week\b",
)

PRODUCTION_CREDIT_PATTERNS = (
    r"\bour production team\b",
    r"\bdaily production team\b",
    r"\bproduction team includes\b",
    r"\bsupervising senior\b",
    r"\bproduced by\b",
    r"\bedited by\b",
)

OUTRO_PATTERNS = (
    r"\bthanks for listening\b",
    r"\bsubscribe\b",
    r"\bnext episode\b",
    r"\bwherever you get your podcasts\b",
)

TOPIC_SHIFT_PATTERNS = (
    r"\bto what extent\b",
    r"\bwell there'?s an interesting confluence\b",
    r"\bmeanwhile\b",
    r"\bon the other hand\b",
)

TRANSITION_OPENING_PATTERNS = (
    r"^(?:okay|ok)\b",
    r"^so\b",
    r"^this next category\b",
    r"^the more\b",
    r"^you know\b",
    r"^i guess\b",
    r"^well\b",
)

EXTERNAL_MEDIA_PATTERNS = (
    r"\breviewer says\b",
    r"\bfood reviewer\b",
    r"\bpodcast\b",
    r"\bepisode\b",
    r"\bhost\b",
    r"\barticle\b",
    r"\bnews\b",
    r"\bshout out\b",
)

FIRST_PERSON_INTENT_PATTERNS = (
    r"\bmet with\b",
    r"\bi need to\b",
    r"\bi should\b",
    r"\bi want to\b",
    r"\bmy note\b",
    r"\bnote to myself\b",
    r"\bremind me\b",
    r"\bwe need to\b",
)

CONCRETE_CLAIM_PATTERNS = (
    r"\bneeds\b",
    r"\bshould\b",
    r"\bproblem\b",
    r"\bdecision\b",
    r"\baction\b",
    r"\bfollow up\b",
    r"\bbefore\b",
    r"\bbecause\b",
    r"\bfeels\b",
    r"\bworth it\b",
    r"\bworth\b",
    r"\bexpensive\b",
    r"\bdecreased?\b",
    r"\bdown\b",
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def split_transcript_sentences(transcript: str) -> list[str]:
    normalized = _normalize_text(transcript)
    if not normalized:
        return []
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", normalized) if sentence.strip()]


def _has_transition_opening(sentence: str) -> bool:
    lowered = _normalize_text(sentence).lower()
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in TRANSITION_OPENING_PATTERNS)


def _has_external_media_cue(transcript: str) -> bool:
    lowered = _normalize_text(transcript).lower()
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in EXTERNAL_MEDIA_PATTERNS)


def _has_first_person_intent(transcript: str) -> bool:
    lowered = _normalize_text(transcript).lower()
    if has_explicit_action_signal(lowered):
        return True
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in FIRST_PERSON_INTENT_PATTERNS)


def score_transcript_sentence(sentence: str, index: int = 0) -> int:
    normalized = _normalize_text(sentence)
    lowered = normalized.lower()
    words = re.findall(r"[A-Za-z0-9'-]+", lowered)
    if not words:
        return -100

    score = 0
    if len(words) < 4:
        score -= 6
    elif len(words) >= 7:
        score += 1

    if _has_transition_opening(normalized):
        score -= 10
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in PRODUCTION_CREDIT_PATTERNS):
        score -= 8
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in OUTRO_PATTERNS):
        score -= 8
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in TOPIC_SHIFT_PATTERNS):
        score -= 5

    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in CONCRETE_CLAIM_PATTERNS):
        score += 4
    if has_explicit_action_signal(normalized):
        score += 5
    if re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", normalized):
        score += 2
    if re.search(r"^(met|talked|spoke|caught up)\b", lowered):
        score += 5

    if index == 0:
        score += 1
    return score


def best_transcript_sentence(transcript: str) -> str:
    sentences = split_transcript_sentences(transcript)
    if not sentences:
        return ""

    best_sentence = ""
    best_score = -100
    for index, sentence in enumerate(sentences):
        score = score_transcript_sentence(sentence, index=index)
        if score > best_score:
            best_score = score
            best_sentence = sentence.strip()

    if best_sentence:
        return best_sentence

    return sentences[0].strip()


def has_explicit_action_signal(source_text: str) -> bool:
    normalized = _normalize_text(source_text).lower()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in ACTION_SIGNAL_PATTERNS)


def _full_name_count(source_text: str) -> int:
    return len(re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", source_text or ""))


def transcript_quality_check(transcript: str) -> dict:
    normalized = _normalize_text(transcript)
    lowered = normalized.lower()
    sentences = split_transcript_sentences(normalized)
    first_sentence = sentences[0] if sentences else normalized
    signals: list[str] = []

    has_production_credits = any(
        re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in PRODUCTION_CREDIT_PATTERNS
    )
    has_outro_language = any(
        re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in OUTRO_PATTERNS
    )
    has_topic_shift = any(
        re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in TOPIC_SHIFT_PATTERNS
    )
    has_transition_opening = _has_transition_opening(first_sentence)
    has_external_media = _has_external_media_cue(normalized)
    has_first_person_intent = _has_first_person_intent(normalized)
    name_count = _full_name_count(normalized)
    very_short = len(normalized.split()) < 15 and not has_explicit_action_signal(normalized)

    if has_production_credits:
        signals.append("production_credits")
    if has_outro_language:
        signals.append("outro_language")
    if has_topic_shift:
        signals.append("topic_shift")
    if has_transition_opening:
        signals.append("transition_opening")
    if has_external_media:
        signals.append("external_media")
    if not has_first_person_intent:
        signals.append("no_first_person_intent")
    if name_count >= 3:
        signals.append("name_run")
    if very_short:
        signals.append("very_short")

    quality = "clean"
    confidence = 0.1
    if has_outro_language or (
        has_production_credits and ("supervising senior" in lowered or name_count >= 3)
    ):
        quality = "noisy"
        confidence = 0.92
    elif has_production_credits or has_topic_shift or very_short or (
        has_external_media and not has_first_person_intent and has_transition_opening
    ):
        quality = "mixed"
        confidence = 0.78 if has_external_media and not has_first_person_intent and has_transition_opening else 0.72

    return {
        "quality": quality,
        "signals": signals,
        "confidence": confidence,
    }
