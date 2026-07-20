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
        self.contexts = {}

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

    def create_context(self, uid, *, context_id, title, summary, seed_capture_id, now):
        context = {
            "context_id": context_id,
            "title": title,
            "summary": summary,
            "status": "active",
            "seed_capture_id": seed_capture_id,
            "created_at": now,
            "updated_at": now,
            "last_activity_at": now,
        }
        self.contexts[(uid, context_id)] = context
        return context

    def get_context(self, uid, context_id):
        return self.contexts.get((uid, context_id))

    def touch_context_activity(self, uid, context_id, now):
        context = self.contexts.get((uid, context_id))
        if context is None:
            return None
        context["last_activity_at"] = now
        context["updated_at"] = now
        return context

    def update_capture_threading(self, uid, capture_id, threading):
        record = self.records[(uid, capture_id)]
        record["threading"] = dict(threading)
        return record

    def update_capture_feedback(self, uid, capture_id, feedback_choice, feedback_note, feedback_updated_at):
        record = self.records[(uid, capture_id)]
        record["feedback_choice"] = feedback_choice
        record["feedback_note"] = feedback_note
        record["feedback_updated_at"] = feedback_updated_at
        return record


class WorkflowApiTests(unittest.TestCase):
    def test_create_and_fetch_capture(self):
        repo = FakeRepository()
        bridge_calls = []

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
            continuity_bridge_writer=lambda **payload: bridge_calls.append(payload),
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
            ["Next step"],
        )
        self.assertEqual(fetched["source_event"]["input_type"], "text")
        self.assertEqual(len(bridge_calls), 1)
        self.assertEqual(bridge_calls[0]["capture_record"]["capture_id"], capture_id)
        self.assertEqual(bridge_calls[0]["include_teaching_context"], True)

    def test_api_primary_artifact_keeps_source_excerpt_and_sections(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
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

        create_capture_response = client.post(
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
        payload = create_capture_response.get_json()
        artifact = payload["result"]["primary_artifact"]
        self.assertTrue(artifact["source_excerpt"])
        self.assertIn("metadata_line", artifact)
        self.assertIn("sections", artifact)

    def test_create_audio_capture_from_multipart_uses_voice_source_type(self):
        repo = FakeRepository()
        bridge_calls = []
        archived_audio = {}

        def archive_voice_capture_audio(*, uid, capture_id, audio_bytes, filename, content_type):
            storage_path = f"workflow-voice-audio/{uid}/{capture_id}/{filename}"
            archived_audio[storage_path] = {
                "bytes": audio_bytes,
                "content_type": content_type,
            }
            return storage_path

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
            continuity_bridge_writer=lambda **payload: bridge_calls.append(payload),
        )
        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
            transcribe_audio=lambda _audio, _filename, _api_key: (
                "Met with Jordan today about the product direction. "
                "Action: revise the result card before the next demo."
            ),
            transcription_api_key_provider=lambda: "test-key",
            archive_voice_capture_audio=archive_voice_capture_audio,
            download_voice_capture_audio=lambda storage_path: archived_audio[storage_path]["bytes"],
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
        self.assertIn("source_audio_storage_path", payload["source_event"])
        self.assertEqual(payload["source_event"]["source_audio_content_type"], "audio/webm")
        self.assertEqual(payload["result"]["route_kind"], "direct_professional_note")
        self.assertEqual(payload["result"]["primary_artifact"]["metadata_line"], "Voice note · Jun 27, 2026 · Product review")
        self.assertEqual(len(bridge_calls), 1)
        self.assertEqual(bridge_calls[0]["capture_record"]["source_event"]["input_type"], "voice")
        self.assertEqual(bridge_calls[0]["include_teaching_context"], True)

        audio_response = client.get(f"/workflows/captures/{payload['capture_id']}/source-audio")
        self.assertEqual(audio_response.status_code, 200)
        self.assertEqual(audio_response.data, b"fake-audio-bytes-that-are-not-empty")
        self.assertEqual(audio_response.headers["Content-Type"], "audio/webm")

    def test_create_file_capture_from_multipart_preserves_file_source_metadata(self):
        repo = FakeRepository()
        bridge_calls = []

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Workshop plan draft",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The plan needs one tighter opener and one clear follow-up.",
                "next_step": "Tighten the opening before sharing it.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
            continuity_bridge_writer=lambda **payload: bridge_calls.append(payload),
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
            data={
                "context_hint": "product review",
                "file": (
                    io.BytesIO(
                        (
                            "# Workshop plan\n\n"
                            "Draft the opening more tightly, then send the revision by Friday."
                        ).encode("utf-8")
                    ),
                    "workshop-plan.md",
                    "text/markdown",
                ),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(create_response.status_code, 201)
        payload = create_response.get_json()
        self.assertEqual(payload["input_type"], "file")
        self.assertEqual(payload["source_event"]["input_type"], "file")
        self.assertEqual(payload["source_event"]["source_filename"], "workshop-plan.md")
        self.assertEqual(payload["source_event"]["source_file_type"], "text/markdown")
        self.assertEqual(payload["source_event"]["source_file_extension"], ".md")
        self.assertGreater(payload["source_event"]["source_file_size_bytes"], 0)
        self.assertEqual(
            payload["result"]["primary_artifact"]["metadata_line"],
            "Uploaded file · Jun 27, 2026 · Product review",
        )
        self.assertEqual(len(bridge_calls), 1)
        self.assertEqual(bridge_calls[0]["capture_record"]["source_event"]["input_type"], "file")
        self.assertEqual(bridge_calls[0]["include_teaching_context"], True)

    def test_file_capture_rejects_unsupported_extension(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {},
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

        response = client.post(
            "/workflows/captures",
            data={
                "file": (io.BytesIO(b"hello"), "notes.pdf", "application/pdf"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "File must be .txt or .md.")

    def test_file_capture_rejects_oversized_file(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {},
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

        response = client.post(
            "/workflows/captures",
            data={
                "file": (io.BytesIO(b"a" * (512 * 1024 + 1)), "notes.txt", "text/plain"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["error"], "File is too large. Maximum size is 512 KB.")

    def test_file_capture_rejects_unreadable_text(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {},
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

        response = client.post(
            "/workflows/captures",
            data={
                "file": (io.BytesIO(b"\xff\xfe\x00"), "notes.txt", "text/plain"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "We couldn’t read text from this file.")

    def test_audio_capture_rejects_empty_file(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "summary": "Unused",
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
                "summary": "Unused",
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
                "summary": "Unused",
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
                "summary": "Unused",
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
                "summary": "Unused",
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
                "summary": "The result needs to feel more like a saved object than a generated response.",
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

    def test_list_active_contexts_returns_active_threads_only(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "summary": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        active_context = service.create_context("user-1", title="Workflows UI/UX", summary="")
        archived_context = service.create_context("user-1", title="Voice capture", summary="")
        repo.contexts[("user-1", archived_context["context_id"])]["status"] = "archived"

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.get("/workflows/contexts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["title"] for item in response.get_json()["items"]],
            [active_context["title"]],
        )

    def test_list_active_contexts_filters_archived_before_limit(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "summary": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        for index in range(12):
            context = service.create_context("user-1", title=f"Archived {index}", summary="")
            repo.contexts[("user-1", context["context_id"])]["status"] = "archived"
        active_context_a = service.create_context("user-1", title="Workflows UI/UX", summary="")
        active_context_b = service.create_context("user-1", title="Voice capture", summary="")

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.get("/workflows/contexts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["title"] for item in response.get_json()["items"]],
            [active_context_a["title"], active_context_b["title"]],
        )

    def test_context_decision_endpoint_confirms_thread_for_capture(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan about the workflows page. "
                "Action: revise the result card."
            ),
            context_hint="",
        )
        context = service.create_context("user-1", title="Workflows UI/UX", summary="")

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            f"/workflows/captures/{capture.capture_id}/context-decision",
            json={"action": "confirmed", "context_id": context["context_id"]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["threading"]["confirmed_context_id"],
            context["context_id"],
        )
        self.assertEqual(
            payload["result"]["related_thread"]["confirmed_title"],
            "Workflows UI/UX",
        )

    def test_context_decision_endpoint_records_selected_different_context(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs a different thread choice.",
                "next_step": "Record the alternate thread decision.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        suggested_context = service.create_context("user-1", title="Workflows UI/UX", summary="")
        alternate_context = service.create_context("user-1", title="Voice capture", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan about the workflows page. "
                "Action: record the alternate thread decision."
            ),
            context_hint="workflows ui/ux",
        )
        self.assertEqual(capture.threading["suggested_context_id"], suggested_context["context_id"])

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            f"/workflows/captures/{capture.capture_id}/context-decision",
            json={"action": "selected_different_context", "context_id": alternate_context["context_id"]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["threading"]["context_decision"], "selected_different_context")
        self.assertEqual(
            payload["threading"]["confirmed_context_id"],
            alternate_context["context_id"],
        )
        self.assertEqual(
            payload["result"]["related_thread"]["confirmed_title"],
            "Voice capture",
        )

    def test_context_decision_endpoint_can_create_new_context(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Voice capture product direction",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs its own new thread.",
                "next_step": "Create the thread from the chooser flow.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Need a dedicated thread for voice capture product direction. "
                "Action: create the thread from the chooser flow."
            ),
            context_hint="",
        )

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            f"/workflows/captures/{capture.capture_id}/context-decision",
            json={"action": "created_new_context", "new_context_title": "Voice capture"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["threading"]["context_decision"], "created_new_context")
        self.assertTrue(payload["threading"]["confirmed_context_id"])
        self.assertEqual(
            payload["result"]["related_thread"]["confirmed_title"],
            "Voice capture",
        )
        created_context = repo.get_context("user-1", payload["threading"]["confirmed_context_id"])
        self.assertIsNotNone(created_context)
        self.assertEqual(created_context["seed_capture_id"], capture.capture_id)

    def test_feedback_endpoint_records_choice(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="",
        )
        original_threading = dict(capture.threading)
        original_result = dict(capture.result)

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            f"/workflows/captures/{capture.capture_id}/feedback",
            json={"feedback_choice": "useful"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["feedback_choice"], "useful")
        self.assertEqual(payload["feedback_updated_at"], "2026-07-01T18:00:00Z")
        self.assertEqual(payload["threading"], original_threading)
        self.assertEqual(payload["result"], original_result)

    def test_feedback_endpoint_replaces_existing_choice(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="",
        )

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        first = client.post(
            f"/workflows/captures/{capture.capture_id}/feedback",
            json={"feedback_choice": "useful"},
        )
        self.assertEqual(first.status_code, 200)

        response = client.post(
            f"/workflows/captures/{capture.capture_id}/feedback",
            json={"feedback_choice": "not_useful"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["feedback_choice"], "not_useful")

    def test_feedback_endpoint_rejects_invalid_choice(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="",
        )

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            f"/workflows/captures/{capture.capture_id}/feedback",
            json={"feedback_choice": "too_generic"},
        )

        self.assertEqual(response.status_code, 400)

    def test_feedback_endpoint_returns_not_found_for_missing_capture(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "summary": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            "/workflows/captures/missing-capture/feedback",
            json={"feedback_choice": "useful"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "not found"})

    def test_feedback_endpoint_returns_unauthorized_without_token(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "summary": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: None,
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            "/workflows/captures/cap-1/feedback",
            json={"feedback_choice": "useful"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "unauthorized"})

    def test_suggestion_invocation_endpoint_creates_derived_saved_result(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "BoulderJS meetup recap",
                "framing_line": "A saved result shaped around the strongest public takeaway.",
                "summary": "The meetup recap should become a public-facing post for the community.",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-03T12:00:00Z",
            api_key_provider=lambda: "test-key",
            social_post_generator=lambda *_args, **_kwargs: {
                "title": "BoulderJS next meetup",
                "framing_line": "A concise social draft built from the capture.",
                "body": "Thanks to everyone who came to BoulderJS tonight. Join us next week for the next meetup.",
                "sections": [],
                "copy_text": "Thanks to everyone who came to BoulderJS tonight. Join us next week for the next meetup.",
            },
            professional_analysis_generator=lambda *_args, **_kwargs: {
                "title": "Unused",
                "framing_line": "Unused",
                "body": "Unused",
                "sections": [],
                "copy_text": "Unused",
            },
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text=(
                "BoulderJS meetup recap: thanks to everyone who came tonight. "
                "Share the highlights and invite the community to next week's event."
            ),
            context_hint="",
        )

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            f"/workflows/captures/{capture.capture_id}/suggestions",
            json={"suggestion_type": "draft_social_post"},
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertNotEqual(payload["capture_id"], capture.capture_id)
        self.assertEqual(payload["next_route"], f"/workflows/result/{payload['capture_id']}")
        self.assertNotIn("contextual_suggestions", payload["result"])
        self.assertNotIn("contextual_suggestions", payload.get("event_manifest") or {})

    def test_suggestion_endpoint_rejects_invalid_or_unshown_suggestion(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Small note",
                "framing_line": "Saved quietly.",
                "summary": "Unused",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-03T12:00:00Z",
            api_key_provider=lambda: "test-key",
            social_post_generator=lambda *_args, **_kwargs: {
                "title": "Unused",
                "framing_line": "Unused",
                "body": "Unused",
                "sections": [],
                "copy_text": "Unused",
            },
            professional_analysis_generator=lambda *_args, **_kwargs: {
                "title": "Unused",
                "framing_line": "Unused",
                "body": "Unused",
                "sections": [],
                "copy_text": "Unused",
            },
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Hold onto this for later",
            context_hint="",
        )

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            f"/workflows/captures/{capture.capture_id}/suggestions",
            json={"suggestion_type": "draft_social_post"},
        )

        self.assertEqual(response.status_code, 400)

    def test_create_capture_response_hides_internal_contextual_suggestion_diagnostics(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "BoulderJS meetup recap",
                "framing_line": "A saved result shaped around the strongest public takeaway.",
                "summary": "The meetup recap should become a public-facing post for the community.",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-03T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        blueprint = create_workflows_blueprint(
            verify_token=lambda _request: "user-1",
            service_provider=lambda: service,
        )
        app = Flask(__name__)
        app.register_blueprint(blueprint, url_prefix="/workflows")
        client = app.test_client()

        response = client.post(
            "/workflows/captures",
            json={
                "text": (
                    "BoulderJS meetup recap: thanks to everyone who came tonight. "
                    "Share the highlights and invite the community to next week's event."
                ),
                "context_hint": "",
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(
            [item["type"] for item in payload["result"].get("contextual_suggestions") or []],
            ["draft_social_post"],
        )
        self.assertNotIn("contextual_suggestions", payload.get("event_manifest") or {})


if __name__ == "__main__":
    unittest.main()
