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
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "key_point": "The system feels too generic because the result does not yet feel like a saved object.",
                "next_step": "Revise the result card so it includes a stronger title, one excerpt, and one concrete next step.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the product direction. She thinks the system feels too generic "
                "because it is trying to do too much at once instead of making one strong call. "
                "Action: revise the result card this week."
            ),
            context_hint="product review",
        )

        self.assertEqual(record.result["route_kind"], "direct_professional_note")
        self.assertEqual(record.result["primary_artifact"]["kind"], "professional_note")
        self.assertEqual(
            record.result["primary_artifact"]["title"],
            "Product direction conversation with Jordan",
        )
        self.assertEqual(
            record.result["primary_artifact"]["status"],
            "Saved and shaped",
        )
        self.assertEqual(
            record.result["primary_artifact"]["metadata_line"],
            "Pasted note · Jun 27, 2026 · Product review",
        )
        self.assertEqual(
            [section["label"] for section in record.result["primary_artifact"]["sections"]],
            ["Key point", "Next step"],
        )
        self.assertIn("system feels too generic", record.result["primary_artifact"]["sections"][0]["text"])
        self.assertIn("Revise the result card", record.result["primary_artifact"]["sections"][1]["text"])
        self.assertIn("Met with Jordan today about the product direction.", record.result["primary_artifact"]["source_excerpt"])
        self.assertIn("product direction", record.result["primary_artifact"]["framing_line"].lower())
        self.assertEqual(record.result["secondary_artifacts"], [])
        self.assertEqual(record.source_event["profile_snapshot"]["lane"], "professional")

    def test_service_fetches_saved_capture(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Teacher workflow note",
                "framing_line": "Pulled from your product reflection.",
                "key_point": "Start by clarifying the single change that reduces user friction first.",
                "next_step": "Clarify the first workflow before broadening scope.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the product direction. She thinks the system feels too generic "
                "because it is trying to do too much at once instead of making one strong call. "
                "Action: revise the result card this week."
            ),
            context_hint="product review",
        )

        fetched = service.get_capture("user-1", record.capture_id)
        self.assertEqual(fetched["capture_id"], record.capture_id)
        self.assertEqual(fetched["result"]["route_kind"], "direct_professional_note")

    def test_service_distinguishes_weak_saved_notes_from_ambiguous_ones(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Unused",
                "framing_line": "Unused",
                "key_point": "Unused",
                "next_step": "Unused",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        weak_record = service.create_text_capture(
            uid="user-1",
            source_text="follow up tomorrow",
            context_hint="",
        )
        ambiguous_record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Not sure what this should become. Something about the product direction I think. "
                "Could be a note to myself, a follow-up, or maybe just something to hold onto."
            ),
            context_hint="",
        )

        self.assertEqual(weak_record.result["route_kind"], "saved_note")
        self.assertEqual(weak_record.result["saved_note_artifact"]["state"], "weak_signal")
        self.assertEqual(
            weak_record.result["saved_note_artifact"]["status"],
            "Saved as a small note",
        )
        self.assertEqual(
            weak_record.result["saved_note_artifact"]["metadata_line"],
            "Pasted note · Jun 27, 2026",
        )
        self.assertEqual(
            [section["label"] for section in weak_record.result["saved_note_artifact"]["sections"]],
            ["Next step"],
        )
        self.assertIn(
            "Follow up tomorrow",
            weak_record.result["saved_note_artifact"]["sections"][0]["text"],
        )
        self.assertEqual(ambiguous_record.result["route_kind"], "saved_note")
        self.assertEqual(ambiguous_record.result["saved_note_artifact"]["state"], "needs_direction")
        self.assertEqual(
            ambiguous_record.result["saved_note_artifact"]["status"],
            "Saved, needs direction",
        )
        self.assertEqual(
            ambiguous_record.result["saved_note_artifact"]["metadata_line"],
            "Pasted note · Jun 27, 2026",
        )
        self.assertEqual(
            [section["label"] for section in ambiguous_record.result["saved_note_artifact"]["sections"]],
            ["Could become"],
        )
        self.assertIn(
            "product direction",
            ambiguous_record.result["saved_note_artifact"]["sections"][0]["text"].lower(),
        )
        self.assertNotEqual(
            weak_record.result["saved_note_artifact"]["framing_line"],
            ambiguous_record.result["saved_note_artifact"]["framing_line"],
        )

    def test_service_sharpens_generic_product_note_output(self):
        repo = FakeRepository()

        def generic_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Memnon Workflows note",
                "framing_line": "Shaped from your note into one practical artifact to review.",
                "key_point": "The note already points toward one useful direction and is worth shaping into a concrete next step.",
                "next_step": "Clarify the single action this note is meant to support before expanding scope.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=generic_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "I’m thinking about Memnon workflows and the Granola teardown. "
                "The big insight is that Granola splits smartness from durability. "
                "Chat gives the better interpretation, but Quick Note creates the saved object. "
                "Memnon should make the best generated result also be the durable saved artifact."
            ),
            context_hint="product review",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(record.result["route_kind"], "direct_professional_note")
        self.assertIn("Granola", artifact["title"])
        self.assertNotEqual(artifact["title"], "Memnon Workflows note")
        self.assertIn(
            "splits smartness from durability",
            artifact["sections"][0]["text"].lower(),
        )
        self.assertIn("saved artifact", artifact["sections"][1]["text"].lower())

    def test_service_preserves_actual_ambiguity_for_saved_notes(self):
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

        record = service.create_text_capture(
            uid="user-1",
            source_text="This could be a reflection, but it also might be something I need to send to Kyle.",
            context_hint="",
        )

        artifact = record.result["saved_note_artifact"]
        self.assertEqual(artifact["state"], "needs_direction")
        self.assertIn("reflection", artifact["title"].lower())
        self.assertIn("kyle", artifact["title"].lower())
        self.assertIn("reflection", artifact["sections"][0]["text"].lower())
        self.assertIn("kyle", artifact["sections"][0]["text"].lower())

    def test_service_shapes_document_like_paste_into_primary_artifact(self):
        repo = FakeRepository()

        def generic_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Professional note",
                "framing_line": "Shaped from your note into one practical artifact to review.",
                "key_point": "The note already points toward one useful direction and is worth shaping into a concrete next step.",
                "next_step": "Be able to toggle this calendar on and off as needed",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=generic_ai,
            now_provider=lambda: "2026-06-28T10:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "RAI Interactive Journal for the 2026 Responsible AI Fellowship. "
                "Meeting agendas, feedback on talks, maybe a few open questions for the cohort, "
                "and notes that could shape the next session. "
                "Themes include what resonated, what felt unresolved, and what to revisit. "
                "Be able to toggle this calendar on and off as needed."
            ),
            context_hint="My Thread for the 2026 Responsible AI Fellowship",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(record.result["route_kind"], "direct_professional_note")
        self.assertIn("Responsible AI Fellowship", artifact["title"])
        self.assertIn("fellowship thread", artifact["sections"][0]["text"].lower())
        self.assertIn("agendas", artifact["sections"][0]["text"].lower())
        self.assertIn("feedback", artifact["sections"][0]["text"].lower())
        self.assertNotIn("looks like source material", artifact["sections"][0]["text"].lower())
        self.assertIn("consolidate", artifact["sections"][1]["text"].lower())
        self.assertNotIn("toggle this calendar", artifact["sections"][1]["text"].lower())
        self.assertIn("fellowship", artifact["framing_line"].lower())
        self.assertIn("reference", artifact["framing_line"].lower())


if __name__ == "__main__":
    unittest.main()
