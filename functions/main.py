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

import hashlib
import io
import json
import os
import re
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

from lanes import build_prompt

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
    "https://memnon-app.web.app",
    "https://memnon-app.firebaseapp.com",
    "http://localhost:5000",
])

# ── constants ─────────────────────────────────────────────────────────────────

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
API_BASE = "https://api-4hth6oktaa-uc.a.run.app"
REDIRECT_URI = f"{API_BASE}/auth/callback"
FRONTEND_URL = "https://memnon-app.web.app"

AUDIO_MIME_TYPES = {
    "audio/mp4", "audio/x-m4a", "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/aac", "audio/flac",
    "audio/webm", "audio/ogg", "video/mp4",
}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".webm", ".ogg"}

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
        scopes=token_data.get("scopes", DRIVE_SCOPES),
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


def _is_audio(f: dict) -> bool:
    return (f.get("mimeType") in AUDIO_MIME_TYPES or
            any(f.get("name", "").lower().endswith(ext) for ext in AUDIO_EXTENSIONS))


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
---

## Summary

{summary}

{extra}## Transcript

{transcript}
"""


def _render_note(lane: str, ai: dict, transcript: str, filename: str) -> str:
    extra = []
    if ai.get("insight"):
        extra += ["## Insight", "", ai["insight"], ""]
    if ai.get("best_practice"):
        extra += ["## Best Practice", "", ai["best_practice"], ""]
    if ai.get("action_items"):
        extra += ["## Action Items", ""] + [f"- {i}" for i in ai["action_items"]] + [""]
    return NOTE_TEMPLATE.format(
        title=ai.get("title", Path(filename).stem),
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        lane=lane,
        tags=", ".join(ai.get("suggested_tags", [])),
        summary=ai.get("summary", ""),
        extra="\n".join(extra) + ("\n" if extra else ""),
        transcript=transcript,
    )


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

    lane = user_data.get("lane", "professional")
    prompt = build_prompt(lane, transcript, user_data)
    try:
        ai_result = _summarize(prompt, api_key)
    except Exception as exc:
        print(f"[{uid}] AI error: {exc} — using fallback")
        ai_result = {"title": Path(filename).stem, "summary": transcript[:300],
                     "action_items": [], "suggested_tags": []}

    note_md = _render_note(lane, ai_result, transcript, filename)
    note_name = (datetime.now().strftime("%Y-%m-%d") + " — " +
                 ai_result.get("title", Path(filename).stem)[:60] + ".md")

    media = MediaInMemoryUpload(note_md.encode(), mimetype="text/plain", resumable=False)
    service.files().create(
        body={"name": note_name, "parents": [notes_id]},
        media_body=media, fields="id",
    ).execute()
    print(f"[{uid}] Note saved: {note_name}")

    processed_id = _find_or_create_folder(service, "processed", inbox_id)
    service.files().update(
        fileId=f["id"],
        addParents=processed_id, removeParents=inbox_id, fields="id",
    ).execute()


def _sweep_user(uid: str, user_data: dict):
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

    files = service.files().list(
        q=f"'{inbox_id}' in parents and trashed=false",
        fields="files(id, name, mimeType, size)", pageSize=50,
    ).execute().get("files", [])

    for f in files:
        if _is_audio(f):
            try:
                _process_file(service, uid, user_data, f, inbox_id, notes_id)
            except Exception as exc:
                print(f"[{uid}] Error on {f.get('name')}: {exc}")


# ── Flask routes ───────────────────────────────────────────────────────────────

@flask_app.route("/auth/drive")
def auth_drive():
    """Step 1: redirect user to Google OAuth to grant Drive access."""
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "uid required"}), 400
    flow = Flow.from_client_secrets_file(
        _client_secrets_path(), scopes=DRIVE_SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", state=uid,
    )
    return redirect(auth_url)


@flask_app.route("/auth/callback")
def auth_callback():
    """Step 2: receive tokens, store in Firestore, redirect to setup page."""
    uid = request.args.get("state")
    if not uid:
        return redirect(f"{FRONTEND_URL}/?error=missing_state")
    try:
        flow = Flow.from_client_secrets_file(
            _client_secrets_path(), scopes=DRIVE_SCOPES, redirect_uri=REDIRECT_URI
        )
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        _get_db().collection("users").document(uid).set(
            {"google_drive_token": json.loads(creds.to_json()), "drive_connected": True},
            merge=True,
        )
    except Exception as exc:
        print(f"OAuth callback error: {exc}")
        return redirect(f"{FRONTEND_URL}/?error=oauth_failed")
    return redirect(f"{FRONTEND_URL}/setup?drive_connected=true")


@flask_app.route("/setup", methods=["POST"])
def save_setup():
    """Save lane + profession/tradition for the signed-in user."""
    uid = _verify_firebase_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    _get_db().collection("users").document(uid).set({
        "lane": data.get("lane", "professional"),
        "profession": data.get("profession", ""),
        "tradition": data.get("tradition", "secular"),
        "active": True,
    }, merge=True)
    return jsonify({"ok": True})


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
