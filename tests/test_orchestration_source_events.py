import json
import tempfile
import unittest
from pathlib import Path

from src.orchestration.analysis import analyze_source_event
from src.orchestration.source_events import load_source_event


class SourceEventAnalysisTests(unittest.TestCase):
    def test_load_source_event_reads_transcript_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript_path = root / "transcript.txt"
            metadata_path = root / "metadata.json"
            transcript_path.write_text(
                "At BoulderJS, Kyle from Credible asked me to follow up with James next week.",
                encoding="utf-8",
            )
            metadata_path.write_text(
                json.dumps(
                    {
                        "source_event_id": "evt-1",
                        "lane": "batch",
                        "workflow": "professional",
                        "routing_reason": "voice_label",
                        "title": "Credible follow-up",
                        "transcript_path": str(transcript_path),
                        "transcript_preview": "At BoulderJS...",
                        "note_path": str(root / "note.md"),
                        "archived_audio_path": str(root / "audio.m4a"),
                        "processed_at": "2026-06-26T10:00:00-06:00",
                        "summary": "summary",
                        "action_items": ["Follow up with James"],
                        "suggested_tags": ["boulderjs", "credible"],
                    }
                ),
                encoding="utf-8",
            )

            event = load_source_event(metadata_path)

            self.assertEqual(event.source_event_id, "evt-1")
            self.assertIn("Kyle", event.transcript)
            self.assertEqual(event.title, "Credible follow-up")

    def test_analyze_source_event_extracts_people_orgs_commitments_and_boulderjs_context(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript_path = root / "transcript.txt"
            transcript_path.write_text(
                "At BoulderJS, Kyle from Credible asked me to follow up with James next Thursday. "
                "The demo focused on turning messy data into trusted answers.",
                encoding="utf-8",
            )
            metadata_path = root / "metadata.json"
            metadata_path.write_text(
                json.dumps(
                    {
                        "source_event_id": "evt-1",
                        "lane": "batch",
                        "workflow": "professional",
                        "routing_reason": "voice_label",
                        "title": "Credible follow-up",
                        "transcript_path": str(transcript_path),
                        "transcript_preview": "At BoulderJS...",
                        "note_path": str(root / "note.md"),
                        "archived_audio_path": str(root / "audio.m4a"),
                        "processed_at": "2026-06-26T10:00:00-06:00",
                        "summary": "summary",
                        "action_items": [],
                        "suggested_tags": [],
                    }
                ),
                encoding="utf-8",
            )

            event = load_source_event(metadata_path)
            analysis = analyze_source_event(
                event,
                {"orchestration": {"enable_llm_enrichment": False}},
            )

            self.assertEqual(analysis.event_type, "boulderjs_demo")
            self.assertIn("Kyle", analysis.named_people)
            self.assertIn("James", analysis.named_people)
            self.assertIn("Credible", analysis.named_orgs)
            self.assertTrue(
                any("follow up" in item.lower() for item in analysis.commitments)
            )
            self.assertTrue(
                any("trusted answers" in item.lower() for item in analysis.publishable_angles)
            )


if __name__ == "__main__":
    unittest.main()
