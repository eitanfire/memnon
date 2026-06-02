"""
Memnon SaaS — web server.

Routes:
  GET  /              Landing page (→ /dashboard if logged in)
  GET  /login         Redirect to Google OAuth
  GET  /auth/callback Handle OAuth callback, store user + tokens
  GET  /setup         Onboarding form (lane, profession/tradition)
  POST /setup         Save user config
  GET  /dashboard     Show recent status and notes folder link
  POST /logout        Clear session

Run locally:
    OPENAI_API_KEY=sk-... FLASK_SECRET=changeme PORT=5050 python src/server.py

Deploy to Render:
  See render.yaml — set OPENAI_API_KEY + FLASK_SECRET + GOOGLE_CLIENT_SECRETS env vars.
"""

import json
import logging
import os
import sys
from pathlib import Path

from flask import (Flask, redirect, render_template, request,
                   session, url_for)
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db
from lanes import LANES
import worker

# ── app setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__, template_folder=str(ROOT / "templates" / "web"))
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("memnon.server")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Google client secrets — from env var (production) or file (local dev)
def _client_secrets_path() -> Path:
    # In production, set GOOGLE_CLIENT_SECRETS to the JSON content
    env_json = os.environ.get("GOOGLE_CLIENT_SECRETS")
    if env_json:
        p = ROOT / "runtime" / "google_client_secrets.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(env_json)
        return p
    # Local: look next to config.json
    for candidate in [
        ROOT / "google_client_secrets.json",
        ROOT / "runtime" / "google_client_secrets.json",
    ]:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "google_client_secrets.json not found. "
        "Set GOOGLE_CLIENT_SECRETS env var in production, "
        "or place the file next to config.json locally."
    )


def _flow():
    return Flow.from_client_secrets_file(
        str(_client_secrets_path()),
        scopes=SCOPES,
        redirect_uri=url_for("auth_callback", _external=True),
    )


# ── auth helpers ──────────────────────────────────────────────────────────────

def _current_user() -> dict | None:
    email = session.get("email")
    if not email:
        return None
    return db.get_user(email)


def _require_login():
    user = _current_user()
    if not user:
        return redirect(url_for("index"))
    return user


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if session.get("email"):
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/login")
def login():
    flow = _flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    flow = _flow()
    flow.fetch_token(authorization_response=request.url)
    creds: Credentials = flow.credentials

    # Get user profile
    people = build("oauth2", "v2", credentials=creds)
    info = people.userinfo().get().execute()
    email = info["email"]
    name = info.get("name", email.split("@")[0])

    token_data = json.loads(creds.to_json())
    db.upsert_user(email, name, token_data)

    session["email"] = email
    session["name"] = name
    log.info("Login: %s (%s)", name, email)

    user = db.get_user(email)
    if not user.get("lane"):
        return redirect(url_for("setup"))
    return redirect(url_for("dashboard"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    result = _require_login()
    if not isinstance(result, dict):
        return result
    user = result

    if request.method == "POST":
        lane = request.form.get("lane", "professional")
        profession = request.form.get("profession", "").strip()
        tradition = request.form.get("tradition", "secular")
        db.update_user(user["email"], lane=lane, profession=profession, tradition=tradition)
        log.info("Setup saved for %s: lane=%s", user["email"], lane)
        return redirect(url_for("dashboard"))

    return render_template("setup.html",
                           user=user,
                           lanes=LANES,
                           name=session.get("name", ""))


@app.route("/dashboard")
def dashboard():
    result = _require_login()
    if not isinstance(result, dict):
        return result
    user = result

    notes_folder_link = None
    if user.get("notes_folder_id"):
        notes_folder_link = (
            f"https://drive.google.com/drive/folders/{user['notes_folder_id']}"
        )

    inbox_folder_link = None
    if user.get("inbox_folder_id"):
        inbox_folder_link = (
            f"https://drive.google.com/drive/folders/{user['inbox_folder_id']}"
        )

    lane_info = LANES.get(user.get("lane", "professional"), {})

    return render_template("dashboard.html",
                           user=user,
                           name=session.get("name", ""),
                           lane_info=lane_info,
                           notes_folder_link=notes_folder_link,
                           inbox_folder_link=inbox_folder_link)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── startup ───────────────────────────────────────────────────────────────────

def create_app():
    db.init()
    worker.start_background()
    return app


if __name__ == "__main__":
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # local HTTP dev only
    create_app()
    port = int(os.environ.get("PORT", "5050"))
    log.info("Starting local server on http://localhost:%s", port)
    app.run(debug=True, port=port, use_reloader=False)
