"""Cloud-friendly audio synthesis helpers for grounded reflections.

This is the minimal subset of the local voice pipeline needed by the SaaS app:
turn a prepared reflection script into an MP3 using the same `edge-tts`
backend used in the local workflow.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

MUSIC_LIBRARY = {
    "energizing": ["deliberate-thought.mp3", "call-to-adventure.mp3", "rising.mp3"],
    "reflective": ["comfortable-mystery.mp3", "constancy-part-one.mp3", "slow-burn.mp3"],
    "grounded": ["constancy-part-one.mp3", "comfortable-mystery.mp3", "slow-burn.mp3"],
}
DEFAULT_MUSIC_LIBRARY_DIRS = [
    Path(__file__).resolve().parent / "assets" / "music",
    Path.home() / ".codex" / "wisdom" / "audio",
]


def synthesize_reflection_mp3(
    text: str,
    output_path: str | Path,
    *,
    voice: str = "en-IE-EmilyNeural",
    rate: str = "+0%",
) -> Path:
    """Write `text` to `output_path` as an MP3 via edge-tts."""
    if not text or not text.strip():
        raise ValueError("reflection text is empty")

    try:
        import edge_tts  # type: ignore
    except ImportError as exc:
        raise RuntimeError("edge-tts is not installed") from exc

    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        communicator = edge_tts.Communicate(text.strip(), voice, rate=rate)
        await communicator.save(str(dest))

    asyncio.run(_run())
    return dest


def synthesize_reflection_bytes(
    text: str,
    *,
    voice: str = "en-IE-EmilyNeural",
    rate: str = "+0%",
) -> bytes:
    """Return synthesized MP3 bytes for `text`."""
    output_path = Path("/tmp") / f"memnon-preview-{voice}.mp3"
    synthesize_reflection_mp3(text, output_path, voice=voice, rate=rate)
    return output_path.read_bytes()


def _resolve_ffmpeg() -> str:
    direct = shutil.which("ffmpeg")
    if direct:
        return direct

    homebrew = "/opt/homebrew/bin/ffmpeg"
    if Path(homebrew).exists():
        return homebrew

    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("ffmpeg is not available for audio mixing") from exc


def _candidate_music_dirs(music_library_dirs: list[str | Path] | None = None) -> list[Path]:
    raw_dirs = music_library_dirs or DEFAULT_MUSIC_LIBRARY_DIRS
    resolved = []
    for value in raw_dirs:
        path = Path(os.path.expanduser(str(value))).resolve()
        if path not in resolved:
            resolved.append(path)
    return resolved


def _select_music_for_mood(
    mood: str,
    *,
    music_library_dirs: list[str | Path] | None = None,
) -> Path | None:
    filenames = MUSIC_LIBRARY.get((mood or "").strip().lower(), [])
    if not filenames:
        return None
    for music_dir in _candidate_music_dirs(music_library_dirs):
        for filename in filenames:
            candidate = music_dir / filename
            if candidate.exists():
                return candidate
    return None


def _mix_narration_with_music(
    narration_path: str | Path,
    music_path: str | Path,
    output_path: str | Path,
    *,
    music_volume: float = 0.12,
    fade_in_seconds: int = 0,
    fade_out_seconds: int = 4,
) -> Path:
    ffmpeg = _resolve_ffmpeg()
    narration = Path(narration_path)
    music = Path(music_path)
    output = Path(output_path)

    fade_in_filter = f"afade=t=in:st=0:d={fade_in_seconds}," if fade_in_seconds > 0 else ""
    filter_graph = (
        f"[1:a]volume={music_volume},{fade_in_filter}aloop=loop=-1:size=2147483647[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition={fade_out_seconds}[out]"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(narration),
        "-i",
        str(music),
        "-filter_complex",
        filter_graph,
        "-map",
        "[out]",
        "-b:a",
        "128k",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mixing failed: {result.stderr.strip()}")
    return output


def _concatenate_mp3_files(input_paths: list[str | Path], output_path: str | Path) -> Path:
    ffmpeg = _resolve_ffmpeg()
    output = Path(output_path)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        concat_path = Path(handle.name)
        for path in input_paths:
            handle.write(f"file '{Path(path)}'\n")
    try:
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr.strip()}")
        return output
    finally:
        concat_path.unlink(missing_ok=True)


def synthesize_daily_brief_bytes(
    *,
    professional_text: str,
    reflective_text: str,
    voice: str = "en-IE-EmilyNeural",
    rate: str = "+0%",
    music_volume: float = 0.12,
    professional_music_mood: str = "energizing",
    reflective_music_mood: str = "reflective",
) -> tuple[bytes, dict]:
    professional_text = (professional_text or "").strip()
    reflective_text = (reflective_text or "").strip()
    if not professional_text and not reflective_text:
        raise ValueError("daily brief sections are empty")

    mix_meta = {
        "professional_music_track": "",
        "reflective_music_track": "",
        "used_music_beds": False,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        rendered_parts: list[Path] = []

        def _render_section(
            stem: str,
            text: str,
            mood: str,
            *,
            fade_in_seconds: int = 0,
        ) -> None:
            if not text:
                return
            narration_path = tmp / f"{stem}-narration.mp3"
            synthesize_reflection_mp3(text, narration_path, voice=voice, rate=rate)
            mixed_path = tmp / f"{stem}.mp3"
            track = _select_music_for_mood(mood)
            if track:
                _mix_narration_with_music(
                    narration_path,
                    track,
                    mixed_path,
                    music_volume=music_volume,
                    fade_in_seconds=fade_in_seconds,
                )
                if stem == "professional":
                    mix_meta["professional_music_track"] = track.name
                else:
                    mix_meta["reflective_music_track"] = track.name
                mix_meta["used_music_beds"] = True
                narration_path.unlink(missing_ok=True)
                rendered_parts.append(mixed_path)
            else:
                rendered_parts.append(narration_path)

        _render_section("professional", professional_text, professional_music_mood)
        _render_section("reflective", reflective_text, reflective_music_mood, fade_in_seconds=2)

        if not rendered_parts:
            raise RuntimeError("daily brief rendering produced no audio sections")
        if len(rendered_parts) == 1:
            return rendered_parts[0].read_bytes(), mix_meta

        final_path = tmp / "daily-brief.mp3"
        _concatenate_mp3_files(rendered_parts, final_path)
        return final_path.read_bytes(), mix_meta
