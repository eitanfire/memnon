#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_STORAGE = Path('.local/workflow-captures.json')
DEFAULT_REPORT = Path('docs/superpowers/plans/2026-07-17-derived-label-collisions.csv')


def iso_day(value: str) -> str:
    text = (value or '').strip()
    if not text:
        return ''
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date().isoformat()
    except ValueError:
        return ''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit same-day derived-label collisions for file captures."
    )
    parser.add_argument(
        "--fail-on-collision",
        action="store_true",
        help="Exit non-zero when one or more collision groups are found.",
    )
    parser.add_argument(
        "--storage",
        default=str(DEFAULT_STORAGE),
        help="Path to workflow captures JSON payload.",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="Path to write collision CSV report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    storage = Path(args.storage)
    report = Path(args.report)
    payload = json.loads(storage.read_text(encoding='utf-8'))
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

    for rec in (payload.get('records') or {}).values():
        result = rec.get('result') or {}
        artifact = result.get('primary_artifact') or result.get('saved_note_artifact') or {}
        source_event = rec.get('source_event') or {}
        if str(source_event.get('input_type') or '').strip().lower() != 'file':
            continue
        metadata_line = str(artifact.get('metadata_line') or '').strip()
        if '·' not in metadata_line:
            continue
        parts = [part.strip() for part in metadata_line.split('·')]
        if len(parts) != 2:
            continue
        label, _date_label = parts
        if label.lower() in {'uploaded file', 'pasted note', 'voice note', 'saved note'}:
            continue

        day = iso_day(str(rec.get('created_at') or ''))
        if not day:
            continue

        groups[(label, day)].append(
            {
                'capture_id': str(rec.get('capture_id') or ''),
                'source_filename': str(source_event.get('source_filename') or ''),
                'title': str(artifact.get('title') or ''),
                'metadata_line': metadata_line,
                'next_route': f"/workflows/result/{rec.get('capture_id') or ''}",
            }
        )

    rows: list[dict[str, str]] = []
    for (label, day), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        for item in items:
            rows.append(
                {
                    'label': label,
                    'day': day,
                    'collision_count': str(len(items)),
                    **item,
                }
            )

    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                'label',
                'day',
                'collision_count',
                'capture_id',
                'source_filename',
                'title',
                'metadata_line',
                'next_route',
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    collision_groups = len({(r["label"], r["day"]) for r in rows})
    collision_rows = len(rows)

    print(f'collision_groups={collision_groups}')
    print(f'collision_rows={collision_rows}')
    print(f'storage={storage}')
    print(f'report={report}')
    print('interpretation=no collisions observed in current dataset' if collision_groups == 0 else 'interpretation=collisions observed in current dataset')
    if rows:
        print('sample:')
        for row in rows[:10]:
            print(f"{row['label']} | {row['day']} | {row['capture_id']} | {row['source_filename']}")

    if args.fail_on_collision and collision_groups > 0:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
