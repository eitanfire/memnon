import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.routing import build_source_event, route_text_capture


class WorkflowRoutingTests(unittest.TestCase):
    def test_tiny_capture_is_saved_as_a_small_note(self):
        decision = route_text_capture("follow up tomorrow", "", {"lane": "professional", "profession": "teacher"})

        self.assertEqual(decision.route_kind, "saved_note")
        self.assertEqual(decision.primary_artifact_kind, "")
        self.assertEqual(decision.saved_note_state, "weak_signal")
        self.assertLessEqual(len(decision.secondary_artifact_kinds), 1)
        self.assertIn("Saved", decision.interpretation_line)

    def test_ambiguous_capture_is_saved_as_needing_light_direction(self):
        decision = route_text_capture(
            "Not sure what this should become. Something about the product direction I think. "
            "Could be a note to myself, a follow-up, or maybe just something to hold onto.",
            "",
            {"lane": "professional", "profession": "teacher"},
        )

        self.assertEqual(decision.route_kind, "saved_note")
        self.assertEqual(decision.primary_artifact_kind, "")
        self.assertEqual(decision.saved_note_state, "needs_direction")
        self.assertIn("direction", decision.interpretation_line.lower())

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
        self.assertEqual(decision.saved_note_state, "needs_direction")

    def test_long_document_like_capture_with_light_ambiguity_still_routes_to_primary_artifact(self):
        decision = route_text_capture(
            (
                "RAI Interactive Journal for the 2026 Responsible AI Fellowship. "
                "Meeting agendas, feedback on talks, maybe a few open questions for the cohort, "
                "and notes that could shape the next session. "
                "Themes include what resonated, what felt unresolved, and what to revisit."
            ),
            "My Thread for the 2026 Responsible AI Fellowship",
            {"lane": "professional", "profession": "professional"},
        )

        self.assertEqual(decision.route_kind, "direct_professional_note")
        self.assertEqual(decision.primary_artifact_kind, "professional_note")
        self.assertEqual(decision.saved_note_state, "")

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
