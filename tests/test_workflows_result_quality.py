import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.service import WorkflowService


MEETING_DEBRIEF = (
    "Met with Jordan about the workflows page for Memnon. "
    "The result still feels too generic and not enough like a saved object worth revisiting. "
    "Action: revise the result card before the next demo."
)

PRODUCT_IDEA = (
    "I want Memnon to feel competitive with capture-first apps without turning into a dashboard. "
    "The key is one useful saved result, not more workflow options."
)

FOLLOW_UP_NOTE = (
    "Send Jordan the revised result card draft by Thursday and ask whether the title feels specific enough."
)

VOICE_REFLECTION = (
    "I keep noticing that the strong part of Memnon is restraint. "
    "If the input is weak, it should save it honestly instead of pretending it knows what to do."
)


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


def build_service():
    return WorkflowService(
        repository=FakeRepository(),
        note_generator=lambda *_args, **_kwargs: {
            "title": "Professional note",
            "framing_line": "Shaped from your note into one practical artifact.",
            "key_point": "The note already points toward one useful direction and is worth shaping into a concrete next step.",
            "next_step": "Revise the result card before the next demo.",
        },
        now_provider=lambda: "2026-06-27T16:00:00Z",
        api_key_provider=lambda: "test-key",
    )


class WorkflowResultQualityTests(unittest.TestCase):
    def test_meeting_debrief_produces_specific_title_grounded_excerpt_and_next_step(self):
        service = build_service()

        record = service.create_text_capture("user-1", MEETING_DEBRIEF, "")
        artifact = record.result["primary_artifact"]
        self.assertTrue("Jordan" in artifact["title"] or "Workflows" in artifact["title"])
        self.assertTrue(artifact["source_excerpt"].startswith("Met with Jordan"))
        self.assertEqual([section["label"] for section in artifact["sections"]], ["Key point", "Next step"])
        self.assertNotIn(artifact["title"].lower(), {"professional note", "suggested note", "saved note"})

    def test_product_idea_can_be_worth_reopening_without_forced_action(self):
        service = build_service()

        record = service.create_text_capture("user-1", PRODUCT_IDEA, "")
        artifact = record.result["primary_artifact"]
        self.assertNotEqual(artifact["title"], "Professional note")
        self.assertTrue(artifact["source_excerpt"])
        self.assertNotIn("teacher", artifact["framing_line"].lower())

    def test_follow_up_note_prefers_action_shaped_next_step(self):
        service = build_service()

        record = service.create_text_capture("user-1", FOLLOW_UP_NOTE, "")
        artifact = record.result["primary_artifact"]
        self.assertEqual(artifact["sections"][-1]["label"], "Next step")
        self.assertTrue("Jordan" in artifact["sections"][-1]["text"] or "Thursday" in artifact["sections"][-1]["text"])

    def test_voice_reflection_stays_grounded_without_inventing_external_action(self):
        service = build_service()

        record = service.create_text_capture("user-1", VOICE_REFLECTION, "", input_type="voice")
        artifact = record.result["primary_artifact"] or record.result["saved_note_artifact"]
        self.assertTrue(artifact["source_excerpt"])

    def test_non_action_capture_can_omit_next_step(self):
        service = build_service()

        record = service.create_text_capture("user-1", PRODUCT_IDEA, "")
        artifact = record.result["primary_artifact"]
        labels = [section["label"] for section in artifact["sections"]]
        self.assertEqual(labels, ["Key point"])

