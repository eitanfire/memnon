"""Colorado school district EAP directory: the fields both the web pages
and the connector's find_eap tool give out.

data/eap/colorado-districts.json is the source of truth;
scripts/build_eap_pages.py renders it into public/eap/ and copies it here
(eap/colorado-districts.json) so the Cloud Function can read it.

- Low-confidence entries say "ask HR" and never guess.
- Company and group codes are shown; usernames and passwords never are.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

DATA = Path(__file__).parent / "eap" / "colorado-districts.json"
SITE = "https://memnon.app"
CRISIS_LINE = "In crisis? Call or text 988 (Suicide & Crisis Lifeline, 24/7)."

# Reference entries are not districts: no page of their own, a note on the index.
REFERENCE_SLUGS = {"colorado-state-c-seap"}

ASK_HR = "We could not confirm this district's EAP from a public source; ask HR."
LOGIN_IN_GUIDE = "Login details are in your district's benefits guide"
SECRET = re.compile(r"\b(user\s*name|password)\b", re.IGNORECASE)


class DataError(ValueError):
    pass


def load(path: Path = DATA) -> list[dict]:
    entries = json.loads(path.read_text())
    for entry in entries:
        name = entry.get("slug") or entry.get("district_name") or "entry"
        if not entry.get("official_source_urls"):
            raise DataError(f"{name}: official_source_urls is required")
        if not entry.get("slug") or not entry.get("last_verified"):
            raise DataError(f"{name}: slug and last_verified are required")
    return entries


def confirmed(entry: dict) -> bool:
    return entry.get("has_eap") is True and entry.get("confidence") in ("high", "medium")


def has_page(entry: dict) -> bool:
    return entry.get("has_eap") is not None and entry["slug"] not in REFERENCE_SLUGS


def guide_url(entry: dict) -> str:
    urls = entry["official_source_urls"]
    return next((u for u in urls if u.lower().endswith(".pdf")), urls[0])


def login(entry: dict) -> tuple[str, bool]:
    """web_access without usernames or passwords. Returns (text, redacted)."""
    text = entry.get("web_access") or ""
    without_asides = re.sub(r"\s*\([^)]*\)", lambda m: "" if SECRET.search(m.group()) else m.group(), text)
    parts = [p.strip() for p in re.split(r",\s*", without_asides) if p.strip()]
    kept = [p for p in parts if not SECRET.search(p)]
    return ", ".join(kept), bool(SECRET.search(text))


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.rstrip("'\"’”)").endswith((".", "!", "?")) else text + "."


def facts(entry: dict) -> dict:
    """Plain-text answers shared by the page and its FAQPage JSON-LD."""
    name = entry["district_name"]
    if not confirmed(entry):
        return {"answer": ASK_HR, "sessions": "", "contact": ""}
    login_text, redacted = login(entry)
    sessions = _sentence(f"Sessions: {entry['sessions']}") if entry.get("sessions") else \
        "Sessions: the district does not publish a limit; ask when you call."
    phone = _sentence(f"Phone: {entry['phone']}") if entry.get("phone") else \
        "Phone: the district does not publish one; ask HR."
    log_in = ""
    if login_text:
        log_in = _sentence(f"Log in: {login_text}")
    if redacted:
        log_in += f" {LOGIN_IN_GUIDE}."
    answer = " ".join(filter(None, [
        f"Yes. {name} has an employee assistance program.",
        _sentence(f"Provider: {entry['provider']}") if entry.get("provider") else "",
        sessions,
        phone,
        log_in.strip(),
    ]))
    return {
        "answer": answer,
        "sessions": sessions.removeprefix("Sessions: "),
        "contact": " ".join(filter(None, [phone, log_in.strip()])),
    }


def page_url(entry: dict) -> str:
    return f"{SITE}/eap/{entry['slug']}" if has_page(entry) else f"{SITE}/eap"


# ── find_eap ─────────────────────────────────────────────────────────────────

_STOPWORDS = {"school", "schools", "district", "public", "county", "the", "of", "and", "sd"}
_STATE_WORDS = {"state", "cseap", "seap", "university", "college", "agency", "department",
                "judicial", "legislature", "legislative", "colorado"}
_MATCH_CUTOFF = 0.75


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace("-", "")).split())


def _tokens(text: str) -> set[str]:
    return set(_norm(text).split()) - _STOPWORDS


def _aliases(entry: dict) -> list[str]:
    aliases = [entry["district_name"], entry.get("short_name") or "", entry["slug"].replace("-", " ")]
    # A city names a district, not the state program headquartered there.
    if entry["slug"] not in REFERENCE_SLUGS:
        aliases.append(entry.get("city") or "")
    return [a for a in aliases if a]


def _score(query: str, alias: str) -> float:
    q, a = _norm(query), _norm(alias)
    if q == a:
        return 1.0
    q_tokens, a_tokens = _tokens(query), _tokens(alias)
    if q_tokens and q_tokens <= a_tokens:
        return 0.9
    fuzzy = 0.0
    if q_tokens:
        close = sum(
            1 for t in q_tokens
            if any(SequenceMatcher(None, t, u).ratio() >= 0.8 for u in a_tokens)
        )
        fuzzy = 0.85 * close / len(q_tokens)
    return max(fuzzy, SequenceMatcher(None, q, a).ratio())


def match(query: str, entries: list[dict]) -> list[dict]:
    """Entries whose name, short name, slug or city best match the query.
    Several entries come back only when they tie (two districts in one city)."""
    if not _norm(query):
        return []
    scored = [(max(_score(query, a) for a in _aliases(e)), e) for e in entries]
    top = max(score for score, _ in scored)
    if top < _MATCH_CUTOFF:
        return []
    return [e for score, e in scored if score >= top - 1e-9]


def answer_fields(entry: dict) -> dict:
    """The fields the district's web page shows, plus its URL and 988."""
    fields = {
        "district_name": entry["district_name"],
        "city": entry.get("city") or "",
        "answer": facts(entry)["answer"],
        "confidence": entry.get("confidence"),
        "last_verified": entry["last_verified"],
        "official_source_urls": list(entry["official_source_urls"]),
        "page_url": page_url(entry),
        "crisis": CRISIS_LINE,
    }
    if not confirmed(entry):
        return fields
    login_text, redacted = login(entry)
    fields.update({
        "provider": entry.get("provider") or "",
        "sessions": entry.get("sessions") or "",
        "session_basis_conflict": entry.get("session_basis_conflict") or "",
        "phone": entry.get("phone") or "",
        "log_in": login_text,
        "who_is_covered": entry.get("who_is_covered") or "",
        "extra_benefits": entry.get("extra_benefits") or "",
    })
    if redacted:
        fields["login_details"] = f"{LOGIN_IN_GUIDE}: {guide_url(entry)}"
    if entry.get("confidence") == "medium":
        fields["confirm_with_hr"] = "Some of this comes from a past-year or benefits-trust guide, so confirm it with HR."
    if entry["slug"] in REFERENCE_SLUGS:
        fields["note"] = entry.get("notes") or ""
    return fields


def c_seap_note(entries: list[dict]) -> str:
    c_seap = next((e for e in entries if e["slug"] in REFERENCE_SLUGS), None)
    if not c_seap:
        return ""
    return (
        f"State of Colorado employees and many public college staff use C-SEAP "
        f"({c_seap.get('phone')}, {c_seap.get('web_access')}). It does not cover most K-12 school "
        f"district staff, who use their district's EAP."
    )


def find_eap(district: str, entries: list[dict] | None = None) -> dict:
    entries = entries if entries is not None else load()
    matches = match(district, entries)
    if not matches:
        result = {
            "status": "not_found",
            "message": f"{district.strip() or 'That district'} is not in the directory yet. Ask your HR or benefits office.",
            "directory_url": f"{SITE}/eap",
            "crisis": CRISIS_LINE,
        }
        if _tokens(district) & _STATE_WORDS:
            result["c_seap_note"] = c_seap_note(entries)
        return result
    if len(matches) == 1:
        return {"status": "found", **answer_fields(matches[0])}
    return {
        "status": "several_matches",
        "message": "More than one district matches; pick one.",
        "matches": [answer_fields(e) for e in matches],
        "crisis": CRISIS_LINE,
    }
