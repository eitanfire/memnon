"""
Active Schema — lane definitions.

Two built-in lanes:

  reflect      Personal reflections grounded in chosen voices/wisdom traditions.
  professional Teaching notes enriched with pedagogical best practices.

Each lane provides:
  - label       Display name
  - prompt()    Returns (prompt_string, sources_used_list)
"""

import json
import os
import re
from pathlib import Path
from typing import Any

# ── Load sources.json once at import time ─────────────────────────────────────

_SOURCES_PATH = Path(__file__).parent / "sources.json"

def _load_sources() -> dict:
    try:
        with open(_SOURCES_PATH) as f:
            return json.load(f)
    except Exception as e:
        print(f"[lanes] Warning: could not load sources.json: {e}")
        return {"traditions": {}, "guides": {}}

SOURCES = _load_sources()


# ── Theme normalization ────────────────────────────────────────────────────────
# Maps common transcript words/phrases → canonical tag names.
# Covers the most common semantic gaps between natural speech and curated tags.

THEME_SYNONYMS: dict[str, list[str]] = {
    # Emotional states
    "burnout":       ["exhausted", "drained", "depleted", "burned", "running", "empty"],
    "overwhelm":     ["overwhelmed", "overwhelm", "swamped", "buried", "drowning", "everything"],
    "frustration":   ["frustrated", "frustrating", "frustration", "annoying", "annoyed"],
    "anger":         ["angry", "anger", "furious", "mad", "upset", "irritated", "rage"],
    "anxiety":       ["anxious", "anxiety", "worried", "worry", "nervous", "stress", "stressed"],
    "grief":         ["grief", "loss", "lost", "miss", "missing", "mourning", "death"],
    "joy":           ["happy", "joy", "joyful", "excited", "wonderful", "great", "love"],
    "gratitude":     ["grateful", "gratitude", "thankful", "appreciate", "appreciation"],

    # Control and agency
    "control":       ["control", "manage", "managing", "managed", "handle", "fix", "fixing",
                      "responsible", "responsibility", "in charge", "cant stop"],
    "acceptance":    ["accept", "acceptance", "let go", "letting go", "surrender",
                      "cannot change", "nothing i can do"],
    "freedom":       ["free", "freedom", "choice", "choose", "decided", "my decision"],

    # Relationships
    "conflict":      ["conflict", "argument", "fight", "disagree", "disagreement",
                      "tension", "clash", "confrontation"],
    "connection":    ["connected", "connection", "bond", "together", "belonging", "community"],
    "difficult_people": ["difficult", "hard to deal", "problem student", "problem parent",
                         "challenging", "defiant", "disruptive"],
    "listening":     ["listen", "listening", "heard", "hear", "not listening"],
    "relationship":  ["relationship", "student", "colleague", "parent", "admin", "team"],

    # Purpose and meaning
    "vocation":      ["why i teach", "calling", "purpose", "meant to", "reason i",
                      "love teaching", "hate teaching", "quit", "leaving"],
    "meaning":       ["meaning", "meaningful", "matters", "matter", "point", "worth it"],
    "purpose":       ["purpose", "goal", "aim", "direction", "focus", "what matters"],

    # Time and pace
    "time":          ["time", "no time", "never enough time", "rushing", "rushed", "deadline",
                      "behind", "schedule", "calendar", "hours"],
    "overwhelm":     ["too much", "too many", "overwhelmed", "cannot do it all"],
    "pace":          ["slow down", "slowing", "pace", "rushing", "hurried", "fast"],
    "urgency":       ["urgent", "urgency", "now", "immediately", "crisis"],

    # Inner life
    "solitude":      ["alone", "solitude", "quiet", "silence", "need space", "need time"],
    "rest":          ["rest", "tired", "sleep", "break", "vacation", "exhausted",
                      "recharge", "restore"],
    "renewal":       ["renewed", "refreshed", "energized", "reset", "new start"],
    "presence":      ["present", "presence", "mindful", "here", "now", "paying attention"],
    "attention":     ["attention", "focus", "notice", "noticing", "aware", "awareness"],

    # Growth
    "growth":        ["grow", "growth", "learn", "learning", "improve", "better",
                      "progress", "developing"],
    "change":        ["change", "changing", "different", "shift", "transition"],
    "learning":      ["learning", "lesson", "realized", "understand", "understanding"],
    "humility":      ["wrong", "mistake", "humble", "humility", "admit", "failed"],

    # Teaching specific
    "teaching":      ["teach", "teaching", "teacher", "classroom", "lesson", "class",
                      "students", "instruction"],
    "self_care":     ["self care", "taking care", "my needs", "my wellbeing", "myself"],
    "sustainability": ["sustainable", "long term", "year after year", "keep going",
                       "not burning out"],
    "justice":       ["fair", "fairness", "equity", "justice", "equal", "equitable",
                      "bias", "discrimination"],
    "belonging":     ["belong", "belonging", "included", "excluded", "welcome", "safe"],

    # Reflection
    "self_awareness": ["realize", "noticed", "seeing myself", "reflecting", "honest"],
    "wisdom":        ["wisdom", "wise", "insight", "learned", "know now"],
    "honesty":       ["honest", "truth", "truthful", "real", "genuine", "authentic"],
}

# Reverse map: word → set of tags it implies
_WORD_TO_TAGS: dict[str, set[str]] = {}
for _tag, _words in THEME_SYNONYMS.items():
    for _word in _words:
        _WORD_TO_TAGS.setdefault(_word, set()).add(_tag)


def _stem(word: str) -> str:
    """Minimal suffix-stripping stem — no library needed."""
    for suffix in ("ing", "tion", "ness", "ful", "less", "ed", "ly", "er", "est", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def extract_themes(transcript: str) -> set[str]:
    """
    Extract a set of canonical tag names from a transcript using:
    1. Direct tag matching (stemmed)
    2. Synonym expansion via THEME_SYNONYMS
    """
    words = re.findall(r"\b[a-z]{3,}\b", transcript.lower())

    # Common stopwords to skip
    stopwords = {
        "that", "this", "with", "from", "they", "have", "been", "were",
        "will", "just", "like", "what", "when", "where", "which", "while",
        "about", "there", "their", "them", "then", "than", "into", "onto",
        "even", "also", "very", "more", "some", "well", "back", "good",
        "really", "going", "does", "doing", "said", "want", "need",
        "know", "think", "feel", "felt", "felt", "come", "came", "make",
        "made", "people", "thing", "things", "today", "yesterday",
    }

    themes: set[str] = set()

    # All tags from sources.json for direct matching
    all_tags: set[str] = set()
    for guide in SOURCES.get("guides", {}).values():
        for p in guide.get("passages", []):
            all_tags.update(p.get("tags", []))

    for word in words:
        if word in stopwords:
            continue

        stemmed = _stem(word)

        # 1. Direct match against all known tags (stemmed)
        for tag in all_tags:
            if stemmed == _stem(tag) or word == tag:
                themes.add(tag)

        # 2. Synonym expansion
        for lookup in (word, stemmed):
            if lookup in _WORD_TO_TAGS:
                themes.update(_WORD_TO_TAGS[lookup])

        # 3. Multi-word phrases (bigrams) — check "word1 word2" patterns
        # (handled implicitly via THEME_SYNONYMS entries with spaces)

    return themes


def score_passage(passage: dict, themes: set[str]) -> int:
    """Score a passage by tag overlap with extracted themes."""
    return len(set(passage.get("tags", [])) & themes)


def select_passages(selected_guides: list[str], themes: set[str]) -> list[dict]:
    """
    Select top 3 passages globally with primary guide guarantee.

    Algorithm:
    1. Lock in top-scoring passage from selected_guides[0] (primary voice guarantee)
    2. Score all remaining passages across all guides
    3. Fill 2 remaining slots with best overall matches
    4. Deduplicate if primary guide's best already won globally

    Returns list of dicts: { guide_id, passage, score }
    """
    guides_db = SOURCES.get("guides", {})

    if not selected_guides:
        return []

    primary_id = selected_guides[0]
    primary_guide = guides_db.get(primary_id)

    # Score every passage across all selected guides
    all_scored: list[dict] = []
    for guide_id in selected_guides:
        guide = guides_db.get(guide_id)
        if not guide:
            continue
        for passage in guide.get("passages", []):
            score = score_passage(passage, themes)
            all_scored.append({
                "guide_id":  guide_id,
                "guide":     guide,
                "passage":   passage,
                "score":     score,
            })

    all_scored.sort(key=lambda x: x["score"], reverse=True)

    selected: list[dict] = []
    used_ids: set[str] = set()

    # Step 1: primary voice guarantee
    if primary_guide:
        primary_passages = [
            x for x in all_scored if x["guide_id"] == primary_id
        ]
        if primary_passages:
            best_primary = primary_passages[0]  # already sorted by score
            selected.append(best_primary)
            used_ids.add(best_primary["passage"]["id"])

    # Step 2: fill remaining 2 slots from global best (no duplicates)
    for item in all_scored:
        if len(selected) >= 3:
            break
        if item["passage"]["id"] not in used_ids:
            selected.append(item)
            used_ids.add(item["passage"]["id"])

    return selected


def build_sources_used(selected: list[dict]) -> list[dict]:
    """
    Build the deterministic sources_used record from selected passages.
    This is what gets stored alongside the note — no model involvement.
    """
    return [
        {
            "source_id": item["passage"]["id"],
            "author":    item["guide"]["label"],
            "work":      item["guide"].get("work", ""),
            "ref":       item["passage"].get("ref", ""),
            "excerpt":   item["passage"]["text"][:120] + ("…" if len(item["passage"]["text"]) > 120 else ""),
        }
        for item in selected
    ]


# ── Reflect prompt ────────────────────────────────────────────────────────────

def reflect_prompt(transcript: str, user: dict, max_tags: int = 5) -> tuple[str, list[dict]]:
    """
    Returns (prompt_string, sources_used).
    sources_used is deterministic — set before the model sees anything.
    """
    reflect_config  = user.get("reflect_config") or {}
    selected_guides = reflect_config.get("selected_guides") or []
    custom_passages = reflect_config.get("custom_passages") or {}
    formative_docs  = user.get("formative_docs") or []
    reflect_docs    = user.get("reflect_docs") or []

    # Apply any user-customized passages
    guides_db = SOURCES.get("guides", {})
    for guide_id, custom_text in custom_passages.items():
        if guide_id in guides_db and guides_db[guide_id].get("passages"):
            guides_db[guide_id]["passages"][0]["text"] = custom_text

    # Extract themes from transcript
    themes = extract_themes(transcript)

    # Select passages (deterministic)
    selected = select_passages(selected_guides, themes)
    sources_used = build_sources_used(selected)

    # Build passage block for prompt
    passage_lines = []
    for item in selected:
        g = item["guide"]
        p = item["passage"]
        passage_lines.append(
            f'{g["label"]} ({g.get("work","")}, {p.get("ref","")}):\n"{p["text"]}"'
        )
    passages_block = "\n\n".join(passage_lines) if passage_lines else "(no voices selected)"

    # Voice names for context line
    voice_names = []
    for guide_id in selected_guides:
        g = guides_db.get(guide_id)
        if g:
            voice_names.append(g["label"])
    voices_context = ", ".join(voice_names) if voice_names else "no specific voices selected"

    # Formative + reflect documents
    doc_sections = []
    for doc in (formative_docs + reflect_docs):
        summary   = doc.get("summary", "")
        quotes    = doc.get("key_quotes", [])
        name      = doc.get("name", "Uploaded document")
        if summary or quotes:
            lines = [f"[{name}]", summary]
            for q in quotes[:3]:
                lines.append(f'  • "{q}"')
            doc_sections.append("\n".join(lines))
    docs_block = "\n\n".join(doc_sections) if doc_sections else ""
    tasks_context_summary = (user.get("tasks_context_summary") or "").strip()
    history_context_summary = (user.get("history_context_summary") or "").strip()
    tasks_block = (
        f"\nRelevant current obligations:\n{tasks_context_summary}\n"
        if tasks_context_summary else ""
    )
    history_block = (
        f"\nRelevant prior reflection context:\n{history_context_summary}\n"
        if history_context_summary else ""
    )

    prompted_by = f"Voices the user has chosen: {voices_context}"

    prompt = f"""You are a deeply attentive reflective partner for a teacher. \
Your role is to help them make meaning from their day using the wisdom voices they have chosen.

{prompted_by}

The following passages were selected because they relate thematically to what the user shared. \
Engage with them genuinely — do not force connections that aren't there:

{passages_block}
{f'''
The following documents are part of the user's personal context:

{docs_block}
''' if docs_block else ''}
{tasks_block}
{history_block}
The user recorded this reflection:

---
{transcript}
---

Your task:
1. Honor the emotional truth of what they shared first — then bring in wisdom.
2. Use the voices above as genuine interlocutors, not decoration.
3. Identify the deepest tension or insight in what they said.
4. Suggest one concrete thing they can carry into tomorrow.
5. For each passage you drew on, write one sentence explaining the specific connection \
   to what the user said. Be honest — if a passage didn't apply, omit it from influenced_by.
6. If current obligations are included, use them only when they genuinely clarify what is weighing on the user.
7. If the user is revising, resisting, or deepening an earlier framing or voice, name that clearly.
8. If voices or perspectives come into meaningful agreement or conflict, note it when doing so helps the user.

Respond with strict JSON only — no markdown, no extra keys.

{{
  "title": "short evocative title (5–8 words)",
  "summary": "3–5 sentences. Honor the emotional truth. Weave in wisdom from the voices where it fits naturally.",
  "insight": "The deepest tension, question, or reframe worth sitting with. Specific to their words.",
  "action_items": ["One concrete thing to carry into tomorrow — specific, human-scale"],
  "suggested_tags": ["up to {max_tags} lowercase tags"],
  "influenced_by": [
    {{
      "source_id": "exact source_id from the passages above",
      "because": "one sentence: why this passage connected to what they shared"
    }}
  ]
}}

Rules:
- Return only the JSON object
- No invented facts
- influenced_by entries must correspond to source_ids from the passages above
- Max {max_tags} tags
- Tone: warm, thoughtful, like a trusted colleague who also reads deeply
- Treat prior reflections as context, not authority
"""
    return prompt, sources_used


# ── Professional / Teaching prompt ───────────────────────────────────────────

def professional_prompt(
    transcript: str,
    profession: str,
    tasks_context_summary: str = "",
    history_context_summary: str = "",
    max_tags: int = 5,
) -> str:
    tasks_block = (
        f"\nRelevant current obligations:\n{tasks_context_summary}\n"
        if tasks_context_summary else ""
    )
    history_block = (
        f"\nRelevant prior reflection context:\n{history_context_summary}\n"
        if history_context_summary else ""
    )
    return f"""You are a professional development assistant for a {profession}.

They recorded the following work note:

---
{transcript}
---
{tasks_block}
{history_block}

Respond with strict JSON only — no markdown wrapper, no extra keys.

{{
  "title": "short professional title (5–8 words)",
  "summary": "2–4 sentences capturing the key points and context.",
  "action_items": ["specific, actionable next steps — concrete and time-bound where possible"],
  "best_practice": "One relevant best practice for a {profession} that applies to what they described.",
  "suggested_tags": ["up to {max_tags} lowercase tags"]
}}

Rules:
- Return only the JSON object
- No invented facts
- If current obligations are included, use them only when they clearly help interpret the note
- If prior reflection context is included, use it to notice changed judgment, follow-through, or productive disagreement
- Max {max_tags} tags
"""


TEACHING_FRAMEWORKS = """
Key pedagogical frameworks to draw on where genuinely relevant:
- Universal Design for Learning (UDL)
- Backward Design (Wiggins & McTighe)
- Bloom's Taxonomy
- Zone of Proximal Development & Scaffolding (Vygotsky)
- Formative vs. Summative Assessment
- Culturally Responsive Pedagogy (Ladson-Billings)
- Trauma-Informed Practice
- Social-Emotional Learning (SEL / CASEL)
- Differentiated Instruction
- Inquiry-Based Learning
"""

TEACHING_CONCERNS = """
Watch for and address when present:
- Student engagement and motivation
- Differentiation (IEPs, 504s, ELLs, gifted)
- Classroom management and community
- Assessment design and feedback
- Teacher workload and wellbeing
- Family communication
- Equity gaps
- Curriculum pacing pressure
"""


def teaching_practical_prompt(transcript: str, user: dict, max_tags: int = 5) -> str:
    preferred_name = (user.get("preferred_name") or user.get("name") or "the teacher").strip()
    preferred_pronouns = (user.get("preferred_pronouns") or "").strip()
    grades       = user.get("grade_levels") or []
    subjects     = user.get("subjects") or ""
    standards    = user.get("state_standards") or []
    school_state = user.get("school_state") or ""
    formative    = user.get("formative_docs") or []
    prof_docs    = user.get("professional_docs") or []
    tasks_context_summary = (user.get("tasks_context_summary") or "").strip()
    history_context_summary = (user.get("history_context_summary") or "").strip()

    grade_str    = ", ".join(grades) if grades else "unspecified grades"
    subject_str  = subjects if subjects else "unspecified subjects"
    state_str    = f" in {school_state}" if school_state else ""
    stds_str     = (
        f"\nRelevant standards: {', '.join(standards)}. "
        "Name specific standard domains when action items map cleanly to them."
    ) if standards else ""

    doc_sections = []
    for doc in (formative + prof_docs):
        summary = doc.get("summary", "")
        quotes  = doc.get("key_quotes", [])
        name    = doc.get("name", "Uploaded document")
        if summary or quotes:
            lines = [f"[{name}]", summary]
            for q in quotes[:3]:
                lines.append(f'  • "{q}"')
            doc_sections.append("\n".join(lines))
    docs_block = ("\n\nUser's documents:\n" + "\n\n".join(doc_sections)) if doc_sections else ""

    tasks_block = (
        f"\nRelevant current obligations:\n{tasks_context_summary}\n"
        if tasks_context_summary else ""
    )
    history_block = (
        f"\nRelevant prior reflection context:\n{history_context_summary}\n"
        if history_context_summary else ""
    )
    identity_bits = []
    if preferred_pronouns:
        identity_bits.append(f"pronouns: {preferred_pronouns}")
    identity_line = (
        f"\nTeacher identity: name: {preferred_name}; " + "; ".join(identity_bits) + "."
        if identity_bits else f"\nTeacher identity: name: {preferred_name}."
    )

    prompt = f"""You are a dedicated instructional coach for {preferred_name}{state_str} \
teaching {subject_str} at the {grade_str} level.

{identity_line}

{preferred_name} recorded the following voice note about their teaching day:

---
{transcript}
---
{docs_block}
{tasks_block}
{history_block}
{TEACHING_FRAMEWORKS}
{TEACHING_CONCERNS}
{stds_str}

Respond with strict JSON only — no markdown wrapper, no extra keys.

{{
  "title": "short specific title (5–8 words)",
  "summary": "3–5 sentences: what happened, what matters, the underlying teaching challenge or win.",
  "insight": "One specific pedagogical insight grounded in the frameworks above. Concrete to their grade/subject.",
  "action_items": [
    "Before or during next class — specific",
    "This week or before next unit",
    "Optional: wellbeing or reflection action"
  ],
  "concerns": "One sentence naming any equity, student-wellbeing, or sustainability concern — or null.",
  "best_practice": "One evidence-based practice that directly applies. Name the framework.",
  "suggested_tags": ["up to {max_tags} lowercase tags including grade band, subject, framework"]
}}

Rules:
- Return only the JSON object
- No invented facts about students or events not mentioned
- Max {max_tags} tags
- Honor their lived experience first, then add pedagogy
- If current obligations are included, use them only when they genuinely illuminate the teaching context
- If prior reflection context is included, use it to notice repeated patterns, changed judgment, follow-through, or a clear disagreement with an earlier framing
- Refer to {preferred_name} by name where natural
- Respect these pronouns when needed: {preferred_pronouns or "use neutral phrasing if possible"}
- Tone: warm, collegial, specific — trusted coach who knows their context
"""
    return prompt


def teaching_prompt(transcript: str, user: dict, max_tags: int = 5) -> tuple[str, list[dict]]:
    prompt = teaching_practical_prompt(transcript, user, max_tags)
    return prompt, []


# ── Lane registry ─────────────────────────────────────────────────────────────

LANES: dict[str, dict[str, Any]] = {
    "reflect": {
        "label":       "Reflect",
        "description": "Personal reflections grounded in the voices you choose.",
        "icon":        "🌿",
    },
    "professional": {
        "label":       "Professional",
        "description": "Teaching notes enriched with pedagogical best practices.",
        "icon":        "💼",
    },
}


def build_prompt(lane: str, transcript: str, user: dict, max_tags: int = 5) -> tuple[str, list[dict]]:
    """
    Returns (prompt_string, sources_used).
    sources_used is empty list for non-reflect lanes.
    """
    if lane == "reflect":
        return reflect_prompt(transcript, user, max_tags)

    elif lane == "professional":
        profession = (user.get("profession") or "").lower().strip()
        is_teacher = (
            not profession
            or "teach" in profession
            or "educat" in profession
            or "instructor" in profession
            or "professor" in profession
        )
        if is_teacher:
            return teaching_prompt(transcript, user, max_tags)
        return professional_prompt(
            transcript,
            profession,
            (user.get("tasks_context_summary") or "").strip(),
            (user.get("history_context_summary") or "").strip(),
            max_tags,
        ), []

    else:
        return (
            f'Summarize this voice note as JSON: {{"title":"...","summary":"...",'
            f'"action_items":[],"suggested_tags":[]}}\nTranscript:\n{transcript}',
            []
        )
