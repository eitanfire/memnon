#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

import requests

API_URL = "http://127.0.0.1:5051/api/workflows/captures"
AUTH_HEADER = {"Authorization": "Bearer local-dev-token"}
TRANSCRIPTS_DIR = Path("runtime/transcripts")
LOCAL_STORAGE = Path(".local/workflow-captures.json")
RESULTS_FILE = Path("docs/superpowers/plans/2026-07-17-catchup-upload-results.csv")
FAILURES_FILE = Path("docs/superpowers/plans/2026-07-17-catchup-upload-failures.csv")


def existing_uploaded_filenames() -> set[str]:
    if not LOCAL_STORAGE.exists():
        return set()
    payload = json.loads(LOCAL_STORAGE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for record in (payload.get("records") or {}).values():
        source_event = record.get("source_event") or {}
        if (source_event.get("input_type") or "") != "file":
            continue
        name = (source_event.get("source_filename") or "").strip()
        if name:
            names.add(name)
    return names


def discover_missing_transcripts(existing_names: set[str]) -> list[Path]:
    files = sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    return [path for path in files if path.name not in existing_names]


def upload_file(path: Path) -> tuple[bool, dict]:
    with path.open("rb") as fh:
        response = requests.post(
            API_URL,
            files={"file": (path.name, fh, "text/plain")},
            data={"context_hint": "", "uid": "catchup-local"},
            headers=AUTH_HEADER,
            timeout=60,
        )

    if response.status_code in (200, 201):
        payload = response.json()
        return True, {
            "source_name": path.name,
            "capture_id": payload.get("capture_id", ""),
            "next_route": payload.get("next_route", ""),
        }

    return False, {
        "source_name": path.name,
        "error_kind": f"status_{response.status_code}",
        "error_detail": (response.text or "")[:180],
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload all missing transcript files to local workflows API.")
    parser.add_argument(
        "--skip-collision-gate",
        action="store_true",
        help="Skip running derived-label collision audit after upload.",
    )
    return parser.parse_args()


def run_collision_gate() -> int:
    gate_script = Path("scripts/audit_label_collisions.py")
    if not gate_script.exists():
        print("collision_gate=missing_script")
        return 0

    completed = subprocess.run(
        [sys.executable, str(gate_script), "--fail-on-collision"],
        check=False,
    )
    if completed.returncode != 0:
        print("collision_gate=failed")
        return completed.returncode
    print("collision_gate=passed")
    return 0


def main() -> int:
    args = parse_args()
    existing = existing_uploaded_filenames()
    missing = discover_missing_transcripts(existing)

    ok_rows: list[dict] = []
    fail_rows: list[dict] = []

    for path in missing:
        try:
            ok, data = upload_file(path)
        except Exception as exc:  # noqa: BLE001
            ok = False
            data = {
                "source_name": path.name,
                "error_kind": type(exc).__name__,
                "error_detail": str(exc)[:180],
            }

        if ok:
            ok_rows.append(data)
        else:
            fail_rows.append(data)

    write_csv(RESULTS_FILE, ["source_name", "capture_id", "next_route"], ok_rows)
    write_csv(FAILURES_FILE, ["source_name", "error_kind", "error_detail"], fail_rows)

    print(f"existing_file_captures={len(existing)}")
    print(f"missing_transcripts={len(missing)}")
    print(f"uploaded_ok={len(ok_rows)}")
    print(f"uploaded_fail={len(fail_rows)}")
    print(f"results_file={RESULTS_FILE}")
    print(f"failures_file={FAILURES_FILE}")

    if not args.skip_collision_gate:
        gate_code = run_collision_gate()
        if gate_code != 0:
            return gate_code

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
