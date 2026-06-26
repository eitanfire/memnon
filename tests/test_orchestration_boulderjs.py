import json
import tempfile
import unittest
from pathlib import Path

from src.orchestration.boulderjs import build_social_agent_command, write_boulderjs_recap_packet
from src.orchestration.models import AnalysisResult, SourceEvent, WorkflowJob


class BoulderJsPacketTests(unittest.TestCase):
    def test_packet_writer_creates_deterministic_files_and_optional_cli_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "boulderjs_dir": str(Path(tmp) / "boulderjs"),
                "social_agent_repo_dir": "/Users/eitan/.openclaw/workspace/boulderjs-social-agent",
                "run_social_agent_cli": True,
                "default_boulderjs_event_number": 30,
                "default_boulderjs_talk_number": 41,
            }
            event = SourceEvent(
                source_event_id="evt-1",
                lane="batch",
                workflow="professional",
                routing_reason="voice_label",
                title="Credible BoulderJS recap",
                transcript="Kyle and James from Credible presented at BoulderJS about messy data and trusted answers.",
                transcript_path="/tmp/transcript.txt",
                transcript_preview="Kyle and James...",
                note_path="/tmp/note.md",
                archived_audio_path="/tmp/audio.m4a",
                metadata_path="/tmp/metadata.json",
                processed_at="2026-06-26T10:00:00-06:00",
                summary="summary",
                action_items=[],
                suggested_tags=["boulderjs", "credible"],
                source_links=["https://github.com/boulder-js/talks/issues/41"],
            )
            analysis = AnalysisResult(
                event_type="boulderjs_demo",
                named_people=["Kyle Nesbit", "James Swirhun"],
                named_orgs=["Credible", "BoulderJS"],
                publishable_angles=["context engineering for messy business data"],
                product_feedback=["go from messy data to trusted answers"],
            )
            job = WorkflowJob("boulderjs_recap_packet", 1.0, "hard rule", "ready", "runtime")

            bundle = write_boulderjs_recap_packet(job, event, analysis, config)
            packet_dir = Path(bundle.output_path)

            self.assertTrue((packet_dir / "event.json").exists())
            self.assertTrue((packet_dir / "talk.json").exists())
            self.assertTrue((packet_dir / "abstract.txt").exists())
            self.assertTrue((packet_dir / "thoughts.txt").exists())
            self.assertTrue((packet_dir / "source-links.json").exists())

            command = build_social_agent_command(packet_dir, config)
            self.assertEqual(command[:4], ["npm", "run", "draft", "--"])

    def test_boulderjs_packet_schema_matches_adapter_contract(self):
        schema = json.loads(
            Path("schemas/boulderjs-recap-packet.schema.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            sorted(schema["properties"].keys()),
            ["abstract.txt", "event.json", "source-links.json", "talk.json", "thoughts.txt"],
        )
        self.assertEqual(schema["required"], ["event.json", "talk.json", "abstract.txt", "thoughts.txt", "source-links.json"])
        self.assertEqual(schema["properties"]["source-links.json"]["type"], "array")


if __name__ == "__main__":
    unittest.main()
