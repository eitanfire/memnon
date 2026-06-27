import sys
import unittest
from pathlib import Path

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


class WorkflowApiTests(unittest.TestCase):
    def test_create_and_fetch_capture(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Teacher workflow note",
                "framing_line": "Pulled from your product reflection.",
                "body": "Start by clarifying the single change that reduces user friction first.",
                "task_list": [],
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
                "text": "I want to turn this messy product reflection into one useful professional note for the next build step.",
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


if __name__ == "__main__":
    unittest.main()
