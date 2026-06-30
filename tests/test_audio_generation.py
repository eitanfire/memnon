import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path("/Users/eitan/memnon")
FUNCTIONS_DIR = REPO_ROOT / "functions"
AUDIO_PATH = FUNCTIONS_DIR / "audio_generation.py"


def load_audio_module():
    sys.path.insert(0, str(FUNCTIONS_DIR))
    spec = importlib.util.spec_from_file_location("memnon_audio_generation", AUDIO_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AudioGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audio = load_audio_module()

    def test_mix_narration_with_music_uses_true_end_fade_from_probed_duration(self):
        commands = []

        def fake_run(cmd, capture_output, text, check):
            commands.append(cmd)

            class Result:
                returncode = 0
                stderr = ""

            return Result()

        with (
            patch.object(self.audio, "_resolve_ffmpeg", return_value="/usr/bin/ffmpeg"),
            patch.object(self.audio, "_probe_audio_duration", return_value=120.0, create=True),
            patch.object(self.audio.subprocess, "run", side_effect=fake_run),
        ):
            output = self.audio._mix_narration_with_music(
                "narration.mp3",
                "music.mp3",
                "out.mp3",
                music_volume=0.12,
                fade_in_seconds=3,
                fade_out_seconds=5,
            )

        self.assertEqual(str(output), "out.mp3")
        self.assertEqual(len(commands), 1)
        filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
        self.assertIn("afade=t=in:st=0:d=3", filter_graph)
        self.assertIn("[mixed]afade=t=out:st=115.0:d=5[out]", filter_graph)

    def test_synthesize_daily_brief_bytes_uses_section_specific_fades(self):
        mix_calls = []
        crossfade_calls = []

        def fake_synthesize_reflection_mp3(text, output_path, *, voice="en-IE-EmilyNeural", rate="+0%"):
            Path(output_path).write_bytes(f"audio:{text}".encode("utf-8"))
            return Path(output_path)

        def fake_mix(narration_path, music_path, output_path, *, music_volume=0.12, fade_in_seconds=0, fade_out_seconds=4):
            mix_calls.append({
                "narration_path": Path(narration_path).name,
                "music_path": Path(music_path).name,
                "output_path": Path(output_path).name,
                "fade_in_seconds": fade_in_seconds,
                "fade_out_seconds": fade_out_seconds,
            })
            Path(output_path).write_bytes(b"mixed-audio")
            return Path(output_path)

        def fake_crossfade(input_paths, output_path, *, crossfade_seconds=3):
            crossfade_calls.append({
                "input_paths": [Path(path).name for path in input_paths],
                "output_path": Path(output_path).name,
                "crossfade_seconds": crossfade_seconds,
            })
            Path(output_path).write_bytes(b"final-audio")
            return Path(output_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            music_dir = Path(tmpdir)
            professional_track = music_dir / "deliberate-thought.mp3"
            reflective_track = music_dir / "comfortable-mystery.mp3"
            professional_track.write_bytes(b"pro")
            reflective_track.write_bytes(b"ref")

            with (
                patch.object(self.audio, "DEFAULT_MUSIC_LIBRARY_DIRS", [music_dir]),
                patch.object(self.audio, "synthesize_reflection_mp3", side_effect=fake_synthesize_reflection_mp3),
                patch.object(self.audio, "_mix_narration_with_music", side_effect=fake_mix),
                patch.object(self.audio, "_crossfade_mp3_files", side_effect=fake_crossfade, create=True),
            ):
                audio_bytes, mix_meta = self.audio.synthesize_daily_brief_bytes(
                    professional_text="Opening and practical content.",
                    reflective_text="Reflective grounding and close.",
                )

        self.assertEqual(audio_bytes, b"final-audio")
        self.assertEqual(len(mix_calls), 2)
        self.assertEqual(mix_calls[0]["fade_in_seconds"], 3)
        self.assertEqual(mix_calls[0]["fade_out_seconds"], 4)
        self.assertEqual(mix_calls[1]["fade_in_seconds"], 4)
        self.assertEqual(mix_calls[1]["fade_out_seconds"], 5)
        self.assertEqual(len(crossfade_calls), 1)
        self.assertEqual(crossfade_calls[0]["input_paths"], ["professional.mp3", "reflective.mp3"])
        self.assertEqual(crossfade_calls[0]["crossfade_seconds"], 3)
        self.assertEqual(mix_meta["professional_music_track"], "deliberate-thought.mp3")
        self.assertEqual(mix_meta["reflective_music_track"], "comfortable-mystery.mp3")

    def test_crossfade_mp3_files_uses_acrossfade_filter(self):
        commands = []

        def fake_run(cmd, capture_output, text, check):
            commands.append(cmd)

            class Result:
                returncode = 0
                stderr = ""

            return Result()

        with (
            patch.object(self.audio, "_resolve_ffmpeg", return_value="/usr/bin/ffmpeg"),
            patch.object(self.audio.subprocess, "run", side_effect=fake_run),
        ):
            output = self.audio._crossfade_mp3_files(
                ["professional.mp3", "reflective.mp3"],
                "daily-brief.mp3",
                crossfade_seconds=3,
            )

        self.assertEqual(str(output), "daily-brief.mp3")
        self.assertEqual(len(commands), 1)
        filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
        self.assertIn("acrossfade=d=3", filter_graph)


if __name__ == "__main__":
    unittest.main()
