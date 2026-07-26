import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.continuity_bridge import build_daily_feed_continuity_note


def _capture_record(artifact: dict, *, source_preview: str = "") -> dict:
    return {
        "capture_id": "cap-1",
        "created_at": "2026-07-25T10:00:00Z",
        "source_event": {"input_type": "file", "source_preview": source_preview},
        "result": {"primary_artifact": artifact, "interpretation_line": "Saved as a note."},
    }


class ContinuityBridgeTests(unittest.TestCase):
    def test_insight_prefers_summary_field_over_legacy_key_point_section(self):
        artifact = {
            "title": "Mentorship program overview",
            "summary": "The mentorship program spans two semesters and builds peer-teaching habits.",
            "source_excerpt": "You know, like we're at this time.",
            "sections": [{"label": "Key point", "text": "stale legacy section text"}],
        }
        note = build_daily_feed_continuity_note(
            capture_record=_capture_record(artifact),
            profile={},
            include_teaching_context=None,
        )
        self.assertEqual(
            note["insight"],
            "The mentorship program spans two semesters and builds peer-teaching habits.",
        )

    def test_insight_falls_back_to_source_excerpt_when_summary_is_missing(self):
        artifact = {
            "title": "Saved note",
            "source_excerpt": "A real verbatim sentence from the transcript.",
        }
        note = build_daily_feed_continuity_note(
            capture_record=_capture_record(artifact),
            profile={},
            include_teaching_context=None,
        )
        self.assertEqual(note["insight"], "A real verbatim sentence from the transcript.")

    def test_summary_field_prefers_framing_line_then_artifact_summary(self):
        artifact = {
            "title": "Saved note",
            "summary": "Real synthesized summary content.",
        }
        note = build_daily_feed_continuity_note(
            capture_record=_capture_record(artifact),
            profile={},
            include_teaching_context=None,
        )
        self.assertEqual(note["summary"], "Real synthesized summary content.")


if __name__ == "__main__":
    unittest.main()
