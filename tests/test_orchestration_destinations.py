import json
import tempfile
import unittest
from pathlib import Path

from src.orchestration.destinations import (
    write_artifact_bundle,
    write_event_manifest,
    write_review_queue_entry,
)
from src.orchestration.models import AnalysisResult, SourceEvent, WorkflowJob


class OrchestrationDestinationsTests(unittest.TestCase):
    def test_follow_up_and_research_outputs_are_file_based_and_reviewable(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "runtime_dir": tmp,
                "manifests_dir": str(Path(tmp) / "manifests"),
                "review_queue_dir": str(Path(tmp) / "review-queue"),
                "outbox_dir": str(Path(tmp) / "outbox"),
                "research_dir": str(Path(tmp) / "research"),
                "note_bundles_dir": str(Path(tmp) / "note-bundles"),
                "boulderjs_dir": str(Path(tmp) / "boulderjs"),
            }
            event = SourceEvent(
                source_event_id="evt-1",
                lane="batch",
                workflow="professional",
                routing_reason="voice_label",
                title="Credible recap",
                transcript="Kyle asked me to follow up with James and share district-signal.",
                transcript_path="/tmp/transcript.txt",
                transcript_preview="Kyle asked...",
                note_path="/tmp/note.md",
                archived_audio_path="/tmp/audio.m4a",
                metadata_path="/tmp/metadata.json",
                processed_at="2026-06-26T10:00:00-06:00",
                summary="summary",
                action_items=["Follow up with James"],
                suggested_tags=["credible"],
            )
            analysis = AnalysisResult(
                event_type="professional_note",
                named_people=["Kyle", "James"],
                named_orgs=["Credible"],
                commitments=["follow up with James"],
                follow_up_requests=["follow up with James"],
                product_feedback=["district-signal looked promising"],
                research_signals=["education data is fragmented"],
                publishable_angles=[],
                reflection_signals=[],
                professional_signals=["professional"],
            )
            jobs = [
                WorkflowJob("professional_note_bundle", 1.0, "rule", "ready", "runtime"),
                WorkflowJob("research_note", 0.72, "research signal", "ready", "runtime", needs_review=True),
                WorkflowJob("follow_up_bundle", 0.91, "explicit ask", "ready", "runtime"),
            ]

            bundles = [write_artifact_bundle(job, event, analysis, config) for job in jobs]
            manifest_path = write_event_manifest(
                event, analysis, [], ["professional_note_bundle"], {}, jobs, bundles, config
            )
            first_review_path = write_review_queue_entry(event, jobs, bundles, config)
            second_review_path = write_review_queue_entry(event, jobs, bundles, config)

            manifest_dir = Path(config["manifests_dir"]) / event.source_event_id
            review_dir = Path(config["review_queue_dir"]) / event.source_event_id
            history_files = sorted((review_dir / "history").glob("*.json"))

            self.assertEqual(
                manifest_path,
                str(manifest_dir / "event_manifest.json"),
            )
            self.assertEqual(
                first_review_path,
                str(review_dir / "review_queue_entry.json"),
            )
            self.assertEqual(
                second_review_path,
                str(review_dir / "review_queue_entry.json"),
            )
            self.assertTrue(Path(manifest_path).exists())
            self.assertTrue(Path(first_review_path).exists())
            self.assertTrue(any(path.endswith("email.md") for bundle in bundles for path in bundle.files))
            self.assertEqual(len(history_files), 2)
            payload = json.loads(Path(first_review_path).read_text(encoding="utf-8"))
            self.assertTrue(payload["external_drafts_pending"])
            self.assertIn("research_note", {job["workflow_type"] for job in payload["workflow_jobs"]})
            self.assertEqual(
                payload,
                json.loads(history_files[-1].read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
