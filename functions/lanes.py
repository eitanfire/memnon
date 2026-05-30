"""
Memnon SaaS — lane definitions.

Two built-in lanes for the fellowship pilot:

  reflect      Personal reflections grounded in a chosen spiritual/philosophical tradition.
  professional Work notes enriched with career best practices for the user's field.

Each lane provides:
  - label       Display name
  - description Short explanation shown in setup UI
  - prompt()    Returns the AI prompt string for a given user config
"""

from typing import Any

# ── Reflect ───────────────────────────────────────────────────────────────────

TRADITIONS = {
    "stoicism":   "Stoic philosophy (Marcus Aurelius, Epictetus, Seneca)",
    "buddhism":   "Buddhist teachings (mindfulness, impermanence, the Eightfold Path)",
    "christianity": "Christian scripture and contemplative tradition",
    "islam":      "Islamic wisdom (Quran, Hadith, Sufi teachings)",
    "taoism":     "Taoist philosophy (Tao Te Ching, wu wei, harmony)",
    "judaism":    "Jewish wisdom (Torah, Talmud, Mussar tradition)",
    "secular":    "Secular humanism and evidence-based psychology",
    "other":      "My own personal philosophical framework",
}


def reflect_prompt(transcript: str, tradition_key: str, max_tags: int = 5) -> str:
    tradition = TRADITIONS.get(tradition_key, tradition_key)
    return f"""You are a reflective journal assistant. The user draws on {tradition} as a grounding framework.

They recorded the following personal reflection:

---
{transcript}
---

Respond with strict JSON only — no markdown wrapper, no extra keys.

{{
  "title": "short evocative title (5–8 words)",
  "summary": "2–4 sentences synthesizing their reflection. Where it fits naturally, weave in a relevant theme, question, or principle from {tradition}. Never force it — if the connection isn't genuine, leave it out.",
  "insight": "One question or idea for the user to sit with. Grounded in their words, deepened by {tradition} if relevant.",
  "action_items": ["concrete next step if one is implied, else empty array"],
  "suggested_tags": ["up to {max_tags} lowercase tags"]
}}

Rules:
- Return only the JSON object
- No invented facts
- Max {max_tags} tags
- Be warm, not clinical
"""


# ── Professional ──────────────────────────────────────────────────────────────

def professional_prompt(transcript: str, profession: str, max_tags: int = 5) -> str:
    return f"""You are a professional development assistant for a {profession}.

They recorded the following work note:

---
{transcript}
---

Respond with strict JSON only — no markdown wrapper, no extra keys.

{{
  "title": "short professional title (5–8 words)",
  "summary": "2–4 sentences capturing the key points and context.",
  "action_items": ["specific, actionable next steps — concrete and time-bound where possible"],
  "best_practice": "One relevant best practice for a {profession} that applies to what they described. Be specific to their field, not generic.",
  "suggested_tags": ["up to {max_tags} lowercase tags"]
}}

Rules:
- Return only the JSON object
- No invented facts
- Max {max_tags} tags
- Action items should be things a busy {profession} can actually do
"""


# ── Lane registry ─────────────────────────────────────────────────────────────

LANES: dict[str, dict[str, Any]] = {
    "reflect": {
        "label": "Reflect",
        "description": "Personal reflections, grounded in a spiritual or philosophical tradition you choose.",
        "icon": "🌿",
        "setup_field": "tradition",
        "setup_label": "Grounding tradition",
        "setup_options": {k: v for k, v in TRADITIONS.items()},
        "prompt_fn": reflect_prompt,
    },
    "professional": {
        "label": "Professional",
        "description": "Work notes enriched with best practices for your specific field.",
        "icon": "💼",
        "setup_field": "profession",
        "setup_label": "Your profession or field",
        "setup_options": None,  # free-text input
        "prompt_fn": professional_prompt,
    },
}


def build_prompt(lane: str, transcript: str, user: dict, max_tags: int = 5) -> str:
    """Build the AI prompt for a given lane and user config."""
    if lane == "reflect":
        tradition = user.get("tradition") or "secular"
        return reflect_prompt(transcript, tradition, max_tags)
    elif lane == "professional":
        profession = user.get("profession") or "professional"
        return professional_prompt(transcript, profession, max_tags)
    else:
        # Fallback — plain summarization
        return f"""Summarize the following voice note as strict JSON:
{{"title": "...", "summary": "...", "action_items": [], "suggested_tags": []}}
Transcript:
{transcript}"""
