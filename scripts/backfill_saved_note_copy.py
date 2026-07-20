from __future__ import annotations

import json
from pathlib import Path

from functions.workflows.models import WorkflowArtifactSection
from functions.workflows.service import _derive_ambiguous_reason, build_copy_text, derive_specific_title

STORAGE_PATH = Path(".local/workflow-captures.json")
NEW_INTERPRETATION = "Saved as a note that needs clearer direction."
NEW_FRAMING = "Saved for now. The direction is not clear yet, but the note is worth keeping."


def _rewrite_needs_direction_record(record: dict) -> bool:
    result = record.get("result") or {}
    saved = result.get("saved_note_artifact") or {}
    if not saved or saved.get("state") != "needs_direction":
        return False

    source_event = record.get("source_event") or {}
    source_text = str(source_event.get("source_text") or "")
    context_hint = str(record.get("context_hint") or source_event.get("context_hint") or "")
    likely_themes = list((record.get("routing") or {}).get("likely_themes") or [])

    title = derive_specific_title(source_text, context_hint, "", suffix="note")
    reason = _derive_ambiguous_reason(source_text, context_hint, likely_themes)
    sections = [{"label": "Why keep this", "text": reason}]

    saved["title"] = title
    saved["framing_line"] = NEW_FRAMING
    saved["status"] = "Saved, needs direction"
    saved["sections"] = sections
    saved["copy_text"] = build_copy_text(
        title,
        [WorkflowArtifactSection(label="Why keep this", text=reason)],
    )

    result["interpretation_line"] = NEW_INTERPRETATION

    routing = record.get("routing") or {}
    routing["interpretation_line"] = NEW_INTERPRETATION

    manifest = record.get("event_manifest") or {}
    manifest_routing = manifest.get("routing")
    if isinstance(manifest_routing, dict):
        manifest_routing["interpretation_line"] = NEW_INTERPRETATION

    return True


def main() -> None:
    payload = json.loads(STORAGE_PATH.read_text(encoding="utf-8"))
    records = payload.get("records") or {}

    changed = 0
    for _key, record in records.items():
        if _rewrite_needs_direction_record(record):
            changed += 1

    STORAGE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"updated={changed}")


if __name__ == "__main__":
    main()
