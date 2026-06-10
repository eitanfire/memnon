"""
Memnon SaaS — Firebase Cloud Functions.

Two functions:
  api      HTTP function — Google Drive OAuth flow + user setup + data endpoints
  worker   Scheduled function — polls every user's Drive inbox every minute

Environment variables (set via Firebase Secret Manager):
  OPENAI_API_KEY          Your OpenAI key — used for Whisper + GPT-4o-mini
  GOOGLE_CLIENT_SECRETS   Contents of the OAuth client secrets JSON from Google Cloud Console
  FLASK_SECRET            Any random string for Flask session signing

Set secrets:
  firebase functions:secrets:set OPENAI_API_KEY
  firebase functions:secrets:set GOOGLE_CLIENT_SECRETS
  firebase functions:secrets:set FLASK_SECRET

OAuth redirect URI to register in Google Cloud Console:
  https://api-4hth6oktaa-uc.a.run.app/auth/callback
"""

import base64
import hashlib
import io
import json
import os
import re
import secrets
import tempfile
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import firebase_admin
from firebase_admin import auth as fb_auth
from firebase_admin import firestore
from firebase_functions import https_fn, options, scheduler_fn
from flask import Flask, jsonify, redirect, request
from flask_cors import CORS
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

from audio_generation import synthesize_reflection_bytes, synthesize_reflection_mp3
from lanes import extract_themes, professional_prompt, reflect_prompt, teaching_practical_prompt

# ── lazy init — do NOT call at module level (hangs CLI analysis) ──────────────

_firebase_app = None
_firestore_client = None


def _get_db():
    global _firebase_app, _firestore_client
    if _firebase_app is None:
        _firebase_app = firebase_admin.initialize_app()
    if _firestore_client is None:
        _firestore_client = firestore.client()
    return _firestore_client


flask_app = Flask(__name__)
flask_app.secret_key = os.environ.get("FLASK_SECRET", "dev-change-me")
CORS(flask_app, origins=[
    "https://memnon.app",
    "https://memnon-app.web.app",
    "https://memnon-app.firebaseapp.com",
    "http://localhost:5000",
    "http://localhost:5050",
    "http://localhost:8000",
    "http://localhost:8080",
])

# ── constants ─────────────────────────────────────────────────────────────────

BASE_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",  # access only files created by this app
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]
TASKS_READONLY_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"
API_BASE = "https://api-4hth6oktaa-uc.a.run.app"
REDIRECT_URI = f"{API_BASE}/auth/callback"
FRONTEND_URL = "https://memnon.app"
ALLOWED_FRONTEND_ORIGINS = {
    "https://memnon.app",
    "https://memnon-app.web.app",
    "https://memnon-app.firebaseapp.com",
    "http://localhost:5000",
    "http://localhost:5050",
    "http://localhost:8000",
    "http://localhost:8080",
}

AUDIO_MIME_TYPES = {
    "audio/mp4", "audio/x-m4a", "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/aac", "audio/flac",
    "audio/webm", "audio/ogg", "video/mp4",
}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".webm", ".ogg"}
IMAGE_MIME_PREFIX = "image/"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_NARRATION_VOICE = "en-IE-EmilyNeural"
VOICE_PREVIEW_TEXT = "Did Nature, creator of all,\ngive perception and voice to stone?"
NARRATION_VOICES = {
    "en-IE-EmilyNeural": {"label": "Emily", "descriptor": "warm"},
    "en-US-JennyNeural": {"label": "Jenny", "descriptor": "clear"},
    "en-GB-RyanNeural": {"label": "Ryan", "descriptor": "measured"},
}
GOOGLE_TASKS_WEB_URL = "https://tasks.google.com/tasks/"

# ── helpers ───────────────────────────────────────────────────────────────────

def _client_secrets_path() -> str:
    raw = os.environ.get("GOOGLE_CLIENT_SECRETS", "")
    if not raw:
        raise RuntimeError("GOOGLE_CLIENT_SECRETS env var not set")
    p = Path("/tmp/google_client_secrets.json")
    if raw.strip().startswith("{"):
        p.write_text(raw)
    else:
        p.write_text(Path(raw).read_text())
    return str(p)


def _verify_firebase_token(req) -> str | None:
    """Verify Firebase ID token from Authorization header. Returns uid or None."""
    header = req.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        return fb_auth.verify_id_token(header[7:])["uid"]
    except Exception:
        return None


def _safe_frontend_return_url(candidate: str | None) -> str:
    """Allow redirects only to known frontend origins."""
    if not candidate:
        return FRONTEND_URL
    try:
        parsed = urllib.parse.urlparse(candidate)
    except Exception:
        return FRONTEND_URL
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in ALLOWED_FRONTEND_ORIGINS:
        return FRONTEND_URL
    path = parsed.path or "/"
    safe_url = f"{origin}{path}"
    if parsed.query:
        safe_url += f"?{parsed.query}"
    return safe_url


def _append_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _requested_google_scopes(include_tasks: bool = False) -> list[str]:
    scopes = list(BASE_GOOGLE_SCOPES)
    if include_tasks:
        scopes.append(TASKS_READONLY_SCOPE)
    return scopes


def _scopes_from_token_data(token_data: dict | None) -> set[str]:
    if not token_data:
        return set()
    raw = token_data.get("scopes")
    if isinstance(raw, list):
        return {scope for scope in raw if isinstance(scope, str) and scope}
    if isinstance(raw, str):
        return {scope for scope in raw.split() if scope}
    return set()


def _merge_google_token(existing_token: dict | None, new_token: dict) -> dict:
    """Preserve broader existing grants when a narrower re-auth returns later."""
    if not existing_token:
        return new_token

    existing_scopes = _scopes_from_token_data(existing_token)
    new_scopes = _scopes_from_token_data(new_token)

    if existing_token.get("refresh_token") and not new_token.get("refresh_token"):
        new_token["refresh_token"] = existing_token["refresh_token"]

    if TASKS_READONLY_SCOPE in existing_scopes and TASKS_READONLY_SCOPE not in new_scopes:
        merged = dict(existing_token)
        merged["scopes"] = sorted(existing_scopes | new_scopes)
        for key in ("client_id", "client_secret", "token_uri", "refresh_token"):
            if new_token.get(key):
                merged[key] = new_token[key]
        return merged

    if existing_scopes or new_scopes:
        new_token["scopes"] = sorted(existing_scopes | new_scopes)
    return new_token


def _drive_creds(uid: str) -> Credentials | None:
    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return None
    token_data = doc.to_dict().get("google_drive_token")
    if not token_data:
        return None
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", BASE_GOOGLE_SCOPES),
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _get_db().collection("users").document(uid).update(
                {"google_drive_token": json.loads(creds.to_json())}
            )
        except Exception as exc:
            print(f"Token refresh failed for {uid}: {exc}")
            return None
    return creds


# ── Drive helpers ──────────────────────────────────────────────────────────────

def _find_or_create_folder(service, name: str, parent_id: str | None = None) -> str:
    parent_q = f"and '{parent_id}' in parents" if parent_id else "and 'root' in parents"
    q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
         f"{parent_q} and trashed=false")
    files = service.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return service.files().create(body=meta, fields="id").execute()["id"]


def _drive_service_for_user(uid: str):
    creds = _drive_creds(uid)
    if not creds:
        return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _tasks_service_for_user(uid: str):
    creds = _drive_creds(uid)
    if not creds:
        return None
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def _user_tasks_connected(user_data: dict | None) -> bool:
    token_scopes = _scopes_from_token_data((user_data or {}).get("google_drive_token"))
    if TASKS_READONLY_SCOPE in token_scopes:
        return True
    return bool((user_data or {}).get("google_tasks_connected"))


def _normalize_task_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "title": (item.get("title") or "").strip(),
        "due": item.get("due"),
        "notes": (item.get("notes") or "").strip(),
        "status": item.get("status", ""),
        "updated": item.get("updated"),
    }


def _fetch_open_tasks_for_user(uid: str, user_data: dict, limit: int = 12) -> list[dict]:
    if not _user_tasks_connected(user_data):
        return []
    tasklist_id = (user_data.get("google_tasks_list_id") or "").strip()
    if not tasklist_id:
        return []
    service = _tasks_service_for_user(uid)
    if not service:
        return []
    try:
        result = service.tasks().list(
            tasklist=tasklist_id,
            maxResults=max(1, min(limit, 20)),
            showCompleted=False,
            showHidden=False,
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not fetch tasks for reflection context: {exc}")
        return []
    items = []
    for item in result.get("items", []):
        if item.get("status") == "completed":
            continue
        normalized = _normalize_task_item(item)
        if normalized["title"]:
            items.append(normalized)
    return items


def _derive_task_context(transcript: str, tasks: list[dict]) -> list[str]:
    if not tasks:
        return []

    transcript_lower = transcript.lower()
    transcript_tokens = set(re.findall(r"\b[a-z]{4,}\b", transcript_lower))
    stopwords = {
        "about", "after", "again", "because", "being", "could", "every", "first",
        "from", "have", "into", "just", "like", "many", "more", "most", "need",
        "really", "some", "than", "that", "their", "there", "these", "they",
        "this", "today", "very", "what", "when", "where", "which", "with", "would",
    }
    transcript_tokens = {token for token in transcript_tokens if token not in stopwords}

    def display_task(task: dict) -> str:
        return f'{task["title"]}' + (f' (due {task["due"][:10]})' if task.get("due") else "")

    top_tasks = tasks[:3]
    chosen_by_id = {task["id"]: task for task in top_tasks if task.get("id")}

    scored: list[tuple[int, dict]] = []
    for task in tasks:
        title = task.get("title", "")
        notes = task.get("notes", "")
        haystack = f"{title} {notes}".strip().lower()
        if not haystack:
            continue
        task_tokens = {token for token in re.findall(r"\b[a-z]{4,}\b", haystack) if token not in stopwords}
        overlap = len(task_tokens & transcript_tokens)
        title_phrase_match = title.lower() in transcript_lower if len(title) >= 8 else False
        score = overlap + (2 if title_phrase_match else 0)
        if score > 0:
            scored.append((score, task))

    scored.sort(key=lambda item: item[0], reverse=True)
    for _, task in scored:
        if len(chosen_by_id) >= 6:
            break
        task_id = task.get("id")
        if task_id and task_id not in chosen_by_id:
            chosen_by_id[task_id] = task

    ordered = []
    seen = set()
    for task in top_tasks:
        task_id = task.get("id") or task.get("title")
        if task_id in seen:
            continue
        ordered.append(task)
        seen.add(task_id)
    for _, task in scored:
        task_id = task.get("id") or task.get("title")
        if task_id in seen:
            continue
        if task_id in chosen_by_id:
            ordered.append(task)
            seen.add(task_id)
        if len(ordered) >= len(chosen_by_id):
            break

    return [display_task(task) for task in ordered]


def _find_or_create_media_folder(service) -> str:
    return _find_or_create_folder(service, "memnon-media")


def _find_or_create_recordings_folder(service) -> str:
    return _find_or_create_folder(service, "memnon-recordings")


def _find_or_create_reflections_folder(service) -> str:
    return _find_or_create_folder(service, "memnon-reflections")


def _is_audio(f: dict) -> bool:
    return (f.get("mimeType") in AUDIO_MIME_TYPES or
            any(f.get("name", "").lower().endswith(ext) for ext in AUDIO_EXTENSIONS))


def _normalize_narration_voice(raw: str | None) -> str:
    if raw in NARRATION_VOICES:
        return raw  # type: ignore[return-value]
    return DEFAULT_NARRATION_VOICE


def _normalize_reflection_style(raw: str | None) -> str:
    if raw in {"practical", "grounded", "complete"}:
        return raw  # type: ignore[return-value]
    return "complete"


def _is_teacher_profession(user_data: dict) -> bool:
    profession = (user_data.get("profession") or "").lower().strip()
    return (
        not profession
        or "teach" in profession
        or "educat" in profession
        or "instructor" in profession
        or "professor" in profession
    )


def _build_complete_reflection_prompt(
    transcript: str,
    user_data: dict,
    practical_result: dict,
    grounded_result: dict,
    sources_used: list[dict],
) -> str:
    preferred_name = (user_data.get("preferred_name") or user_data.get("name") or "the teacher").strip()
    tasks_context_summary = (user_data.get("tasks_context_summary") or "").strip()
    history_context_summary = (user_data.get("history_context_summary") or "").strip()
    tasks_block = (
        f"\nRelevant current obligations:\n{tasks_context_summary}\n"
        if tasks_context_summary else ""
    )
    history_block = (
        f"\nRelevant prior reflection context:\n{history_context_summary}\n"
        if history_context_summary else ""
    )
    sources_block = "\n\n".join(
        f'{source.get("author", "")} ({source.get("ref", "")}): "{source.get("excerpt", "")}"'
        for source in sources_used
    ) if sources_used else "(no guiding voices selected)"

    payload = json.dumps({
        "practical": practical_result,
        "grounded": grounded_result,
    }, ensure_ascii=False)

    return f"""You are integrating multiple perspectives into one grounded reflection for {preferred_name}.

The goal is not to flatten the perspectives. Hold them in conversation and produce one coherent return that helps {preferred_name} feel both supported and grounded.

Original transcript:
---
{transcript}
---
{tasks_block}
{history_block}
Perspective outputs:
{payload}

Guiding voice sources referenced in the grounded perspective:
{sources_block}

Respond with strict JSON only:
{{
  "title": "short specific title (5–8 words)",
  "summary": "3–5 sentences. Integrate the practical and grounded perspectives into one coherent reflection.",
  "insight": "One concise line naming the deepest tension, reframe, or pattern worth carrying forward.",
  "action_items": [
    "One practical next step",
    "Optional second step if it clearly matters"
  ],
  "suggested_tags": ["up to 5 lowercase tags"],
  "influenced_by": [
    {{
      "source_id": "exact source_id from the grounded perspective sources above",
      "because": "one sentence explaining why this source still matters in the integrated reflection"
    }}
  ]
}}

Rules:
- Return JSON only
- Preserve productive tension between perspectives when it matters
- If the current reflection revises, resists, or deepens an earlier framing, name that clearly
- If prior and current perspectives come into meaningful agreement, note that without flattening difference
- Do not introduce new facts
- The practical perspective should remain concrete
- The grounded perspective should deepen, not overwrite, the practical one
- If current obligations are included, use them lightly
- If no guiding voice truly matters, return an empty influenced_by array
"""


# ── pipeline ──────────────────────────────────────────────────────────────────

def _transcribe(audio_bytes: bytes, filename: str, api_key: str) -> str:
    boundary = "MemnonWhisper" + hashlib.md5(audio_bytes[:64]).hexdigest()
    ext = Path(filename).suffix.lower() or ".m4a"
    mime = "audio/mp4" if ext in (".m4a", ".mp4") else f"audio/{ext.lstrip('.')}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\nen\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["text"].strip()


def _summarize(prompt: str, api_key: str) -> dict:
    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = json.loads(resp.read())["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


NOTE_TEMPLATE = """\
---
title: {title}
date: {date}
lane: {lane}
tags: [{tags}]
{influenced_by_yaml}---

## Summary

{summary}

{extra}## Transcript

{transcript}
"""


def _render_influenced_by_yaml(sources_used: list) -> str:
    """Render sources_used as YAML frontmatter block."""
    if not sources_used:
        return ""
    lines = ["influenced_by:"]
    for s in sources_used:
        lines.append(f'  - source_id: {s.get("source_id", "")}')
        lines.append(f'    author: "{s.get("author", "")}"')
        lines.append(f'    work: "{s.get("work", "")}"')
        lines.append(f'    ref: "{s.get("ref", "")}"')
        because = s.get("because", "")
        if because:
            lines.append(f'    because: "{because}"')
    return "\n".join(lines) + "\n"


def _render_note(lane: str, ai: dict, transcript: str, filename: str,
                 sources_used: list | None = None) -> str:
    extra = []
    if ai.get("insight"):
        extra += ["## Insight", "", ai["insight"], ""]
    if ai.get("concerns") and ai["concerns"] not in (None, "null"):
        extra += ["## Note", "", ai["concerns"], ""]
    if ai.get("best_practice"):
        extra += ["## Best Practice", "", ai["best_practice"], ""]
    if ai.get("action_items"):
        extra += ["## Action Items", ""] + [f"- {i}" for i in ai["action_items"]] + [""]

    # Merge model-generated "because" into sources_used
    merged_sources = []
    if sources_used:
        model_influenced = {
            item.get("source_id"): item.get("because", "")
            for item in ai.get("influenced_by", [])
        }
        for s in sources_used:
            sid = s.get("source_id", "")
            merged_sources.append({**s, "because": model_influenced.get(sid, "")})

    return NOTE_TEMPLATE.format(
        title=ai.get("title", Path(filename).stem),
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        lane=lane,
        tags=", ".join(ai.get("suggested_tags", [])),
        summary=ai.get("summary", ""),
        extra="\n".join(extra) + ("\n" if extra else ""),
        transcript=transcript,
        influenced_by_yaml=_render_influenced_by_yaml(merged_sources),
    )


def _store_note_metadata(
    uid: str,
    ai: dict,
    sources_used: list,
    note_name: str,
    transcript: str,
    reflection_style: str,
) -> None:
    """Store recent note metadata in Firestore for dashboard display (keep last 10)."""
    try:
        note_meta = {
            "title":        ai.get("title", note_name),
            "summary":      ai.get("summary", "")[:300],
            "date":         datetime.now().strftime("%Y-%m-%d"),
            "created_at":   firestore.SERVER_TIMESTAMP,
            "note_name":    note_name,
            "influenced_by": sources_used or [],
            "reflection_style": reflection_style,
            "insight": ai.get("insight", "")[:240],
            "action_items": (ai.get("action_items") or [])[:3],
            "suggested_tags": (ai.get("suggested_tags") or [])[:8],
            "themes": sorted(list(extract_themes(transcript)))[:8],
            "voice_labels": [item.get("author", "") for item in (sources_used or []) if item.get("author")][:5],
            "transcript_excerpt": transcript[:240],
        }
        # Use a subcollection for notes — one doc per note
        _get_db().collection("users").document(uid)\
                 .collection("notes").add(note_meta)
    except Exception as exc:
        print(f"[{uid}] Warning: could not store note metadata: {exc}")


CALLBACK_CUE_PHRASES = (
    "last time",
    "previously",
    "before",
    "earlier",
    "you said",
    "you mentioned",
    "i don't agree",
    "i disagree",
    "i still think",
    "you framed it as",
    "that perspective",
    "that voice",
)


def _tokenize_history_text(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z]{3,}", (text or "").lower())
        if token not in {"that", "this", "with", "from", "they", "have", "been", "were", "them", "their", "about"}
    }


def _has_callback_cue(transcript: str) -> bool:
    lowered = (transcript or "").lower()
    return any(phrase in lowered for phrase in CALLBACK_CUE_PHRASES)


def _note_history_terms(note: dict) -> set[str]:
    terms = set()
    for field in ("title", "summary", "insight", "transcript_excerpt"):
        terms.update(_tokenize_history_text(note.get(field, "")))
    for item in note.get("themes", []) or []:
        terms.update(_tokenize_history_text(item))
    for item in note.get("voice_labels", []) or []:
        terms.update(_tokenize_history_text(item))
    return terms


def _build_history_note_line(note: dict) -> str:
    parts = []
    date = (note.get("date") or "").strip()
    style = _normalize_reflection_style(note.get("reflection_style"))
    title = (note.get("title") or "Untitled").strip()
    if date:
        parts.append(date)
    parts.append(style.title())
    header = " | ".join(parts) + f" | {title}"
    lines = [header]
    summary = (note.get("summary") or "").strip()
    if summary:
        lines.append(f"summary: {summary}")
    insight = (note.get("insight") or "").strip()
    if insight:
        lines.append(f"insight: {insight}")
    voices = note.get("voice_labels") or []
    if voices:
        lines.append(f"voices: {', '.join(voices[:3])}")
    themes = note.get("themes") or []
    if themes:
        lines.append(f"themes: {', '.join(themes[:5])}")
    return "\n".join(lines)


def _load_relevant_reflection_history(uid: str, transcript: str, max_items: int = 3) -> list[dict]:
    try:
        notes_ref = _get_db().collection("users").document(uid).collection("notes")
        try:
            docs = list(
                notes_ref.order_by("created_at", direction=firestore.Query.DESCENDING).limit(8).stream()
            )
        except Exception:
            docs = list(
                notes_ref.order_by("date", direction=firestore.Query.DESCENDING).limit(8).stream()
            )
    except Exception as exc:
        print(f"[{uid}] Warning: could not load reflection history: {exc}")
        return []

    entries = [doc.to_dict() for doc in docs if doc.exists]
    if not entries:
        return []

    current_terms = _tokenize_history_text(transcript)
    current_themes = extract_themes(transcript)
    callback = _has_callback_cue(transcript)
    ranked: list[tuple[int, int, dict]] = []

    for idx, note in enumerate(entries):
        note_terms = _note_history_terms(note)
        note_themes = set(note.get("themes") or [])
        score = max(0, 8 - idx)
        score += len(current_terms & note_terms)
        score += 4 * len(current_themes & note_themes)
        if callback and note_terms:
            score += 3
        if note.get("voice_labels"):
            score += 1
        ranked.append((score, idx, note))

    ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    chosen = [note for score, _, note in ranked if score > 0][:max_items]
    if not chosen:
        chosen = entries[: min(2, len(entries))]
    return chosen


def _history_context_summary(uid: str, transcript: str) -> tuple[list[dict], str]:
    notes = _load_relevant_reflection_history(uid, transcript)
    if not notes:
        return [], ""
    lines = [
        "The teacher may be continuing, revising, or disagreeing with earlier reflection framings."
    ]
    for note in notes:
        lines.append(f"- {_build_history_note_line(note)}")
    if _has_callback_cue(transcript):
        lines.append(
            "The current transcript explicitly seems to reference earlier reflections or prior voice framings."
        )
    return notes, "\n".join(lines)


def _record_source_usage(uid: str, sources_used: list) -> None:
    """Increment guide_usage and passage_usage counters in Firestore."""
    try:
        from google.cloud.firestore_v1 import Increment
        updates = {}
        for s in sources_used:
            sid = s.get("source_id", "")
            # Derive guide_id from source_id prefix (e.g. "ma_4_3" → "marcus_aurelius")
            # We store it directly in sources_used for reliability
            author = s.get("author", "unknown").lower().replace(" ", "_")
            if sid:
                updates[f"passage_usage.{sid}"] = Increment(1)
            updates[f"guide_usage.{author}"] = Increment(1)
        if updates:
            _get_db().collection("users").document(uid).update(updates)
    except Exception as exc:
        print(f"[{uid}] Warning: could not record source usage: {exc}")


def _build_grounded_reflection_script(
    transcript: str,
    ai: dict,
    sources_used: list[dict],
    user_data: dict,
    api_key: str,
) -> str:
    """Turn the grounded note into a spoken reflection script."""
    preferred_name = (user_data.get("preferred_name") or user_data.get("name") or "the teacher").strip()
    spoken_name = (user_data.get("spoken_name") or preferred_name).strip()
    preferred_pronouns = (user_data.get("preferred_pronouns") or "").strip()
    grades = ", ".join(user_data.get("grade_levels") or [])
    subjects = user_data.get("subjects") or ""
    context_bits = []
    if subjects:
        context_bits.append(f"subjects: {subjects}")
    if grades:
        context_bits.append(f"grades: {grades}")
    school_state = user_data.get("school_state") or ""
    if school_state:
        context_bits.append(f"state: {school_state}")
    if preferred_pronouns:
        context_bits.append(f"pronouns: {preferred_pronouns}")
    context_line = "; ".join(context_bits) if context_bits else "teaching context not specified"
    tasks_context_summary = (user_data.get("tasks_context_summary") or "").strip()
    tasks_context_block = (
        f"\nRelevant current obligations:\n{tasks_context_summary}\n"
        if tasks_context_summary else ""
    )

    source_lines = []
    for source in sources_used[:3]:
        excerpt = source.get("excerpt", "").strip()
        because = ""
        for item in ai.get("influenced_by", []):
            if item.get("source_id") == source.get("source_id"):
                because = item.get("because", "").strip()
                break
        detail = f'{source.get("author", "")} ({source.get("ref", "")}): "{excerpt}"'
        if because:
            detail += f"\nConnection: {because}"
        source_lines.append(detail)
    sources_block = "\n\n".join(source_lines) if source_lines else "(no guiding voices selected)"

    payload = json.dumps({
        "title": ai.get("title", ""),
        "summary": ai.get("summary", ""),
        "insight": ai.get("insight", ""),
        "best_practice": ai.get("best_practice", ""),
        "concerns": ai.get("concerns", ""),
        "action_items": ai.get("action_items", []),
    }, ensure_ascii=False)

    prompt = f"""You are writing a spoken grounded reflection for {preferred_name}.

This script will be narrated back to {preferred_name} as audio.
It should feel thoughtful, warm, and concise, like a trusted instructional coach
helping them hear the day more clearly.

Person context: display name: {preferred_name}; spoken name for audio: {spoken_name}; {context_line}

Original transcript:
---
{transcript}
---

Structured reflection:
{payload}
{tasks_context_block}

Guiding voices actually selected for this reflection:
{sources_block}

Return strict JSON only:
{{
  "reflection_script": "A spoken script of 130 to 220 words. No bullet points. No greeting. No sign-off. Weave in the guiding voices naturally when they genuinely fit, but do not quote long passages."
}}

Rules:
- Sound natural when read aloud
- Begin with the lived classroom moment, then deepen it
- Use the guiding voices as grounding, not decoration
- If current obligations are included, use them only when they genuinely clarify what is weighing on {preferred_name}
- End with one clear line they can carry into tomorrow
- Refer to {preferred_name} by name where natural
- Respect these pronouns when needed: {preferred_pronouns or "use neutral phrasing if possible"}
- If the script uses their name, write it in the spoken form "{spoken_name}" so the narrator says it correctly
- Return JSON only
"""

    result = _summarize(prompt, api_key)
    return (result.get("reflection_script") or "").strip()


def _generate_grounded_reflection_audio(script_text: str, stem: str, voice: str) -> bytes:
    """Synthesize the spoken reflection as MP3 bytes."""
    if not script_text.strip():
        raise ValueError("grounded reflection script is empty")
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem).strip("-") or "grounded-reflection"
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / f"{safe_stem}.mp3"
        synthesize_reflection_mp3(script_text, output_path, voice=voice)
        return output_path.read_bytes()


def _generate_reflection_result(
    transcript: str,
    user_data: dict,
    api_key: str,
) -> tuple[str, dict, list[dict]]:
    style = _normalize_reflection_style(user_data.get("reflection_style"))
    lane = user_data.get("lane", "professional")

    if style == "grounded":
        prompt, sources_used = reflect_prompt(transcript, user_data)
        return style, _summarize(prompt, api_key), sources_used

    if style == "practical":
        if lane == "professional" and _is_teacher_profession(user_data):
            prompt = teaching_practical_prompt(transcript, user_data)
            return style, _summarize(prompt, api_key), []
        profession = (user_data.get("profession") or "professional").lower().strip() or "professional"
        prompt = professional_prompt(
            transcript,
            profession,
            (user_data.get("tasks_context_summary") or "").strip(),
            (user_data.get("history_context_summary") or "").strip(),
        )
        return style, _summarize(prompt, api_key), []

    practical_prompt = (
        teaching_practical_prompt(transcript, user_data)
        if lane == "professional" and _is_teacher_profession(user_data)
        else professional_prompt(
            transcript,
            ((user_data.get("profession") or "professional").lower().strip() or "professional"),
            (user_data.get("tasks_context_summary") or "").strip(),
            (user_data.get("history_context_summary") or "").strip(),
        )
    )
    practical_result = _summarize(practical_prompt, api_key)
    grounded_prompt, sources_used = reflect_prompt(transcript, user_data)
    grounded_result = _summarize(grounded_prompt, api_key)
    integration_prompt = _build_complete_reflection_prompt(
        transcript,
        user_data,
        practical_result,
        grounded_result,
        sources_used,
    )
    integrated_result = _summarize(integration_prompt, api_key)
    return style, integrated_result, sources_used


def _process_file(service, uid: str, user_data: dict, f: dict, inbox_id: str, notes_id: str):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    filename = f["name"]
    print(f"[{uid}] Processing: {filename}")

    # Download audio
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=f["id"]),
                             chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
    audio_bytes = buf.getvalue()

    if len(audio_bytes) < 4096:
        print(f"[{uid}] File too small, skipping")
        return

    transcript = _transcribe(audio_bytes, filename, api_key)
    if len(transcript.split()) < 3:
        print(f"[{uid}] Transcript too short, skipping")
        return

    user_data = dict(user_data)
    task_context_items = _derive_task_context(
        transcript,
        _fetch_open_tasks_for_user(uid, user_data),
    )
    user_data["tasks_context_items"] = task_context_items
    user_data["tasks_context_summary"] = "\n".join(f"- {item}" for item in task_context_items)
    history_context_items, history_context_summary = _history_context_summary(uid, transcript)
    user_data["history_context_items"] = history_context_items
    user_data["history_context_summary"] = history_context_summary

    lane = user_data.get("lane", "professional")
    try:
        style_key, ai_result, sources_used = _generate_reflection_result(transcript, user_data, api_key)
    except Exception as exc:
        print(f"[{uid}] AI error: {exc} — using fallback")
        style_key = _normalize_reflection_style(user_data.get("reflection_style"))
        ai_result = {"title": Path(filename).stem, "summary": transcript[:300],
                     "action_items": [], "suggested_tags": [], "influenced_by": []}
        sources_used = []

    note_md = _render_note(lane, ai_result, transcript, filename, sources_used)
    note_name = (datetime.now().strftime("%Y-%m-%d") + " — " +
                 ai_result.get("title", Path(filename).stem)[:60] + ".md")

    media = MediaInMemoryUpload(note_md.encode(), mimetype="text/plain", resumable=False)
    service.files().create(
        body={"name": note_name, "parents": [notes_id]},
        media_body=media, fields="id",
    ).execute()
    print(f"[{uid}] Note saved: {note_name}")

    # Track guide and passage usage counts in Firestore
    if sources_used:
        _record_source_usage(uid, sources_used)

    # Store recent note metadata in Firestore for dashboard display
    _store_note_metadata(uid, ai_result, sources_used, note_name, transcript, style_key)

    processed_id = _find_or_create_folder(service, "processed", inbox_id)
    service.files().update(
        fileId=f["id"],
        addParents=processed_id, removeParents=inbox_id, fields="id",
    ).execute()


def _sweep_user(uid: str, user_data: dict):
    """
    Ensure the user's Drive folders exist.

    With drive.file scope, we can only access files this app created.
    Audio processing now happens exclusively via the /upload endpoint
    (browser recording or PWA share target), not by polling Drive.
    """
    creds = _drive_creds(uid)
    if not creds:
        return

    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    inbox_id = user_data.get("inbox_folder_id")
    notes_id = user_data.get("notes_folder_id")
    updates = {}
    if not inbox_id:
        inbox_id = _find_or_create_folder(service, "memnon-inbox")
        updates["inbox_folder_id"] = inbox_id
    if not notes_id:
        notes_id = _find_or_create_folder(service, "memnon-notes")
        updates["notes_folder_id"] = notes_id
    if updates:
        _get_db().collection("users").document(uid).update(updates)
        print(f"[{uid}] Drive folders ensured: inbox={inbox_id} notes={notes_id}")


# ── Flask routes ───────────────────────────────────────────────────────────────

def _client_config() -> dict:
    """Return the parsed client secrets dict."""
    raw = os.environ.get("GOOGLE_CLIENT_SECRETS", "")
    if not raw:
        raise RuntimeError("GOOGLE_CLIENT_SECRETS env var not set")
    return json.loads(raw if raw.strip().startswith("{") else Path(raw).read_text())


@flask_app.route("/auth/start")
def auth_start():
    """Redirect user to Google — requests profile + Drive in one consent screen."""
    cfg = _client_config()["web"]
    # Generate PKCE code verifier + challenge
    code_verifier  = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)

    # Store both in Flask session for callback verification
    from flask import session
    session["frontend_return_to"] = _safe_frontend_return_url(
        request.args.get("return_to")
    )
    session["oauth_state"]    = state
    session["code_verifier"]  = code_verifier

    include_tasks = request.args.get("include_tasks") == "1"
    params = {
        "client_id":              cfg["client_id"],
        "redirect_uri":           REDIRECT_URI,
        "response_type":          "code",
        "scope":                  " ".join(_requested_google_scopes(include_tasks)),
        "access_type":            "offline",
        "state":                  state,
        "code_challenge":         code_challenge,
        "code_challenge_method":  "S256",
    }
    if request.args.get("force_consent") == "1":
        params["prompt"] = "consent"
    params = urllib.parse.urlencode(params)
    return redirect(f"https://accounts.google.com/o/oauth2/auth?{params}")


@flask_app.route("/auth/callback")
def auth_callback():
    """Receive tokens, create/update Firebase user, mint custom token, redirect."""
    from flask import session
    frontend_return_to = _safe_frontend_return_url(session.get("frontend_return_to"))
    try:
        code  = request.args.get("code")
        state = request.args.get("state")

        if not code:
            return redirect(_append_query_params(frontend_return_to, {"error": "missing_code"}))

        # Verify state to prevent CSRF
        if not state or state != session.get("oauth_state"):
            return redirect(_append_query_params(frontend_return_to, {"error": "invalid_state"}))

        code_verifier = session.pop("code_verifier", None)
        session.pop("oauth_state", None)
        session.pop("frontend_return_to", None)

        # Exchange code for tokens with PKCE verifier
        cfg = _client_config()["web"]
        import requests as http_requests
        token_payload = {
            "code":          code,
            "client_id":     cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        }
        if code_verifier:
            token_payload["code_verifier"] = code_verifier

        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data=token_payload,
        ).json()

        if "error" in token_resp:
            raise RuntimeError(token_resp["error_description"])

        creds = Credentials(
            token=token_resp.get("access_token"),
            refresh_token=token_resp.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
            scopes=token_resp.get("scope", "").split(),
        )

        # Ensure Firebase Admin is initialized before any fb_auth calls
        _get_db()

        # Get user profile from Google
        people = build("oauth2", "v2", credentials=creds)
        info = people.userinfo().get().execute()
        email = info["email"]
        name = info.get("name", "")
        google_id = info["id"]

        # Create or fetch Firebase user
        try:
            fb_user = fb_auth.get_user_by_email(email)
        except fb_auth.UserNotFoundError:
            fb_user = fb_auth.create_user(
                email=email,
                display_name=name,
                uid=f"google_{google_id}",
            )

        uid = fb_user.uid

        existing_doc = _get_db().collection("users").document(uid).get()
        existing_user = existing_doc.to_dict() if existing_doc.exists else {}
        merged_token = _merge_google_token(
            existing_user.get("google_drive_token"),
            json.loads(creds.to_json()),
        )
        tasks_connected = TASKS_READONLY_SCOPE in _scopes_from_token_data(merged_token)

        # Persist Drive tokens + user info
        _get_db().collection("users").document(uid).set({
            "email": email,
            "name": name,
            "google_drive_token": merged_token,
            "drive_connected": True,
            "google_tasks_connected": tasks_connected,
            "active": True,
        }, merge=True)

        # Mint a short-lived Firebase custom token for the frontend
        custom_token = fb_auth.create_custom_token(uid).decode("utf-8")

    except Exception as exc:
        print(f"OAuth callback error: {exc}")
        return redirect(_append_query_params(frontend_return_to, {"error": "oauth_failed"}))

    return redirect(_append_query_params(frontend_return_to, {"token": custom_token}))


@flask_app.route("/setup", methods=["POST"])
def save_setup():
    """Save lane + profession/tradition for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    _get_db().collection("users").document(uid).set({
        "lane":           data.get("lane", "professional"),
        "profession":     data.get("profession", "teacher"),
        "reflection_style": _normalize_reflection_style(data.get("reflection_style")),
        "preferred_name": data.get("preferred_name", ""),
        "spoken_name": data.get("spoken_name", ""),
        "preferred_pronouns": data.get("preferred_pronouns", ""),
        "narration_voice": _normalize_narration_voice(data.get("narration_voice")),
        "tradition":      data.get("tradition", "secular"),
        # Teaching-specific fields
        "grade_levels":    data.get("grade_levels", []),
        "subjects":        data.get("subjects", ""),
        "school_state":    data.get("school_state", ""),
        "state_standards": data.get("state_standards", []),
        "school_name":     data.get("school_name", ""),
        "school_district": data.get("school_district", ""),
        "school_city":     data.get("school_city", ""),
        # Reflect lane voices config
        "reflect_config":  data.get("reflect_config", {}),
        "dashboard_image": data.get("dashboard_image", {"kind": "preset", "preset": "lattice"}),
        "google_tasks_list_id": (data.get("google_tasks_list_id") or "").strip(),
        "google_tasks_list_name": (data.get("google_tasks_list_name") or "").strip(),
        "active": True,
    }, merge=True)
    return jsonify({"ok": True})


@flask_app.route("/voice-preview", methods=["POST"])
def voice_preview():
    """Generate a short preview clip for a selected narration voice."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    voice = _normalize_narration_voice(data.get("voice"))
    text = (data.get("text") or VOICE_PREVIEW_TEXT).strip()
    if not text:
        text = VOICE_PREVIEW_TEXT

    try:
        audio_bytes = synthesize_reflection_bytes(text, voice=voice)
    except Exception as exc:
        print(f"[{uid}] Voice preview failed: {exc}")
        return jsonify({"error": "preview unavailable"}), 502

    return (
        audio_bytes,
        200,
        {
            "Content-Type": "audio/mpeg",
            "Cache-Control": "private, max-age=60",
        },
    )


@flask_app.route("/tasklists")
def list_tasklists():
    """Return available Google Tasks lists for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"error": "user not found"}), 404
    user_data = doc.to_dict()
    if not _user_tasks_connected(user_data):
        return jsonify({"error": "Tasks not connected", "needs_consent": True}), 403

    service = _tasks_service_for_user(uid)
    if not service:
        return jsonify({"error": "Google account not connected"}), 403

    try:
        result = service.tasklists().list(maxResults=100).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list task lists: {exc}")
        return jsonify({"error": "Could not load task lists"}), 502

    items = [
        {"id": item.get("id", ""), "title": item.get("title", "Untitled")}
        for item in result.get("items", []) if item.get("id")
    ]
    return jsonify({"items": items})


@flask_app.route("/tasks")
def list_tasks():
    """Return open tasks from the user's selected Google Tasks list."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"error": "user not found"}), 404
    user_data = doc.to_dict()
    if not _user_tasks_connected(user_data):
        return jsonify({"error": "Tasks not connected", "needs_consent": True}), 403

    tasklist_id = (user_data.get("google_tasks_list_id") or "").strip()
    if not tasklist_id:
        return jsonify({"items": [], "tasklist_configured": False, "tasks_web_url": GOOGLE_TASKS_WEB_URL})

    service = _tasks_service_for_user(uid)
    if not service:
        return jsonify({"error": "Google account not connected"}), 403

    max_results = request.args.get("limit", "5")
    try:
        limit_value = max(1, min(int(max_results), 20))
    except ValueError:
        limit_value = 5

    try:
        result = service.tasks().list(
            tasklist=tasklist_id,
            maxResults=limit_value,
            showCompleted=False,
            showHidden=False,
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list tasks: {exc}")
        return jsonify({"error": "Could not load tasks"}), 502

    items = []
    for item in result.get("items", []):
        if item.get("status") == "completed":
            continue
        items.append({
            "id": item.get("id", ""),
            "title": item.get("title", "").strip(),
            "due": item.get("due"),
            "notes": item.get("notes", "").strip(),
            "status": item.get("status", ""),
            "updated": item.get("updated"),
        })

    return jsonify({
        "items": items,
        "tasklist_configured": True,
        "tasklist_name": user_data.get("google_tasks_list_name", ""),
        "tasks_web_url": GOOGLE_TASKS_WEB_URL,
    })


@flask_app.route("/profile-images")
def list_profile_images():
    """Return recent Drive images accessible to the app for this user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    try:
        result = service.files().list(
            q="mimeType contains 'image/' and trashed=false",
            pageSize=24,
            orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,parents)",
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list profile images: {exc}")
        return jsonify({"error": "Could not load Drive images"}), 502

    return jsonify({
        "files": result.get("files", []),
    })


@flask_app.route("/profile-image/upload", methods=["POST"])
def upload_profile_image():
    """Upload a custom dashboard image to Drive and return its file metadata."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    if "image" not in request.files:
        return jsonify({"error": "missing image"}), 400

    f = request.files["image"]
    image_bytes = f.read()
    mime_type = (f.mimetype or "").strip().lower()
    filename = f.filename or f"dashboard-image-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"

    if not mime_type.startswith(IMAGE_MIME_PREFIX):
        return jsonify({"error": "unsupported image type"}), 400
    if not image_bytes:
        return jsonify({"error": "empty image"}), 400
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "image too large"}), 400

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    folder_id = _find_or_create_media_folder(service)
    media = MediaInMemoryUpload(image_bytes, mimetype=mime_type, resumable=False)
    meta = {
        "name": filename,
        "parents": [folder_id],
    }
    try:
        created = service.files().create(
            body=meta,
            media_body=media,
            fields="id,name,mimeType,modifiedTime",
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not upload profile image: {exc}")
        return jsonify({"error": "image upload failed"}), 502

    return jsonify({"file": created})


@flask_app.route("/profile-image/<file_id>")
def get_profile_image(file_id: str):
    """Stream a Drive-backed dashboard image for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    try:
        meta = service.files().get(fileId=file_id, fields="id,name,mimeType").execute()
        mime_type = meta.get("mimeType", "")
        if not mime_type.startswith(IMAGE_MIME_PREFIX):
            return jsonify({"error": "not an image"}), 400

        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id), chunksize=4 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
    except Exception as exc:
        print(f"[{uid}] Could not fetch profile image {file_id}: {exc}")
        return jsonify({"error": "image unavailable"}), 404

    return (
        buf.getvalue(),
        200,
        {
            "Content-Type": mime_type,
            "Cache-Control": "private, max-age=300",
        },
    )


@flask_app.route("/recordings")
def list_recordings():
    """Return recent recordings saved by the app for this user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"files": []})
    user_data = doc.to_dict()

    recordings_id = user_data.get("recordings_folder_id")
    if not recordings_id:
        return jsonify({"files": []})

    try:
        result = service.files().list(
            q=f"'{recordings_id}' in parents and trashed=false",
            pageSize=8,
            orderBy="createdTime desc",
            fields="files(id,name,mimeType,createdTime,modifiedTime,size)",
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list recordings: {exc}")
        return jsonify({"error": "Could not load recordings"}), 502

    files = [f for f in result.get("files", []) if _is_audio(f)]
    return jsonify({"files": files})


@flask_app.route("/recording/<file_id>")
def get_recording(file_id: str):
    """Stream a Drive-backed recording for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    try:
        meta = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,parents",
        ).execute()
        if not _is_audio(meta):
            return jsonify({"error": "not a recording"}), 400

        buf = io.BytesIO()
        dl = MediaIoBaseDownload(
            buf,
            service.files().get_media(fileId=file_id),
            chunksize=4 * 1024 * 1024,
        )
        done = False
        while not done:
            _, done = dl.next_chunk()
    except Exception as exc:
        print(f"[{uid}] Could not fetch recording {file_id}: {exc}")
        return jsonify({"error": "recording unavailable"}), 404

    return (
        buf.getvalue(),
        200,
        {
            "Content-Type": meta.get("mimeType", "audio/webm"),
            "Cache-Control": "private, max-age=300",
        },
    )


@flask_app.route("/reflections")
def list_reflections():
    """Return recent grounded reflection audio files saved by the app for this user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"files": []})
    user_data = doc.to_dict()

    reflections_id = user_data.get("reflections_folder_id")
    if not reflections_id:
        return jsonify({"files": []})

    try:
        result = service.files().list(
            q=f"'{reflections_id}' in parents and trashed=false",
            pageSize=8,
            orderBy="createdTime desc",
            fields="files(id,name,mimeType,createdTime,modifiedTime,size)",
        ).execute()
    except Exception as exc:
        print(f"[{uid}] Could not list reflections: {exc}")
        return jsonify({"error": "Could not load reflections"}), 502

    files = [f for f in result.get("files", []) if _is_audio(f)]
    return jsonify({"files": files})


@flask_app.route("/reflection/<file_id>")
def get_reflection(file_id: str):
    """Stream a Drive-backed grounded reflection audio file for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    service = _drive_service_for_user(uid)
    if not service:
        return jsonify({"error": "Drive not connected"}), 403

    try:
        meta = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,parents",
        ).execute()
        if not _is_audio(meta):
            return jsonify({"error": "not a reflection"}), 400

        buf = io.BytesIO()
        dl = MediaIoBaseDownload(
            buf,
            service.files().get_media(fileId=file_id),
            chunksize=4 * 1024 * 1024,
        )
        done = False
        while not done:
            _, done = dl.next_chunk()
    except Exception as exc:
        print(f"[{uid}] Could not fetch reflection {file_id}: {exc}")
        return jsonify({"error": "reflection unavailable"}), 404

    return (
        buf.getvalue(),
        200,
        {
            "Content-Type": meta.get("mimeType", "audio/mpeg"),
            "Cache-Control": "private, max-age=300",
        },
    )


@flask_app.route("/upload", methods=["POST"])
def upload_audio():
    """Accept a direct audio upload, run it through the pipeline, save note to Drive."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    # Accept either multipart file or raw bytes
    if "file" in request.files:
        f = request.files["file"]
        audio_bytes = f.read()
        filename = f.filename or f"upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}.webm"
        upload_mime_type = f.mimetype or "audio/webm"
    else:
        audio_bytes = request.get_data()
        filename = request.headers.get("X-Filename",
                   f"upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}.webm")
        upload_mime_type = request.headers.get("Content-Type", "audio/webm")

    if len(audio_bytes) < 4096:
        return jsonify({"error": "audio too short"}), 400

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "server misconfigured"}), 500

    # Get user data + Drive credentials
    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({"error": "user not found"}), 404
    user_data = doc.to_dict()
    user_data = dict(user_data)

    creds = _drive_creds(uid)
    if not creds:
        return jsonify({"error": "Drive not connected"}), 403

    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    # Ensure notes folder exists
    notes_id = user_data.get("notes_folder_id")
    if not notes_id:
        notes_id = _find_or_create_folder(service, "memnon-notes")
        _get_db().collection("users").document(uid).update({"notes_folder_id": notes_id})

    recordings_id = user_data.get("recordings_folder_id")
    if not recordings_id:
        recordings_id = _find_or_create_recordings_folder(service)
        _get_db().collection("users").document(uid).update({"recordings_folder_id": recordings_id})
    reflections_id = user_data.get("reflections_folder_id")
    if not reflections_id:
        reflections_id = _find_or_create_reflections_folder(service)
        _get_db().collection("users").document(uid).update({"reflections_folder_id": reflections_id})

    # Run pipeline
    try:
        transcript = _transcribe(audio_bytes, filename, api_key)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return jsonify({"error": "transcription service authentication failed"}), 502
        return jsonify({"error": f"transcription failed ({exc.code})"}), 502
    except Exception as exc:
        print(f"[{uid}] Transcription error on upload: {exc}")
        return jsonify({"error": "transcription failed"}), 502

    if len(transcript.split()) < 3:
        return jsonify({"error": "transcript too short"}), 400

    task_context_items = _derive_task_context(
        transcript,
        _fetch_open_tasks_for_user(uid, user_data),
    )
    user_data["tasks_context_items"] = task_context_items
    user_data["tasks_context_summary"] = "\n".join(f"- {item}" for item in task_context_items)
    history_context_items, history_context_summary = _history_context_summary(uid, transcript)
    user_data["history_context_items"] = history_context_items
    user_data["history_context_summary"] = history_context_summary

    lane = user_data.get("lane", "professional")
    try:
        style_key, ai_result, sources_used = _generate_reflection_result(transcript, user_data, api_key)
    except Exception as exc:
        print(f"[{uid}] AI error on upload: {exc}")
        style_key = _normalize_reflection_style(user_data.get("reflection_style"))
        ai_result = {"title": Path(filename).stem, "summary": transcript[:300],
                     "action_items": [], "suggested_tags": [], "influenced_by": []}
        sources_used = []

    note_name = (datetime.now().strftime("%Y-%m-%d") + " — " +
                 ai_result.get("title", Path(filename).stem)[:60] + ".md")
    note_md = _render_note(lane, ai_result, transcript, filename, sources_used)
    if sources_used:
        _record_source_usage(uid, sources_used)
    _store_note_metadata(uid, ai_result, sources_used, note_name, transcript, style_key)

    media = MediaInMemoryUpload(note_md.encode(), mimetype="text/plain", resumable=False)
    service.files().create(
        body={"name": note_name, "parents": [notes_id]},
        media_body=media, fields="id",
    ).execute()

    audio_ext = Path(filename).suffix or ".webm"
    recording_name = f"{datetime.now().strftime('%Y-%m-%d %H-%M-%S')} — recording{audio_ext}"
    audio_media = MediaInMemoryUpload(audio_bytes, mimetype=upload_mime_type, resumable=False)
    service.files().create(
        body={"name": recording_name, "parents": [recordings_id]},
        media_body=audio_media,
        fields="id,name",
    ).execute()

    reflection_name = None
    try:
        narration_voice = _normalize_narration_voice(user_data.get("narration_voice"))
        reflection_script = _build_grounded_reflection_script(
            transcript,
            ai_result,
            sources_used,
            user_data,
            api_key,
        )
        if reflection_script:
            reflection_bytes = _generate_grounded_reflection_audio(
                reflection_script,
                ai_result.get("title", Path(filename).stem),
                narration_voice,
            )
            reflection_title = ai_result.get("title", Path(filename).stem)[:60].strip() or "Grounded Reflection"
            reflection_name = f"{datetime.now().strftime('%Y-%m-%d')} — {reflection_title}.mp3"
            reflection_media = MediaInMemoryUpload(
                reflection_bytes,
                mimetype="audio/mpeg",
                resumable=False,
            )
            service.files().create(
                body={"name": reflection_name, "parents": [reflections_id]},
                media_body=reflection_media,
                fields="id,name",
            ).execute()
    except Exception as exc:
        print(f"[{uid}] Grounded reflection audio failed: {exc}")

    print(f"[{uid}] Direct upload note saved: {note_name}")
    return jsonify({"ok": True, "note": note_name, "reflection_audio": reflection_name})


@flask_app.route("/me")
def get_me():
    """Return user config (no tokens) for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    doc = _get_db().collection("users").document(uid).get()
    if not doc.exists:
        return jsonify({}), 404
    data = doc.to_dict()
    data.pop("google_drive_token", None)   # never send tokens to frontend
    return jsonify(data)


# ── Cloud Functions ────────────────────────────────────────────────────────────

@https_fn.on_request(
    region="us-central1",
    memory=options.MemoryOption.MB_512,
    timeout_sec=60,
    secrets=["OPENAI_API_KEY", "GOOGLE_CLIENT_SECRETS", "FLASK_SECRET"],
)
def api(req: https_fn.Request) -> https_fn.Response:
    with flask_app.request_context(req.environ):
        return flask_app.full_dispatch_request()


@scheduler_fn.on_schedule(
    schedule="every 1 minutes",
    region="us-central1",
    memory=options.MemoryOption.MB_512,
    timeout_sec=540,
    secrets=["OPENAI_API_KEY", "GOOGLE_CLIENT_SECRETS"],
)
def worker(event: scheduler_fn.ScheduledEvent) -> None:
    users = _get_db().collection("users").where("active", "==", True).stream()
    for doc in users:
        uid = doc.id
        user_data = doc.to_dict()
        if user_data.get("drive_connected"):
            try:
                _sweep_user(uid, user_data)
            except Exception as exc:
                print(f"Sweep error [{uid}]: {exc}")
