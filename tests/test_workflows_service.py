import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

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


class WorkflowServiceTests(unittest.TestCase):
    def test_service_creates_one_professional_note_record(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Teacher workflow note",
                "framing_line": "Pulled from your product reflection.",
                "body": "Start by clarifying the single change that reduces user friction first.",
                "task_list": ["Clarify the primary flow", "Cut one extra decision"],
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text="I want to turn this messy product reflection into one useful professional note for the next build step.",
            context_hint="product review",
        )

        self.assertEqual(record.result["route_kind"], "direct_professional_note")
        self.assertEqual(record.result["primary_artifact"]["kind"], "professional_note")
        self.assertEqual(record.result["secondary_artifacts"], [])
        self.assertEqual(record.source_event["profile_snapshot"]["lane"], "professional")

    def test_service_fetches_saved_capture(self):
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
        record = service.create_text_capture(
            uid="user-1",
            source_text="I want to turn this messy product reflection into one useful professional note for the next build step.",
            context_hint="product review",
        )

        fetched = service.get_capture("user-1", record.capture_id)
        self.assertEqual(fetched["capture_id"], record.capture_id)
        self.assertEqual(fetched["result"]["route_kind"], "direct_professional_note")


if __name__ == "__main__":
    unittest.main()
