import sys
import unittest
from pathlib import Path
import io

from flask import Flask


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.blueprint import create_workflows_blueprint
from workflows.service import WorkflowService


class FakeRepository:
    def __init__(self):
        self.records = {}
        self.user_profiles = {
            "user-1": {
                "lane": "professional",
                "profession": "teacher",
                "reflection_style": "practical",
                "reflect_config": {"selected_guides": ["parker_palmer"]},
            }
        }

    def load_user_profile(self, uid):
        return self.user_profiles.get(uid, {"lane": "professional", "profession": "professional"})

    def save_capture(self, uid, record):
        self.records[(uid, record.capture_id)] = record.to_dict()
        return record.capture_id

    def get_capture(self, uid, capture_id):
        return self.records.get((uid, capture_id))

    def list_captures(self, uid, limit=50):
        items = [
            value
            for (record_uid, _capture_id), value in self.records.items()
            if record_uid == uid
        ]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return items[:limit]


class WorkflowApiTests(unittest.TestCase):
    def test_create_and_fetch_capture(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "key_point": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        create_response = client.post(
            "/workflows/captures",
            json={
                "text": (
                    "Met with Jordan today about the product direction. She thinks the system feels too generic "
                    "because it is trying to do too much at once instead of making one strong call. "
                    "Action: revise the result card before the next demo."
                ),
                "context_hint": "product review",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        payload = create_response.get_json()
        capture_id = payload["capture_id"]

        fetch_response = client.get(f"/workflows/captures/{capture_id}")
        self.assertEqual(fetch_response.status_code, 200)
        fetched = fetch_response.get_json()
        self.assertEqual(fetched["capture_id"], capture_id)
        self.assertEqual(fetched["result"]["route_kind"], "direct_professional_note")
        self.assertEqual(
            fetched["result"]["primary_artifact"]["status"],
            "Saved and shaped",
        )
        self.assertEqual(
            fetched["result"]["primary_artifact"]["metadata_line"],
            "Pasted note · Jun 27, 2026 · Product review",
        )
        self.assertEqual(
            [section["label"] for section in fetched["result"]["primary_artifact"]["sections"]],
            ["Key point", "Next step"],
        )
        self.assertEqual(fetched["source_event"]["input_type"], "text")

    def test_create_audio_capture_from_multipart_uses_voice_source_type(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "key_point": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
            transcribe_audio=lambda _audio, _filename, _api_key: (
                "Met with Jordan today about the product direction. "
                "Action: revise the result card before the next demo."
            ),
            transcription_api_key_provider=lambda: "test-key",
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        create_response = client.post(
            "/workflows/captures",
            data={
                "context_hint": "product review",
                "file": (io.BytesIO(b"fake-audio-bytes-that-are-not-empty"), "voice-note.webm", "audio/webm"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(create_response.status_code, 201)
        payload = create_response.get_json()
        self.assertEqual(payload["source_event"]["input_type"], "voice")
        self.assertEqual(payload["result"]["route_kind"], "direct_professional_note")
        self.assertEqual(payload["result"]["primary_artifact"]["metadata_line"], "Voice note · Jun 27, 2026 · Product review")

    def test_audio_capture_rejects_empty_file(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "key_point": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
            transcribe_audio=lambda *_args: "unused transcript",
            transcription_api_key_provider=lambda: "test-key",
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            "/workflows/captures",
            data={
                "file": (io.BytesIO(b""), "voice-note.webm", "audio/webm"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "audio file is empty"})

    def test_audio_capture_rejects_unsupported_format(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "key_point": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
            transcribe_audio=lambda *_args: "unused transcript",
            transcription_api_key_provider=lambda: "test-key",
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            "/workflows/captures",
            data={
                "file": (io.BytesIO(b"not-empty"), "voice-note.wav", "audio/wav"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "unsupported audio format"})

    def test_audio_capture_returns_transcription_failure(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "key_point": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
            transcribe_audio=lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
            transcription_api_key_provider=lambda: "test-key",
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            "/workflows/captures",
            data={
                "file": (io.BytesIO(b"real-audio-bytes"), "voice-note.webm", "audio/webm"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "transcription failed"})

    def test_audio_capture_rejects_oversized_file_before_transcription(self):
        repo = FakeRepository()
        transcribe_calls = []
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "key_point": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
            transcribe_audio=lambda *_args: transcribe_calls.append(True),
            transcription_api_key_provider=lambda: "test-key",
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            "/workflows/captures",
            data={
                "file": (io.BytesIO(b"x" * (5 * 1024 * 1024 + 1)), "voice-note.m4a", "audio/x-m4a"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json(), {"error": "audio file is too large for inline capture"})
        self.assertEqual(transcribe_calls, [])

    def test_fetch_missing_capture_returns_not_found(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "key_point": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.get("/workflows/captures/missing-capture")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "not found"})

    def test_list_captures_returns_saved_result_summaries(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "key_point": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        create_response = client.post(
            "/workflows/captures",
            json={
                "text": "follow up tomorrow",
                "context_hint": "",
            },
        )
        capture_id = create_response.get_json()["capture_id"]

        response = client.get("/workflows/captures")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["capture_id"], capture_id)
        self.assertEqual(payload["items"][0]["next_route"], f"/workflows/result/{capture_id}")


if __name__ == "__main__":
    unittest.main()
