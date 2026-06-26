import json
import tempfile
import unittest
from pathlib import Path

from src.orchestration.engine import orchestrate_from_metadata


class OrchestrationEngineTests(unittest.TestCase):
    def test_orchestrate_from_metadata_writes_manifest_review_and_multiple_bundles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript_path = root / "transcript.txt"
            transcript_path.write_text(
                "At BoulderJS, Kyle from Credible asked me to follow up with James next week. "
                "The demo showed how to go from messy data to trusted answers.",
                encoding="utf-8",
            )
            metadata_path = root / "metadata.json"
            metadata_path.write_text(json.dumps({
                "source_event_id": "evt-1",
                "entry_id": "evt-1",
                "lane": "batch",
                "workflow": "professional",
                "routing_reason": "voice_label",
                "title": "Credible BoulderJS recap",
                "transcript_path": str(transcript_path),
                "transcript_preview": "At BoulderJS...",
                "note_path": str(root / "note.md"),
                "archived_audio_path": str(root / "audio.m4a"),
                "processed_at": "2026-06-26T10:00:00-06:00",
                "summary": "summary",
                "action_items": ["Follow up with James"],
                "suggested_tags": ["boulderjs", "credible"],
                "source_links": ["https://github.com/boulder-js/talks/issues/41"],
            }), encoding="utf-8")

            config = {
                "runtime_dir": str(root / "runtime"),
                "orchestration": {
                    "enabled": True,
                    "runtime_dir": str(root / "runtime" / "orchestration"),
                    "enable_llm_enrichment": False,
                    "run_social_agent_cli": False,
                },
            }

            result = orchestrate_from_metadata(metadata_path, config)

            self.assertTrue(Path(result["manifest_path"]).exists())
            self.assertTrue(Path(result["review_queue_path"]).exists())
            self.assertGreaterEqual(len(result["artifact_bundles"]), 3)


if __name__ == "__main__":
    unittest.main()
