import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.routing import build_source_event, route_text_capture


class WorkflowRoutingTests(unittest.TestCase):
    def test_short_capture_is_saved_as_note(self):
        decision = route_text_capture("Need to think more.", "", {"lane": "professional", "profession": "teacher"})

        self.assertEqual(decision.route_kind, "saved_note")
        self.assertEqual(decision.primary_artifact_kind, "")
        self.assertLessEqual(len(decision.secondary_artifact_kinds), 1)
        self.assertIn("Saved", decision.interpretation_line)

    def test_richer_capture_routes_directly_to_one_professional_note(self):
        decision = route_text_capture(
            "I want to shape a clearer product note about the teacher workflow, the friction in the UI, "
            "and the next concrete change to make this week.",
            "product review",
            {"lane": "professional", "profession": "teacher"},
        )

        self.assertEqual(decision.route_kind, "direct_professional_note")
        self.assertEqual(decision.primary_artifact_kind, "professional_note")
        self.assertEqual(decision.secondary_artifact_kinds, [])

    def test_reflect_profile_bias_can_keep_borderline_capture_as_note(self):
        decision = route_text_capture(
            "I am trying to make sense of how yesterday felt and what I should carry into tomorrow.",
            "",
            {"lane": "reflect", "profession": "personal reflection"},
        )

        self.assertEqual(decision.route_kind, "saved_note")
        self.assertEqual(decision.primary_artifact_kind, "")

    def test_source_event_keeps_source_text_and_secondary_preview(self):
        event = build_source_event(
            source_text="This is a long enough note to build a preview from without exposing everything at first glance.",
            context_hint="product idea",
            capture_id="cap-123",
            created_at="2026-06-27T10:15:00Z",
        )

        self.assertEqual(event["capture_id"], "cap-123")
        self.assertEqual(event["input_type"], "text")
        self.assertIn("source_text", event)
        self.assertIn("source_preview", event)
        self.assertLessEqual(len(event["likely_themes"]), 6)


if __name__ == "__main__":
    unittest.main()
