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
    "https://memnon.app",
    "https://memnon-app.web.app",
    "https://memnon-app.firebaseapp.com",
    "http://localhost:5000",
    "http://localhost:5050",
    "http://localhost:8000",
    "http://localhost:8080",
])

# ── constants ─────────────────────────────────────────────────────────────────

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",  # access only files created by this app
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]
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


def _drive_service_for_user(uid: str):
    creds = _drive_creds(uid)
    if not creds:
        return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _find_or_create_media_folder(service) -> str:
    return _find_or_create_folder(service, "memnon-media")


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


def _store_note_metadata(uid: str, ai: dict, sources_used: list, note_name: str) -> None:
    """Store recent note metadata in Firestore for dashboard display (keep last 10)."""
    try:
        from google.cloud.firestore_v1 import ArrayUnion
        note_meta = {
            "title":        ai.get("title", note_name),
            "summary":      ai.get("summary", "")[:300],
            "date":         datetime.now().strftime("%Y-%m-%d"),
            "note_name":    note_name,
            "influenced_by": sources_used or [],
        }
        # Use a subcollection for notes — one doc per note
        _get_db().collection("users").document(uid)\
                 .collection("notes").add(note_meta)
    except Exception as exc:
        print(f"[{uid}] Warning: could not store note metadata: {exc}")


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
    prompt, sources_used = build_prompt(lane, transcript, user_data)
    try:
        ai_result = _summarize(prompt, api_key)
    except Exception as exc:
        print(f"[{uid}] AI error: {exc} — using fallback")
        ai_result = {"title": Path(filename).stem, "summary": transcript[:300],
                     "action_items": [], "suggested_tags": [], "influenced_by": []}

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
    _store_note_metadata(uid, ai_result, sources_used, note_name)

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

    params = urllib.parse.urlencode({
        "client_id":              cfg["client_id"],
        "redirect_uri":           REDIRECT_URI,
        "response_type":          "code",
        "scope":                  " ".join(DRIVE_SCOPES),
        "access_type":            "offline",
        "prompt":                 "consent",
        "state":                  state,
        "code_challenge":         code_challenge,
        "code_challenge_method":  "S256",
    })
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

        # Persist Drive tokens + user info
        _get_db().collection("users").document(uid).set({
            "email": email,
            "name": name,
            "google_drive_token": json.loads(creds.to_json()),
            "drive_connected": True,
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
        "dashboard_image": data.get("dashboard_image", {"kind": "preset", "preset": "king_memnon"}),
        "active": True,
    }, merge=True)
    return jsonify({"ok": True})


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
    else:
        audio_bytes = request.get_data()
        filename = request.headers.get("X-Filename",
                   f"upload-{datetime.now().strftime('%Y%m%d-%H%M%S')}.webm")

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

    creds = _drive_creds(uid)
    if not creds:
        return jsonify({"error": "Drive not connected"}), 403

    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    # Ensure notes folder exists
    notes_id = user_data.get("notes_folder_id")
    if not notes_id:
        notes_id = _find_or_create_folder(service, "memnon-notes")
        _get_db().collection("users").document(uid).update({"notes_folder_id": notes_id})

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

    lane = user_data.get("lane", "professional")
    prompt, sources_used = build_prompt(lane, transcript, user_data)
    try:
        ai_result = _summarize(prompt, api_key)
    except Exception as exc:
        print(f"[{uid}] AI error on upload: {exc}")
        ai_result = {"title": Path(filename).stem, "summary": transcript[:300],
                     "action_items": [], "suggested_tags": [], "influenced_by": []}

    note_name = (datetime.now().strftime("%Y-%m-%d") + " — " +
                 ai_result.get("title", Path(filename).stem)[:60] + ".md")
    note_md = _render_note(lane, ai_result, transcript, filename, sources_used)
    if sources_used:
        _record_source_usage(uid, sources_used)
    _store_note_metadata(uid, ai_result, sources_used, note_name)

    media = MediaInMemoryUpload(note_md.encode(), mimetype="text/plain", resumable=False)
    service.files().create(
        body={"name": note_name, "parents": [notes_id]},
        media_body=media, fields="id",
    ).execute()

    print(f"[{uid}] Direct upload note saved: {note_name}")
    return jsonify({"ok": True, "note": note_name})


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
