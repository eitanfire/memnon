from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS

from .blueprint import create_workflows_blueprint
from .service import (
    WorkflowService,
    _should_surface_next_step,
    derive_key_point,
    derive_next_step,
    derive_specific_title,
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
        "key_point": derive_key_point(source_text, context_hint, ""),
        "next_step": next_step,
    }


def _local_transcribe_audio(_audio_bytes: bytes, _filename: str, _api_key: str) -> str:
    return "Voice note captured in local development. Action: review the transcript-backed result path."


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
    service = WorkflowService(
        repository=repository,
        note_generator=_local_note_generator,
        now_provider=lambda: datetime.now().astimezone().isoformat(),
        api_key_provider=lambda: "local-dev",
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

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    return app


app = create_local_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5051"))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
