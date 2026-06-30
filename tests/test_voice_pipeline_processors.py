import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.voice_pipeline import process_file, process_pending


class VoicePipelineProcessorTests(unittest.TestCase):
    def build_config(self, root: Path) -> dict:
        return {
            "runtime_dir": str(root / "runtime"),
            "state_file": str(root / "runtime" / "state.json"),
            "raw_audio_dir": str(root / "raw"),
            "archive_audio_dir": str(root / "processed"),
            "failed_audio_dir": str(root / "failed"),
            "obsidian_inbox_dir": str(root / "obsidian"),
            "note_template_path": str(Path("templates/voice-note.md").resolve()),
            "min_audio_bytes": 1,
            "min_transcript_words": 1,
            "min_stable_age_seconds": 0,
            "audio_extensions": [".m4a"],
            "archive_subdirs_by_date": True,
            "transcription": {
                "backend": "mock",
                "mock_transcript": "Need to follow up with Maya tomorrow.",
            },
            "ai": {
                "enabled": True,
                "backend": "mock",
            },
            "gpt_handoff": {
                "enabled": False,
                "copy_to_clipboard": False,
            },
        }

    def build_source_file(self, root: Path, name: str = "note.m4a") -> Path:
        source = root / "raw" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"audio-bytes")
        return source

    def load_metadata(self, path: str) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def test_processors_record_success_when_summary_and_actions_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            source = self.build_source_file(root)

            with patch(
                "src.voice_pipeline.run_summary_processor",
                return_value={
                    "title": "Follow up with Maya",
                    "summary": "The speaker needs to follow up with Maya tomorrow.",
                    "suggested_tags": ["follow-up"],
                },
            ), patch(
                "src.voice_pipeline.run_action_items_processor",
                return_value={"action_items": ["Follow up with Maya tomorrow."]},
            ):
                result = process_file(config, source)

            self.assertEqual(result.status, "done")
            metadata = self.load_metadata(result.metadata_path)
            self.assertEqual(metadata["processors_run"], ["transcript", "summary", "action_items"])
            self.assertEqual(metadata["processor_failures"], [])
            self.assertIn("id", metadata)
            self.assertIn("created", metadata)
            self.assertIn("ingested_at", metadata)
            self.assertIn("processed_at", metadata)
            self.assertIn("source_audio_hash", metadata)
            self.assertIn("source_audio_archive_path", metadata)
            self.assertIn("transcription_version", metadata)
            self.assertNotIn("workflow", metadata)
            self.assertNotIn("routing_reason", metadata)
            self.assertNotIn("lane", metadata)

            # Note content is the visible contract.
            note_text = Path(result.note_path).read_text(encoding="utf-8")
            self.assertIn('  - "summary"', note_text)
            self.assertIn('  - "action_items"', note_text)
            self.assertIn(f"source_audio_path: {result.archived_audio_path}", note_text)
            self.assertEqual(metadata["source_audio_archive_path"], result.archived_audio_path)

    def test_summary_failure_still_writes_valid_note_with_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            source = self.build_source_file(root)

            with patch(
                "src.voice_pipeline.run_summary_processor",
                side_effect=RuntimeError("summary model request failed"),
            ), patch(
                "src.voice_pipeline.run_action_items_processor",
                return_value={"action_items": ["Follow up with Maya tomorrow."]},
            ):
                result = process_file(config, source)

            self.assertEqual(result.status, "done")
            note_text = Path(result.note_path).read_text(encoding="utf-8")
            self.assertIn("## Transcript", note_text)
            self.assertIn("Need to follow up with Maya tomorrow.", note_text)
            self.assertIn("_No AI summary generated._", note_text)

            metadata = self.load_metadata(result.metadata_path)
            self.assertEqual(metadata["processors_run"], ["transcript", "action_items"])
            self.assertEqual(len(metadata["processor_failures"]), 1)
            self.assertEqual(metadata["processor_failures"][0]["processor"], "summary")
            self.assertEqual(metadata["processor_failures"][0]["reason"], "summary model request failed")
            self.assertTrue(metadata["processor_failures"][0]["failed_at"])

    def test_action_items_failure_still_writes_valid_note_with_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            source = self.build_source_file(root)

            with patch(
                "src.voice_pipeline.run_summary_processor",
                return_value={
                    "title": "Follow up with Maya",
                    "summary": "The speaker needs to follow up with Maya tomorrow.",
                    "suggested_tags": ["follow-up"],
                },
            ), patch(
                "src.voice_pipeline.run_action_items_processor",
                side_effect=RuntimeError("action extraction failed"),
            ):
                result = process_file(config, source)

            self.assertEqual(result.status, "done")
            note_text = Path(result.note_path).read_text(encoding="utf-8")
            self.assertIn("## Transcript", note_text)
            self.assertIn("Need to follow up with Maya tomorrow.", note_text)
            self.assertIn("- [ ] None extracted", note_text)

            metadata = self.load_metadata(result.metadata_path)
            self.assertEqual(metadata["processors_run"], ["transcript", "summary"])
            self.assertEqual(len(metadata["processor_failures"]), 1)
            self.assertEqual(metadata["processor_failures"][0]["processor"], "action_items")
            self.assertEqual(metadata["processor_failures"][0]["reason"], "action extraction failed")
            self.assertTrue(metadata["processor_failures"][0]["failed_at"])

    def test_empty_action_items_still_counts_as_successful_processor_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            source = self.build_source_file(root)

            with patch(
                "src.voice_pipeline.run_summary_processor",
                return_value={
                    "title": "Quiet note",
                    "summary": "The note contains no explicit tasks.",
                    "suggested_tags": [],
                },
            ), patch(
                "src.voice_pipeline.run_action_items_processor",
                return_value={"action_items": []},
            ):
                result = process_file(config, source)

            self.assertEqual(result.status, "done")
            metadata = self.load_metadata(result.metadata_path)
            self.assertEqual(metadata["processors_run"], ["transcript", "summary", "action_items"])
            self.assertEqual(metadata["processor_failures"], [])
            self.assertEqual(metadata["action_items"], [])

    def test_default_v1_path_does_not_call_detect_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            source = self.build_source_file(root)

            with patch(
                "src.voice_pipeline.detect_workflow",
                side_effect=AssertionError("detect_workflow should not be called in v1 default path"),
            ), patch(
                "src.voice_pipeline.run_summary_processor",
                return_value={
                    "title": "Follow up with Maya",
                    "summary": "The speaker needs to follow up with Maya tomorrow.",
                    "suggested_tags": ["follow-up"],
                },
            ), patch(
                "src.voice_pipeline.run_action_items_processor",
                return_value={"action_items": ["Follow up with Maya tomorrow."]},
            ):
                result = process_file(config, source)

            self.assertEqual(result.status, "done")

    def test_archive_move_failure_does_not_leave_stale_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            source = self.build_source_file(root)
            real_move = shutil.move

            def conditional_move(src: str, dst: str):
                if "/processed/" in dst:
                    raise RuntimeError("archive move failed")
                return real_move(src, dst)

            with patch(
                "src.voice_pipeline.run_summary_processor",
                return_value={
                    "title": "Follow up with Maya",
                    "summary": "The speaker needs to follow up with Maya tomorrow.",
                    "suggested_tags": ["follow-up"],
                },
            ), patch(
                "src.voice_pipeline.run_action_items_processor",
                return_value={"action_items": ["Follow up with Maya tomorrow."]},
            ), patch(
                "src.voice_pipeline.shutil.move",
                side_effect=conditional_move,
            ):
                result = process_file(config, source)

            self.assertEqual(result.status, "error")
            self.assertIsNone(result.note_path)
            self.assertIsNone(result.metadata_path)
            self.assertTrue(result.archived_audio_path)
            self.assertIn("/failed/", result.archived_audio_path)
            self.assertFalse(any((root / "obsidian").glob("*.md")))

    def test_process_pending_skips_duplicate_content_after_first_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            first = self.build_source_file(root, "first.m4a")
            second = self.build_source_file(root, "second.m4a")
            second.write_bytes(first.read_bytes())

            with patch(
                "src.voice_pipeline.run_summary_processor",
                return_value={
                    "title": "Follow up with Maya",
                    "summary": "The speaker needs to follow up with Maya tomorrow.",
                    "suggested_tags": ["follow-up"],
                },
            ), patch(
                "src.voice_pipeline.run_action_items_processor",
                return_value={"action_items": ["Follow up with Maya tomorrow."]},
            ):
                first_pass = process_pending(config)
                second_pass = process_pending(config)
                third_pass = process_pending(config)

            self.assertEqual(first_pass, [])
            self.assertEqual(len(second_pass), 1)
            self.assertEqual(second_pass[0].status, "done")
            self.assertEqual(third_pass, [])
            notes = list((root / "obsidian").glob("*.md"))
            self.assertEqual(len(notes), 1)

            state = json.loads((root / "runtime" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["content_hashes"]), 1)

    def test_process_pending_skips_unstable_file_until_next_poll(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            source = self.build_source_file(root)

            with patch(
                "src.voice_pipeline.run_summary_processor",
                return_value={
                    "title": "Follow up with Maya",
                    "summary": "The speaker needs to follow up with Maya tomorrow.",
                    "suggested_tags": ["follow-up"],
                },
            ), patch(
                "src.voice_pipeline.run_action_items_processor",
                return_value={"action_items": ["Follow up with Maya tomorrow."]},
            ):
                first_pass = process_pending(config)
                second_pass = process_pending(config)

            self.assertEqual(first_pass, [])
            self.assertEqual(len(second_pass), 1)
            self.assertEqual(second_pass[0].status, "done")

    def test_process_pending_skips_unreadable_file_without_failing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            source = self.build_source_file(root)

            with patch("src.voice_pipeline.is_locally_readable", return_value=False):
                results = process_pending(config)

            self.assertEqual(results, [])
            self.assertTrue(source.exists())
            self.assertFalse(any((root / "obsidian").glob("*.md")))
            self.assertFalse(any((root / "failed").glob("*")))

    def test_process_pending_moves_invalid_file_to_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.build_config(root)
            config["min_audio_bytes"] = 100
            source = self.build_source_file(root)

            first_pass = process_pending(config)
            second_pass = process_pending(config)

            self.assertEqual(first_pass, [])
            self.assertEqual(len(second_pass), 1)
            self.assertEqual(second_pass[0].status, "error")
            self.assertTrue(second_pass[0].archived_audio_path)
            self.assertIn("/failed/", second_pass[0].archived_audio_path)
            self.assertFalse(source.exists())
            self.assertFalse(any((root / "obsidian").glob("*.md")))


if __name__ == "__main__":
    unittest.main()
