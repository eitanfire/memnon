import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.voice_pipeline import write_note


class CanonicalNoteTests(unittest.TestCase):
    def test_write_note_omits_lane_and_workflow_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_dir = root / "obsidian"
            source = root / "voice-note.m4a"
            archived = root / "processed" / "voice-note.m4a"
            source.write_text("audio", encoding="utf-8")
            archived.parent.mkdir(parents=True, exist_ok=True)

            config = {
                "note_template_path": str(Path("templates/voice-note.md").resolve()),
                "obsidian_inbox_dir": str(obsidian_dir),
                "transcription": {"backend": "mock"},
                "ai": {"enabled": True, "backend": "mock"},
            }
            ai_payload = {
                "title": "Canonical note title",
                "summary": "Useful summary",
                "action_items": ["Do the thing"],
                "suggested_tags": ["canonical-note"],
            }

            note_path = write_note(
                config=config,
                source_path=source,
                archived_audio_path=archived,
                transcript="This is the transcript.",
                ai_payload=ai_payload,
                processors_run=["transcript", "summary", "action_items"],
                processor_failures=[],
                created_at=datetime.fromtimestamp(source.stat().st_mtime).astimezone().replace(microsecond=0),
            )

            text = note_path.read_text(encoding="utf-8")

            self.assertNotIn("\nworkflow:", text)
            self.assertNotIn("\nrouting_reason:", text)
            self.assertIn("processors_run:", text)
            self.assertIn("## Summary", text)
            self.assertIn("Useful summary", text)
            self.assertIn("## Transcript", text)
            self.assertIn("This is the transcript.", text)


if __name__ == "__main__":
    unittest.main()
