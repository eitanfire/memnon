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
        self.assertFalse(orchestration["enable_llm_enrichment"])

    def test_build_orchestration_config_resolves_relative_runtime_dir_from_config_dir(self):
        config = {
            "_config_dir": "/tmp/memnon-config",
            "runtime_dir": "/tmp/memnon-runtime",
            "orchestration": {"runtime_dir": "./custom-orchestration"},
            "ai": {"enabled": False, "backend": "mock"},
            "transcription": {"backend": "mock"},
        }

        orchestration = build_orchestration_config(config)

        self.assertEqual(
            orchestration["runtime_dir"],
            "/tmp/memnon-config/custom-orchestration",
        )
        self.assertEqual(
            orchestration["review_queue_dir"],
            "/tmp/memnon-config/custom-orchestration/review-queue",
        )

    def test_write_metadata_persists_canonical_note_fields(self):
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
                source_path=source,
                archived_audio_path=archived,
                note_path=note,
                transcript="hello world",
                transcript_path=transcript,
                ai_payload={"title": "District Signal", "summary": "", "action_items": [], "suggested_tags": []},
                gpt_packet_path=None,
                entry_id="abc123",
                processors_run=["transcript"],
                processor_failures=[],
            )

            payload = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["id"], "abc123")
            self.assertEqual(payload["title"], "District Signal")
            self.assertEqual(payload["transcript_path"], str(transcript))
            self.assertEqual(payload["source_audio_name"], "raw.m4a")
            self.assertEqual(payload["source_audio_archive_path"], str(archived))
            self.assertEqual(payload["processors_run"], ["transcript"])
            self.assertEqual(payload["processor_failures"], [])
            self.assertNotIn("workflow", payload)
            self.assertNotIn("routing_reason", payload)
            self.assertNotIn("lane", payload)
            self.assertNotIn("workflow_mode", payload)

    def test_review_queue_schema_reuses_workflow_job_shape(self):
        review_queue_schema = json.loads(
            Path("schemas/review-queue-entry.schema.json").read_text(encoding="utf-8")
        )
        workflow_job_schema = json.loads(
            Path("schemas/workflow-job.schema.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            review_queue_schema["properties"]["workflow_jobs"]["items"],
            {"$ref": "./workflow-job.schema.json"},
        )

        job = WorkflowJob(
            workflow_type="professional_note_bundle",
            confidence=0.9,
            reason="matched workflow",
            status="queued",
            destination="/tmp/out",
        ).to_dict()

        self.assertEqual(
            sorted(job.keys()),
            sorted(workflow_job_schema["properties"].keys()),
        )


if __name__ == "__main__":
    unittest.main()
