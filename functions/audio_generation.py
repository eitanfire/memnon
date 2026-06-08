"""Cloud-friendly audio synthesis helpers for grounded reflections.

This is the minimal subset of the local voice pipeline needed by the SaaS app:
turn a prepared reflection script into an MP3 using the same `edge-tts`
backend used in the local workflow.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


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
