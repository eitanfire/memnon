import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.contextual_suggestions import evaluate_contextual_suggestions


def build_record(
    source_text: str,
    *,
    title: str = "Saved result",
    framing_line: str = "Shaped from your note into one practical artifact.",
    route_kind: str = "direct_professional_note",
    input_type: str = "text",
    transcript_quality: str | None = None,
    origin: str = "original_capture",
    saved_note_state: str = "",
):
    event_manifest = {
        "transcript_quality": {},
        "contextual_suggestions": {
            "origin": origin,
        },
    }
    if transcript_quality:
        event_manifest["transcript_quality"] = {"quality": transcript_quality}
    return {
        "capture_id": "cap-1",
        "input_type": input_type,
        "source_event": {
            "input_type": input_type,
            "source_text": source_text,
            "source_preview": source_text[:140],
            "context_hint": "",
        },
        "event_manifest": event_manifest,
        "result": {
            "route_kind": route_kind,
            "primary_artifact": {
                "title": title,
                "framing_line": framing_line,
                "sections": [],
            } if route_kind != "saved_note" else None,
            "saved_note_artifact": {
                "state": saved_note_state,
            } if route_kind == "saved_note" else None,
        },
    }


class ContextualSuggestionHeuristicTests(unittest.TestCase):
    def test_social_post_suggestion_requires_clear_public_event_signal(self):
        record = build_record(
            (
                "BoulderJS meetup recap: thanks to everyone who came tonight. "
                "Share the highlights, invite the community to next week's event, "
                "and post the announcement for members who missed it."
            ),
            title="BoulderJS meetup recap",
        )

        meta = evaluate_contextual_suggestions(record)

        self.assertEqual(meta["shown_types"], ["draft_social_post"])
        self.assertIn("analyze_professionally", meta["suppression_reasons"])

    def test_professional_analysis_requires_clear_professional_input_signal(self):
        record = build_record(
            (
                "Podcast notes on product strategy and market positioning. "
                "The transcript focused on onboarding friction, retention patterns, "
                "and how pricing changes affect activation."
            ),
            title="Product strategy podcast notes",
        )

        meta = evaluate_contextual_suggestions(record)

        self.assertEqual(meta["shown_types"], ["analyze_professionally"])
        self.assertIn("draft_social_post", meta["suppression_reasons"])

    def test_private_or_logistical_captures_are_suppressed_even_with_keyword_overlap(self):
        record = build_record(
            (
                "Need to register the kids for school, coordinate dinner, "
                "and figure out pickup logistics for the family this week."
            ),
            title="Family logistics",
        )

        meta = evaluate_contextual_suggestions(record)

        self.assertEqual(meta["shown_types"], [])
        self.assertIn("draft_social_post", meta["suppression_reasons"])
        self.assertIn("analyze_professionally", meta["suppression_reasons"])

    def test_ambiguous_or_emotional_capture_is_suppressed(self):
        record = build_record(
            (
                "I'm not sure what this means yet. "
                "I feel raw about the conversation and maybe just need to sit with it."
            ),
            title="Need to sit with this",
        )

        meta = evaluate_contextual_suggestions(record)

        self.assertEqual(meta["shown_types"], [])

    def test_saved_note_routes_do_not_trigger_suggestions(self):
        record = build_record(
            "Hold onto this thought for later.",
            route_kind="saved_note",
            saved_note_state="needs_direction",
        )

        meta = evaluate_contextual_suggestions(record)

        self.assertEqual(meta["shown_types"], [])

    def test_noisy_voice_capture_is_suppressed(self):
        record = build_record(
            (
                "Okay so anyway this thing about maybe strategy but also random filler "
                "and the transcript is messy and incomplete."
            ),
            input_type="voice",
            transcript_quality="mixed",
        )

        meta = evaluate_contextual_suggestions(record)

        self.assertEqual(meta["shown_types"], [])

    def test_derived_result_origin_never_generates_further_suggestions(self):
        record = build_record(
            "Public event update for the community with a clear invitation to join the meetup.",
            title="Meetup update",
            origin="derived_result",
        )

        meta = evaluate_contextual_suggestions(record)

        self.assertEqual(meta["shown_types"], [])


if __name__ == "__main__":
    unittest.main()
