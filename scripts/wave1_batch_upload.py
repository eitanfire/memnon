#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path
import subprocess
import sys

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload selected Wave 1 text files to local workflows API.")
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
    manifest_path = Path("docs/superpowers/plans/2026-07-17-wave1-sample-manifest.csv")
    api_url = "http://127.0.0.1:5051/api/workflows/captures"

    with manifest_path.open(newline="") as f:
        rows = [
            r
            for r in csv.DictReader(f)
            if r.get("input_origin") == "text" and r.get("status") == "selected"
        ]

    ok_rows = []
    fail_rows = []

    for r in rows:
        slot_id = r.get("slot_id", "")
        source_path = Path(r.get("source_path", ""))

        if not source_path.exists():
            fail_rows.append([slot_id, str(source_path), "missing_file", ""])
            continue

        try:
            with source_path.open("rb") as fh:
                resp = requests.post(
                    api_url,
                    files={"file": (source_path.name, fh, "text/plain")},
                    data={"context_hint": "", "uid": "wave1-local-eval"},
                    headers={"Authorization": "Bearer local-dev-token"},
                    timeout=45,
                )

            if resp.status_code in (200, 201):
                payload = resp.json()
                ok_rows.append(
                    [
                        slot_id,
                        source_path.name,
                        payload.get("capture_id", ""),
                        payload.get("next_route", ""),
                    ]
                )
            else:
                fail_rows.append(
                    [
                        slot_id,
                        source_path.name,
                        f"status_{resp.status_code}",
                        (resp.text or "")[:160],
                    ]
                )
        except Exception as exc:  # noqa: BLE001
            fail_rows.append([slot_id, source_path.name, type(exc).__name__, str(exc)[:160]])

    ok_file = Path("docs/superpowers/plans/2026-07-17-wave1-upload-results.csv")
    fail_file = Path("docs/superpowers/plans/2026-07-17-wave1-upload-failures.csv")

    with ok_file.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slot_id", "source_name", "capture_id", "next_route"])
        w.writerows(ok_rows)

    with fail_file.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slot_id", "source_name", "error_kind", "error_detail"])
        w.writerows(fail_rows)

    print(f"ok={len(ok_rows)}")
    print(f"fail={len(fail_rows)}")
    print(f"ok_file={ok_file}")
    print(f"fail_file={fail_file}")

    if not args.skip_collision_gate:
        gate_code = run_collision_gate()
        if gate_code != 0:
            return gate_code

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
