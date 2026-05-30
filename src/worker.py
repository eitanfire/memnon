"""
Memnon SaaS — background pipeline worker.

Runs in a daemon thread inside the Flask app. Every POLL_INTERVAL seconds,
iterates over all active users, checks their Drive inbox folder for new
audio files, processes each one through the pipeline, and writes the
resulting note back to their Drive notes folder.

All pipeline logic (transcription, AI summarization, note rendering) is
self-contained here so the worker can run without importing voice_pipeline
as a subprocess.
"""

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload

import db
from lanes import build_prompt, LANES

log = logging.getLogger("memnon.worker")

POLL_INTERVAL = 60  # seconds between full sweeps
SCOPES = ["https://www.googleapis.com/auth/drive"]
AUDIO_MIME_TYPES = {
    "audio/mp4", "audio/x-m4a", "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/aac", "audio/flac",
    "audio/webm", "audio/ogg", "video/mp4",
}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".webm", ".ogg"}

# ── credentials ───────────────────────────────────────────────────────────────

def _creds_for_user(user: dict) -> Credentials | None:
    raw = user.get("google_token")
    if not raw:
        return None
    token_data = json.loads(raw) if isinstance(raw, str) else raw
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", SCOPES),
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            db.save_token(user["email"], json.loads(creds.to_json()))
        except Exception as exc:
            log.warning("Token refresh failed for %s: %s", user["email"], exc)
            return None
    return creds


def _drive(user: dict):
    creds = _creds_for_user(user)
    if not creds:
        return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ── Drive helpers ─────────────────────────────────────────────────────────────

def _find_or_create_folder(service, name: str, parent_id: str | None = None) -> str:
    parent_clause = f"and '{parent_id}' in parents" if parent_id else ""
    q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
         f"{parent_clause} and trashed=false")
    results = service.files().list(q=q, fields="files(id)", pageSize=1).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return service.files().create(body=meta, fields="id").execute()["id"]


def _is_audio(f: dict) -> bool:
    return (f.get("mimeType") in AUDIO_MIME_TYPES or
            any(f.get("name", "").lower().endswith(ext) for ext in AUDIO_EXTENSIONS))


def _ensure_user_folders(service, user: dict) -> tuple[str, str]:
    """Return (inbox_folder_id, notes_folder_id), creating them if needed."""
    inbox_id = user.get("inbox_folder_id")
    notes_id = user.get("notes_folder_id")
    changed = {}

    if not inbox_id:
        inbox_id = _find_or_create_folder(service, "memnon-inbox")
        changed["inbox_folder_id"] = inbox_id

    if not notes_id:
        notes_id = _find_or_create_folder(service, "memnon-notes")
        changed["notes_folder_id"] = notes_id

    if changed:
        db.update_user(user["email"], **changed)

    return inbox_id, notes_id


# ── transcription ─────────────────────────────────────────────────────────────

def _transcribe(audio_bytes: bytes, filename: str, api_key: str) -> str:
    boundary = "MemnonWhisper" + hashlib.md5(audio_bytes[:64]).hexdigest()
    ext = Path(filename).suffix.lower() or ".m4a"
    mime = "audio/mp4" if ext in (".m4a", ".mp4") else f"audio/{ext.lstrip('.')}"

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"whisper-1\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"en\r\n"
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
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["text"].strip()


# ── AI summarization ──────────────────────────────────────────────────────────

def _summarize(prompt: str, api_key: str, model: str = "gpt-4o-mini") -> dict:
    payload = json.dumps({
        "model": model,
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
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = json.loads(resp.read())["choices"][0]["message"]["content"].strip()
    # strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ── note rendering ────────────────────────────────────────────────────────────

NOTE_TEMPLATE = """---
title: {title}
date: {date}
lane: {lane}
tags: [{tags}]
---

## Summary

{summary}

{extra_block}
## Transcript

{transcript}
"""


def _render_note(lane: str, ai: dict, transcript: str, filename: str) -> str:
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    tags = ", ".join(ai.get("suggested_tags", []))
    summary = ai.get("summary", "")

    extra_lines = []
    if ai.get("insight"):
        extra_lines += ["## Insight", "", ai["insight"], ""]
    if ai.get("best_practice"):
        extra_lines += ["## Best Practice", "", ai["best_practice"], ""]
    action_items = ai.get("action_items", [])
    if action_items:
        extra_lines += ["## Action Items", ""]
        extra_lines += [f"- {item}" for item in action_items]
        extra_lines.append("")

    return NOTE_TEMPLATE.format(
        title=ai.get("title", Path(filename).stem),
        date=date,
        lane=lane,
        tags=tags,
        summary=summary,
        extra_block="\n".join(extra_lines),
        transcript=transcript,
    )


# ── per-file processing ───────────────────────────────────────────────────────

def _process_file(service, user: dict, f: dict, inbox_id: str, notes_id: str):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        log.error("OPENAI_API_KEY not set — cannot process")
        return

    filename = f["name"]
    log.info("[%s] Processing %s", user["email"], filename)

    # Download audio from Drive
    request = service.files().get_media(fileId=f["id"])
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request, chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    audio_bytes = buf.getvalue()

    if len(audio_bytes) < 4096:
        log.warning("[%s] File too small, skipping: %s", user["email"], filename)
        return

    # Transcribe
    try:
        transcript = _transcribe(audio_bytes, filename, api_key)
    except Exception as exc:
        log.error("[%s] Transcription failed for %s: %s", user["email"], filename, exc)
        return

    if len(transcript.split()) < 3:
        log.warning("[%s] Transcript too short, skipping", user["email"])
        return

    log.info("[%s] Transcript: %s words", user["email"], len(transcript.split()))

    # AI summarization
    lane = user.get("lane", "professional")
    prompt = build_prompt(lane, transcript, user)
    try:
        ai_result = _summarize(prompt, api_key)
    except Exception as exc:
        log.error("[%s] AI summarization failed: %s", user["email"], exc)
        ai_result = {"title": Path(filename).stem, "summary": transcript[:200],
                     "action_items": [], "suggested_tags": []}

    # Render note
    note_md = _render_note(lane, ai_result, transcript, filename)

    # Upload note to Drive notes folder
    note_name = (
        datetime.now().strftime("%Y-%m-%d")
        + " — "
        + ai_result.get("title", Path(filename).stem)[:60]
        + ".md"
    )
    media = MediaInMemoryUpload(note_md.encode(), mimetype="text/plain", resumable=False)
    service.files().create(
        body={"name": note_name, "parents": [notes_id]},
        media_body=media,
        fields="id",
    ).execute()
    log.info("[%s] Note saved: %s", user["email"], note_name)

    # Move original audio to processed/ subfolder
    processed_id = _find_or_create_folder(service, "processed", inbox_id)
    service.files().update(
        fileId=f["id"],
        addParents=processed_id,
        removeParents=inbox_id,
        fields="id",
    ).execute()


# ── user sweep ────────────────────────────────────────────────────────────────

def _sweep_user(user: dict):
    service = _drive(user)
    if not service:
        return

    try:
        inbox_id, notes_id = _ensure_user_folders(service, user)
    except Exception as exc:
        log.error("[%s] Folder setup failed: %s", user["email"], exc)
        return

    results = service.files().list(
        q=f"'{inbox_id}' in parents and trashed=false",
        fields="files(id, name, mimeType, size)",
        pageSize=50,
    ).execute()

    audio_files = [f for f in results.get("files", []) if _is_audio(f)]
    for f in audio_files:
        try:
            _process_file(service, user, f, inbox_id, notes_id)
        except Exception as exc:
            log.error("[%s] Error processing %s: %s", user["email"], f.get("name"), exc)


# ── main loop ─────────────────────────────────────────────────────────────────

def run_forever():
    log.info("Worker started — polling every %ds", POLL_INTERVAL)
    while True:
        users = db.all_active_users()
        log.debug("Sweeping %d active user(s)", len(users))
        for user in users:
            if user.get("inbox_folder_id") or user.get("google_token"):
                try:
                    _sweep_user(user)
                except Exception as exc:
                    log.error("[%s] Sweep error: %s", user.get("email"), exc)
        time.sleep(POLL_INTERVAL)


def start_background():
    t = threading.Thread(target=run_forever, daemon=True, name="memnon-worker")
    t.start()
    return t
