import json
import tempfile
import unittest
from pathlib import Path

from src.orchestration.config import build_orchestration_config
from src.orchestration.models import WorkflowJob
from src.voice_pipeline import write_metadata


class OrchestrationContractsTests(unittest.TestCase):
    def test_build_orchestration_config_sets_runtime_defaults(self):
        config = {
            "runtime_dir": "/tmp/memnon-runtime",
            "ai": {"enabled": False, "backend": "mock"},
            "transcription": {"backend": "mock"},
        }

        orchestration = build_orchestration_config(config)

        self.assertFalse(orchestration["enabled"])
        self.assertEqual(
            orchestration["runtime_dir"],
            "/tmp/memnon-runtime/orchestration",
        )
        self.assertEqual(
            orchestration["review_queue_dir"],
            "/tmp/memnon-runtime/orchestration/review-queue",
        )

    def test_write_metadata_persists_orchestration_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "runtime_dir": str(root / "runtime"),
                "ai": {"enabled": False, "backend": "mock"},
                "transcription": {"backend": "mock"},
            }
            source = root / "raw.m4a"
            archived = root / "processed.m4a"
            note = root / "note.md"
            transcript = root / "transcript.txt"
            source.write_text("source", encoding="utf-8")
            archived.write_text("archived", encoding="utf-8")
            note.write_text("note", encoding="utf-8")
            transcript.write_text("hello world", encoding="utf-8")

            metadata_path = write_metadata(
                config=config,
                lane="batch",
                source_path=source,
                archived_audio_path=archived,
                note_path=note,
                transcript="hello world",
                transcript_path=transcript,
                ai_payload={"title": "District Signal", "summary": "", "action_items": [], "suggested_tags": []},
                gpt_packet_path=None,
                workflow="professional",
                routing_reason="voice_label",
                entry_id="abc123",
            )

            payload = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["title"], "District Signal")
            self.assertEqual(payload["workflow"], "professional")
            self.assertEqual(payload["routing_reason"], "voice_label")
            self.assertEqual(payload["entry_id"], "abc123")
            self.assertEqual(payload["transcript_path"], str(transcript))
            self.assertEqual(payload["source_event_id"], "abc123")


if __name__ == "__main__":
    unittest.main()
