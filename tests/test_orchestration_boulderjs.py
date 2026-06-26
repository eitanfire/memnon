import json
import tempfile
import unittest
from pathlib import Path

from src.orchestration.boulderjs import build_social_agent_command, write_boulderjs_recap_packet
from src.orchestration.models import AnalysisResult, SourceEvent, WorkflowJob


class BoulderJsPacketTests(unittest.TestCase):
    def test_packet_writer_creates_deterministic_files_and_optional_cli_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = str(Path(tmp) / "social-agent")
            config = {
                "boulderjs_dir": str(Path(tmp) / "boulderjs"),
                "social_agent_repo_dir": repo_dir,
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

            self.assertEqual(
                json.loads((packet_dir / "event.json").read_text(encoding="utf-8")),
                {
                    "number": 30,
                    "title": "Credible BoulderJS recap",
                    "url": "",
                    "date": "",
                    "time": "",
                    "location": "galvanize",
                    "description": "summary",
                },
            )
            self.assertEqual(
                json.loads((packet_dir / "talk.json").read_text(encoding="utf-8")),
                {
                    "number": 41,
                    "title": "Credible BoulderJS recap",
                    "url": "https://github.com/boulder-js/talks/issues/41",
                    "speaker": "Kyle Nesbit",
                    "speakerName": "Kyle Nesbit",
                    "speakerUrl": "",
                    "abstract": "go from messy data to trusted answers",
                    "labels": ["Talk: Feature 🎦"],
                },
            )
            self.assertEqual(
                (packet_dir / "abstract.txt").read_text(encoding="utf-8"),
                "go from messy data to trusted answers\n",
            )
            self.assertEqual(
                (packet_dir / "thoughts.txt").read_text(encoding="utf-8"),
                "context engineering for messy business data\n",
            )
            self.assertEqual(
                json.loads((packet_dir / "source-links.json").read_text(encoding="utf-8")),
                ["https://github.com/boulder-js/talks/issues/41"],
            )

            command = build_social_agent_command(packet_dir, config)
            self.assertEqual(
                command,
                [
                    "npm",
                    "--prefix",
                    repo_dir,
                    "run",
                    "draft",
                    "--",
                    "--event=30",
                    "--talk=41",
                    "--post-type=recap",
                    f"--talk-abstract-file={packet_dir / 'abstract.txt'}",
                    f"--thought-file={packet_dir / 'thoughts.txt'}",
                ],
            )

    def test_social_agent_command_returns_none_without_repo_dir(self):
        packet_dir = Path("/tmp/boulderjs/evt-1")

        self.assertIsNone(
            build_social_agent_command(
                packet_dir,
                {
                    "run_social_agent_cli": True,
                    "social_agent_repo_dir": "",
                    "default_boulderjs_event_number": 30,
                    "default_boulderjs_talk_number": 41,
                },
            )
        )

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
