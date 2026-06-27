import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.orchestration.models import AnalysisResult
from src.orchestration.engine import orchestrate_from_metadata


class OrchestrationEngineTests(unittest.TestCase):
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def write_metadata_fixture(self, root: Path, transcript: str) -> Path:
        transcript_path = root / "transcript.txt"
        transcript_path.write_text(transcript, encoding="utf-8")
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
        return metadata_path

    def orchestration_config(self, root: Path) -> dict:
        return {
            "runtime_dir": str(root / "runtime"),
            "orchestration": {
                "enabled": True,
                "runtime_dir": str(root / "runtime" / "orchestration"),
                "enable_llm_enrichment": False,
                "run_social_agent_cli": False,
            },
        }

    def test_orchestrate_from_metadata_writes_manifest_review_and_multiple_bundles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_path = self.write_metadata_fixture(
                root,
                "At BoulderJS, Kyle from Credible asked me to follow up with James next week. "
                "The demo showed how to go from messy data to trusted answers.",
            )
            config = self.orchestration_config(root)

            result = orchestrate_from_metadata(metadata_path, config)

            self.assertTrue(Path(result["manifest_path"]).exists())
            self.assertTrue(Path(result["review_queue_path"]).exists())
            self.assertGreaterEqual(len(result["artifact_bundles"]), 3)

    def test_orchestrate_from_metadata_preserves_analysis_llm_hints_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_path = self.write_metadata_fixture(
                root,
                "Kyle from Credible gave a concise professional update.",
            )
            config = self.orchestration_config(root)
            llm_hints = {
                "follow_up_bundle": {
                    "confidence": 0.91,
                    "reason": "llm classified explicit next step",
                }
            }
            analysis = AnalysisResult(
                event_type="professional_note",
                named_people=["Kyle"],
                named_orgs=["Credible"],
                professional_signals=["professional"],
                llm_hints=llm_hints,
            )

            with patch("src.orchestration.engine.analyze_source_event", return_value=analysis):
                result = orchestrate_from_metadata(metadata_path, config)

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            workflow_types = {
                job["workflow_type"] for job in manifest["workflow_jobs"]
            }

            self.assertEqual(manifest["llm_output"], llm_hints)
            self.assertIn("follow_up_bundle", workflow_types)

    def test_orchestrate_from_metadata_preserves_boulderjs_numbers_in_generated_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_path = self.write_metadata_fixture(
                root,
                "Kyle and James from Credible presented at BoulderJS about messy data and trusted answers.",
            )
            config = self.orchestration_config(root)
            config["orchestration"]["default_boulderjs_event_number"] = 30
            config["orchestration"]["default_boulderjs_talk_number"] = 41
            analysis = AnalysisResult(
                event_type="boulderjs_demo",
                named_people=["Kyle Nesbit"],
                named_orgs=["Credible", "BoulderJS"],
                publishable_angles=["context engineering for messy business data"],
                product_feedback=["go from messy data to trusted answers"],
            )

            with patch("src.orchestration.engine.analyze_source_event", return_value=analysis):
                result = orchestrate_from_metadata(metadata_path, config)

            boulderjs_bundle = next(
                bundle
                for bundle in result["artifact_bundles"]
                if bundle["workflow_type"] == "boulderjs_recap_packet"
            )
            packet_dir = Path(boulderjs_bundle["output_path"])

            self.assertEqual(
                json.loads((packet_dir / "event.json").read_text(encoding="utf-8"))["number"],
                30,
            )
            self.assertEqual(
                json.loads((packet_dir / "talk.json").read_text(encoding="utf-8"))["number"],
                41,
            )

    def test_orchestration_cli_help_succeeds_when_run_as_script_from_repo_root(self):
        result = subprocess.run(
            [sys.executable, "src/orchestration/cli.py", "--help"],
            cwd=self.repo_root(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
