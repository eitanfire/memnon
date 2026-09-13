from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

from .ai import generate_professional_note, load_openai_api_key
from .blueprint import create_workflows_blueprint
from .service import (
    WorkflowService,
    _should_surface_next_step,
    derive_next_step,
    derive_specific_title,
    derive_summary,
)

DEFAULT_STORAGE_PATH = Path(__file__).resolve().parents[2] / ".local" / "workflow-captures.json"


class InMemoryWorkflowRepository:
    def __init__(self):
        self.records = {}
        self.user_profiles = {
            "local-dev-user": {
                "lane": "professional",
                "profession": "professional",
                "reflection_style": "practical",
                "reflect_config": {},
            }
        }

    def load_user_profile(self, uid: str):
        return self.user_profiles.get(uid, self.user_profiles["local-dev-user"])

    def save_capture(self, uid: str, record):
        self.records[(uid, record.capture_id)] = record.to_dict()
        return record.capture_id

    def get_capture(self, uid: str, capture_id: str):
        return self.records.get((uid, capture_id))

    def list_captures(self, uid: str, limit: int = 50):
        items = [
            value
            for (record_uid, _capture_id), value in self.records.items()
            if record_uid == uid
        ]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return items[:limit]

    def update_capture_feedback(
        self, uid: str, capture_id: str, feedback_choice: str, feedback_note: str, feedback_updated_at: str
    ):
        record = self.records[(uid, capture_id)]
        record["feedback_choice"] = feedback_choice
        record["feedback_note"] = feedback_note
        record["feedback_updated_at"] = feedback_updated_at


class FileBackedWorkflowRepository(InMemoryWorkflowRepository):
    def __init__(self, storage_path: str):
        super().__init__()
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        if not self.storage_path.exists():
            return
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        self.records = {
            tuple(key.split("::", 1)): value
            for key, value in payload.get("records", {}).items()
        }
        profiles = payload.get("user_profiles")
        if isinstance(profiles, dict) and profiles:
            self.user_profiles.update(profiles)

    def _persist(self):
        payload = {
            "records": {
                f"{uid}::{capture_id}": value
                for (uid, capture_id), value in self.records.items()
            },
            "user_profiles": self.user_profiles,
        }
        self.storage_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def save_capture(self, uid: str, record):
        capture_id = super().save_capture(uid, record)
        self._persist()
        return capture_id

    def update_capture_feedback(
        self, uid: str, capture_id: str, feedback_choice: str, feedback_note: str, feedback_updated_at: str
    ):
        super().update_capture_feedback(uid, capture_id, feedback_choice, feedback_note, feedback_updated_at)
        self._persist()


def _verify_local_token(req) -> str | None:
    header = req.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return "local-dev-user"


def _local_note_generator(source_text, context_hint, profile, api_key, allow_next_step=True):
    next_step = ""
    if allow_next_step:
        candidate_next_step = derive_next_step(source_text)
        if _should_surface_next_step(source_text, context_hint, candidate_next_step, input_type="text"):
            next_step = candidate_next_step

    return {
        "title": derive_specific_title(source_text, context_hint, "Saved note"),
        "framing_line": "Shaped from your note into one saved result worth reopening.",
        "summary": derive_summary(source_text, context_hint, ""),
        "next_step": next_step,
    }


def _local_transcribe_audio(_audio_bytes: bytes, _filename: str, _api_key: str) -> str:
    return "Voice note captured in local development. Action: review the transcript-backed result path."


def _local_social_post_generator(source_text, context_hint, profile, api_key):
    del context_hint, profile, api_key
    first_sentence = source_text.split(".")[0].strip()
    body = first_sentence or "Sharing one clear update from this capture."
    if not body.endswith("."):
        body += "."
    return {
        "title": "Social post draft",
        "framing_line": "A public-facing draft built from your saved result.",
        "body": body,
        "sections": [],
        "copy_text": body,
    }


def _local_professional_analysis_generator(source_text, context_hint, profile, api_key):
    del context_hint, profile, api_key
    summary = derive_summary(source_text, "", "")
    return {
        "title": "Professional analysis",
        "framing_line": "A concise professional read grounded in the source material.",
        "body": summary,
        "sections": [
            {"label": "Read", "text": summary},
        ],
        "copy_text": summary,
    }


# Local stand-ins for the production endpoints /today calls but that the
# workflows slice does not implement. Without these, /today on localhost 404s
# on /me and the daily brief, so sign-in, the standing-context block and the
# brief cannot be exercised offline at all -- and a 404 there looks exactly
# like a regression.
LOCAL_DAILY_FEED_STATE = {
    "enabled": False,
    "feed_url": "",
}


def _local_profile(repository) -> dict:
    """A representative signed-in profile for local /today.

    Carries both groups the standing-context block distinguishes: fields that
    shape a capture (lane, reflection_style) and fields that are merely stored
    (subjects, grades, standards, narration voice). Set
    WORKFLOWS_LOCAL_EMPTY_PROFILE=1 to exercise the empty state instead.
    """
    base = dict(repository.load_user_profile("local-dev-user"))
    if os.environ.get("WORKFLOWS_LOCAL_EMPTY_PROFILE", "").strip().lower() in ("1", "true", "yes"):
        stored = {}
    else:
        stored = {
            "preferred_name": "Local Dev",
            "preferred_pronouns": "they/them",
            "subjects": "Biology, Chemistry",
            "grade_levels": [9, 10],
            "state_standards": ["SC.9.1", "SC.9.2"],
            "narration_voice": "sage",
            "school_name": "Local Dev High School",
        }
    return {
        **base,
        **stored,
        "email": "local-dev@example.com",
        "drive_connected": True,
        "google_tasks_connected": False,
        "research_recommendations": {},
        "daily_feed_enabled": LOCAL_DAILY_FEED_STATE["enabled"],
        "daily_feed_url": LOCAL_DAILY_FEED_STATE["feed_url"],
        "daily_feed_can_regenerate": True,
        "daily_feed_status": {
            "state": "ready" if LOCAL_DAILY_FEED_STATE["enabled"] else "off",
            "latest_episode": {},
            "publish_hour_local": 7,
        },
    }


def _register_local_stubs(app, repository):
    @app.get("/api/me")
    def local_me():
        if _verify_local_token(request) is None:
            return jsonify({"error": "unauthorized"}), 401
        return jsonify(_local_profile(repository))

    @app.post("/api/daily-feed/setup")
    def local_daily_feed_setup():
        if _verify_local_token(request) is None:
            return jsonify({"error": "unauthorized"}), 401
        payload = request.get_json(silent=True) or {}
        LOCAL_DAILY_FEED_STATE["enabled"] = bool(payload.get("enabled"))
        LOCAL_DAILY_FEED_STATE["feed_url"] = (
            "http://127.0.0.1:5051/api/daily-feed/local-dev.xml"
            if LOCAL_DAILY_FEED_STATE["enabled"]
            else ""
        )
        return jsonify({
            "enabled": LOCAL_DAILY_FEED_STATE["enabled"],
            "feed_url": LOCAL_DAILY_FEED_STATE["feed_url"],
            "daily_feed_timezone": "America/Denver",
            "daily_feed_publish_hour_local": 7,
        })

    @app.post("/api/daily-feed/generate-today")
    def local_daily_feed_generate():
        if _verify_local_token(request) is None:
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"ok": True, "regenerated": True})

    @app.get("/api/tasks")
    def local_tasks():
        return jsonify({"items": []})

    @app.post("/api/usage-event")
    def local_usage_event():
        # Printed rather than dropped: thread-suggestion telemetry is the whole
        # reason to run this page locally right now.
        payload = request.get_json(silent=True) or {}
        print(json.dumps({"component": "usage_event", **payload}, sort_keys=True, default=str))
        return jsonify({"ok": True})


def create_local_app(storage_path: str | None = None, transcribe_audio=None):
    app = Flask(__name__)
    CORS(
        app,
        origins=[
            "http://localhost:8000",
            "http://localhost:8080",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8080",
        ],
    )

    effective_storage_path = storage_path or os.environ.get("WORKFLOWS_LOCAL_STORAGE_PATH") or str(DEFAULT_STORAGE_PATH)
    repository = FileBackedWorkflowRepository(effective_storage_path)

    # Semantic thread matching is off locally unless a real key is present, so
    # ordinary local dev stays offline and free. Export HUGGING_FACE_API_KEY to
    # exercise the same path production uses -- the fastest way to confirm the
    # model still answers before deploying.
    hugging_face_api_key = os.environ.get("HUGGING_FACE_API_KEY", "").strip()
    if hugging_face_api_key:
        from hf_inference import embed_text

        embedding_provider = lambda text: embed_text(text, hugging_face_api_key)
        embedding_label = "hugging-face"
    else:
        embedding_provider = None
        embedding_label = "disabled"

    use_real_llm = os.environ.get("WORKFLOWS_LOCAL_USE_REAL_LLM", "").strip().lower() in ("1", "true", "yes")
    if use_real_llm:
        note_generator = generate_professional_note
        api_key_provider = load_openai_api_key
        generator_label = "llm"
    else:
        note_generator = _local_note_generator
        api_key_provider = lambda: "local-dev"
        generator_label = "heuristic"

    def _print_usage_event(uid, event_name, metadata):
        print(json.dumps(
            {"component": "usage_event", "uid": uid, "event": event_name, "metadata": metadata},
            sort_keys=True,
            default=str,
        ))

    service = WorkflowService(
        repository=repository,
        note_generator=note_generator,
        now_provider=lambda: datetime.now().astimezone().isoformat(),
        api_key_provider=api_key_provider,
        social_post_generator=_local_social_post_generator,
        professional_analysis_generator=_local_professional_analysis_generator,
        embedding_provider=embedding_provider,
        usage_logger=_print_usage_event,
        generator_label=generator_label,
    )
    app.register_blueprint(
        create_workflows_blueprint(
            verify_token=_verify_local_token,
            service_provider=lambda: service,
            transcribe_audio=transcribe_audio or _local_transcribe_audio,
            transcription_api_key_provider=lambda: "local-dev",
        ),
        url_prefix="/api/workflows",
    )

    _register_local_stubs(app, repository)

    @app.get("/health")
    def health():
        return jsonify({
            "ok": True,
            "generator": generator_label,
            "embeddings": embedding_label,
        })

    return app


app = create_local_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5051"))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
