from __future__ import annotations

import json
from pathlib import Path

from .models import SourceEvent


def load_source_event(metadata_path: Path) -> SourceEvent:
    payload = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    transcript_path = Path(payload["transcript_path"])
    transcript = transcript_path.read_text(encoding="utf-8").strip()
    return SourceEvent(
        source_event_id=payload["source_event_id"],
        lane=payload["lane"],
        workflow=payload["workflow"],
        routing_reason=payload["routing_reason"],
        title=payload["title"],
        transcript=transcript,
        transcript_path=str(transcript_path),
        transcript_preview=payload.get("transcript_preview", ""),
        note_path=payload["note_path"],
        archived_audio_path=payload["archived_audio_path"],
        metadata_path=str(metadata_path),
        processed_at=payload["processed_at"],
        summary=payload.get("summary", ""),
        action_items=list(payload.get("action_items", [])),
        suggested_tags=list(payload.get("suggested_tags", [])),
        source_project=payload.get("source_project", ""),
        source_context=payload.get("source_context", ""),
        source_links=list(payload.get("source_links", [])),
    )
