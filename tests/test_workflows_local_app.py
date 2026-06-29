import sys
import tempfile
import unittest
from pathlib import Path
import io


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.local_app import create_local_app


class WorkflowsLocalAppTests(unittest.TestCase):
    def test_local_app_accepts_audio_capture_multipart(self):
        app = create_local_app(
            transcribe_audio=lambda _audio, _filename, _api_key: (
                "Met with Jordan today about the workflows result page. "
                "Action: revise the result card before the next demo."
            )
        )
        client = app.test_client()
        headers = {"Authorization": "Bearer dev-local-token"}

        response = client.post(
            "/api/workflows/captures",
            headers=headers,
            data={
                "context_hint": "product review",
                "file": (io.BytesIO(b"fake-audio"), "voice-note.webm", "audio/webm"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["input_type"], "voice")
        self.assertEqual(payload["source_event"]["input_type"], "voice")
        self.assertIn(
            "Voice note",
            payload["result"]["primary_artifact"]["metadata_line"],
        )
        self.assertIn(
            "Product review",
            payload["result"]["primary_artifact"]["metadata_line"],
        )

    def test_local_app_creates_and_fetches_capture(self):
        app = create_local_app()
        client = app.test_client()
        headers = {"Authorization": "Bearer dev-local-token"}

        create_response = client.post(
            "/api/workflows/captures",
            headers=headers,
            json={
                "text": "I want to turn this messy product reflection into one useful professional note for the next build step.",
                "context_hint": "product review",
            },
        )

        self.assertEqual(create_response.status_code, 201)
        payload = create_response.get_json()
        self.assertIn("capture_id", payload)

        fetch_response = client.get(
            f"/api/workflows/captures/{payload['capture_id']}",
            headers=headers,
        )
        self.assertEqual(fetch_response.status_code, 200)
        fetched = fetch_response.get_json()
        self.assertEqual(fetched["capture_id"], payload["capture_id"])
        self.assertEqual(fetched["result"]["route_kind"], "direct_professional_note")
        self.assertEqual(
            fetched["result"]["primary_artifact"]["status"],
            "Saved and shaped",
        )
        self.assertIn(
            "Pasted note",
            fetched["result"]["primary_artifact"]["metadata_line"],
        )
        self.assertIn("sections", fetched["result"]["primary_artifact"])
        self.assertEqual(
            [section["label"] for section in fetched["result"]["primary_artifact"]["sections"]],
            ["Key point", "Next step"],
        )
        self.assertIn("source_excerpt", fetched["result"]["primary_artifact"])

    def test_local_app_returns_distinct_saved_note_states(self):
        app = create_local_app()
        client = app.test_client()
        headers = {"Authorization": "Bearer dev-local-token"}

        weak_response = client.post(
            "/api/workflows/captures",
            headers=headers,
            json={
                "text": "follow up tomorrow",
                "context_hint": "",
            },
        )
        ambiguous_response = client.post(
            "/api/workflows/captures",
            headers=headers,
            json={
                "text": (
                    "Not sure what this should become. Something about the product direction I think. "
                    "Could be a note to myself, a follow-up, or maybe just something to hold onto."
                ),
                "context_hint": "",
            },
        )

        self.assertEqual(weak_response.status_code, 201)
        self.assertEqual(ambiguous_response.status_code, 201)
        self.assertEqual(
            weak_response.get_json()["result"]["saved_note_artifact"]["state"],
            "weak_signal",
        )
        self.assertEqual(
            weak_response.get_json()["result"]["saved_note_artifact"]["status"],
            "Saved as a small note",
        )
        self.assertEqual(
            ambiguous_response.get_json()["result"]["saved_note_artifact"]["state"],
            "needs_direction",
        )
        self.assertEqual(
            ambiguous_response.get_json()["result"]["saved_note_artifact"]["status"],
            "Saved, needs direction",
        )

    def test_local_app_allows_localhost_and_127_static_origins(self):
        app = create_local_app()
        client = app.test_client()

        localhost_response = client.options(
            "/api/workflows/captures",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(
            localhost_response.headers.get("Access-Control-Allow-Origin"),
            "http://localhost:8000",
        )

        loopback_response = client.options(
            "/api/workflows/captures",
            headers={
                "Origin": "http://127.0.0.1:8000",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(
            loopback_response.headers.get("Access-Control-Allow-Origin"),
            "http://127.0.0.1:8000",
        )

    def test_local_app_persists_capture_across_app_instances(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "workflow-captures.json"
            headers = {"Authorization": "Bearer dev-local-token"}

            app_one = create_local_app(storage_path=str(storage_path))
            client_one = app_one.test_client()
            create_response = client_one.post(
                "/api/workflows/captures",
                headers=headers,
                json={
                    "text": (
                        "Met with Jordan about the product direction and need one saved result worth reopening later."
                    ),
                    "context_hint": "product review",
                },
            )

            self.assertEqual(create_response.status_code, 201)
            capture_id = create_response.get_json()["capture_id"]

            app_two = create_local_app(storage_path=str(storage_path))
            client_two = app_two.test_client()
            fetch_response = client_two.get(
                f"/api/workflows/captures/{capture_id}",
                headers=headers,
            )

            self.assertEqual(fetch_response.status_code, 200)
            fetched = fetch_response.get_json()
            self.assertEqual(fetched["capture_id"], capture_id)
            self.assertEqual(fetched["result"]["route_kind"], "direct_professional_note")

    def test_local_app_lists_saved_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "workflow-captures.json"
            app = create_local_app(storage_path=str(storage_path))
            client = app.test_client()
            headers = {"Authorization": "Bearer dev-local-token"}

            first = client.post(
                "/api/workflows/captures",
                headers=headers,
                json={
                    "text": "follow up tomorrow",
                    "context_hint": "",
                },
            )
            second = client.post(
                "/api/workflows/captures",
                headers=headers,
                json={
                    "text": "Met with Jordan about the product direction. Action: revisit the result page before the next demo.",
                    "context_hint": "product review",
                },
            )

            response = client.get("/api/workflows/captures", headers=headers)

            self.assertEqual(response.status_code, 200)
            items = response.get_json()["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["capture_id"], second.get_json()["capture_id"])
            self.assertEqual(items[1]["capture_id"], first.get_json()["capture_id"])

    def test_local_app_persists_tiny_and_ambiguous_saved_note_states(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "workflow-captures.json"
            headers = {"Authorization": "Bearer dev-local-token"}
            app_one = create_local_app(storage_path=str(storage_path))
            client_one = app_one.test_client()

            tiny_response = client_one.post(
                "/api/workflows/captures",
                headers=headers,
                json={"text": "follow up tomorrow", "context_hint": ""},
            )
            ambiguous_response = client_one.post(
                "/api/workflows/captures",
                headers=headers,
                json={
                    "text": "This could be a reflection, but it also might be something I need to send to Kyle.",
                    "context_hint": "",
                },
            )

            tiny_id = tiny_response.get_json()["capture_id"]
            ambiguous_id = ambiguous_response.get_json()["capture_id"]

            app_two = create_local_app(storage_path=str(storage_path))
            client_two = app_two.test_client()
            tiny_fetch = client_two.get(f"/api/workflows/captures/{tiny_id}", headers=headers)
            ambiguous_fetch = client_two.get(f"/api/workflows/captures/{ambiguous_id}", headers=headers)

            self.assertEqual(tiny_fetch.status_code, 200)
            self.assertEqual(ambiguous_fetch.status_code, 200)
            self.assertEqual(
                tiny_fetch.get_json()["result"]["saved_note_artifact"]["state"],
                "weak_signal",
            )
            self.assertEqual(
                ambiguous_fetch.get_json()["result"]["saved_note_artifact"]["state"],
                "needs_direction",
            )
            self.assertEqual(
                ambiguous_fetch.get_json()["result"]["likely_themes"],
                [],
            )
            self.assertNotIn(
                " or ",
                ambiguous_fetch.get_json()["result"]["saved_note_artifact"]["title"].lower(),
            )
            self.assertEqual(
                [section["label"] for section in ambiguous_fetch.get_json()["result"]["saved_note_artifact"]["sections"]],
                ["Why keep this"],
            )


if __name__ == "__main__":
    unittest.main()
