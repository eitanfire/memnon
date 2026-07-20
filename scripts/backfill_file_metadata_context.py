#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from functions.workflows.service import _build_metadata_line

STORAGE_PATH = Path('.local/workflow-captures.json')


def main() -> int:
    payload = json.loads(STORAGE_PATH.read_text(encoding='utf-8'))
    records = payload.get('records') or {}

    changed = 0
    for record in records.values():
        source_event = record.get('source_event') or {}
        input_type = str(source_event.get('input_type') or record.get('input_type') or '')
        if input_type != 'file':
            continue

        context_hint = str(record.get('context_hint') or source_event.get('context_hint') or '')
        created_at = str(record.get('created_at') or source_event.get('created_at') or '')
        source_filename = str(source_event.get('source_filename') or '')

        new_metadata = _build_metadata_line(
            'file',
            created_at,
            context_hint,
            source_filename=source_filename,
        )

        result = record.get('result') or {}
        for artifact_key in ('primary_artifact', 'saved_note_artifact'):
            artifact = result.get(artifact_key)
            if isinstance(artifact, dict) and artifact.get('metadata_line') != new_metadata:
                artifact['metadata_line'] = new_metadata
                changed += 1

    STORAGE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    print(f'updated={changed}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
