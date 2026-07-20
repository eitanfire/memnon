from __future__ import annotations

import json
from pathlib import Path

from functions.workflows.service import _extract_source_excerpt, _extract_voice_source_excerpt

STORAGE_PATH = Path('.local/workflow-captures.json')


def _compute_excerpt(source_text: str, input_type: str, limit: int) -> str:
    if input_type == 'voice':
        return _extract_voice_source_excerpt(source_text, limit=limit)
    return _extract_source_excerpt(source_text, limit=limit)


def main() -> None:
    payload = json.loads(STORAGE_PATH.read_text(encoding='utf-8'))
    records = payload.get('records') or {}

    changed = 0
    for record in records.values():
        source_event = record.get('source_event') or {}
        source_text = str(source_event.get('source_text') or '')
        input_type = str(source_event.get('input_type') or record.get('input_type') or 'text')
        if not source_text:
            continue

        result = record.get('result') or {}

        saved = result.get('saved_note_artifact')
        if isinstance(saved, dict):
            new_excerpt = _compute_excerpt(source_text, input_type, limit=140)
            if saved.get('source_excerpt') != new_excerpt:
                saved['source_excerpt'] = new_excerpt
                if not saved.get('copy_text'):
                    saved['copy_text'] = new_excerpt
                changed += 1

        primary = result.get('primary_artifact')
        if isinstance(primary, dict):
            new_excerpt = _compute_excerpt(source_text, input_type, limit=160)
            if primary.get('source_excerpt') != new_excerpt:
                primary['source_excerpt'] = new_excerpt
                changed += 1

    STORAGE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    print(f'updated={changed}')


if __name__ == '__main__':
    main()
