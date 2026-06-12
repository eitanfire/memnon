#!/usr/bin/env python3
"""Audit Firestore note embedding coverage for Memnon.

Uses the caller's gcloud auth via the Firestore REST API.
Does not require HUGGING_FACE_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path


def shell_output(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def get_project_id() -> str:
    firebaserc = json.loads((Path(__file__).resolve().parents[1] / ".firebaserc").read_text())
    return firebaserc["projects"]["default"]


def get_access_token() -> str:
    return shell_output(["gcloud", "auth", "print-access-token"])


def firestore_request(url: str, method: str = "GET", payload: dict | None = None) -> dict | list:
    token = get_access_token()
    body = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def get_string(fields: dict, key: str) -> str:
    return fields.get(key, {}).get("stringValue", "").strip()


def has_embedding(fields: dict) -> bool:
    values = fields.get("embedding_v1", {}).get("arrayValue", {}).get("values", [])
    return bool(values)


def list_user_documents(project_id: str, user_id: str | None) -> list[dict]:
    if user_id:
        base = f"projects/{project_id}/databases/(default)/documents/users/{user_id}"
        return [{"name": base}]
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/users"
        "?pageSize=100"
    )
    payload = firestore_request(url)
    return payload.get("documents", [])


def list_note_documents(project_id: str, user_id: str, limit: int) -> list[dict]:
    query_url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
        f"/documents/users/{user_id}:runQuery"
    )
    payload = {
        "structuredQuery": {
            "from": [{"collectionId": "notes"}],
            "limit": limit,
        }
    }
    rows = firestore_request(query_url, method="POST", payload=payload)
    return [row.get("document") for row in rows if row.get("document")]


def summarize_user_notes(notes: list[dict], sample_limit: int) -> dict:
    total = 0
    embedded = 0
    missing_metadata = 0
    sample_missing = []

    for note in notes:
        fields = note.get("fields", {})
        total += 1
        embedded_here = has_embedding(fields)
        if embedded_here:
            embedded += 1
        if embedded_here and not get_string(fields, "embedding_model"):
            missing_metadata += 1
        if (not embedded_here or not get_string(fields, "embedding_model")) and len(sample_missing) < sample_limit:
            sample_missing.append({
                "id": note["name"].split("/")[-1],
                "title": get_string(fields, "title") or "Untitled",
                "date": get_string(fields, "date"),
                "has_embedding": embedded_here,
                "has_embedding_model": bool(get_string(fields, "embedding_model")),
            })

    return {
        "total_notes": total,
        "embedded_notes": embedded,
        "missing_embeddings": max(total - embedded, 0),
        "missing_metadata_notes": missing_metadata,
        "coverage_ratio": round((embedded / total), 4) if total else 0.0,
        "sample_missing": sample_missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", dest="user_id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project_id = get_project_id()
    users = list_user_documents(project_id, args.user_id)
    report = {"project_id": project_id, "users": []}

    for user_doc in users:
        user_id = user_doc["name"].split("/")[-1]
        user_fields = user_doc.get("fields", {})
        notes = list_note_documents(project_id, user_id, args.limit)
        summary = summarize_user_notes(notes, args.sample_limit)
        summary["uid"] = user_id
        summary["email"] = get_string(user_fields, "email")
        report["users"].append(summary)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"project {project_id}")
    for user in report["users"]:
        print(
            f"user {user['uid']} ({user['email'] or 'unknown'})"
            f" total={user['total_notes']}"
            f" embedded={user['embedded_notes']}"
            f" missing_embeddings={user['missing_embeddings']}"
            f" missing_metadata={user['missing_metadata_notes']}"
            f" coverage={user['coverage_ratio']}"
        )
        for item in user["sample_missing"]:
            print(
                f"  missing {item['id']} | {item['date']} | {item['title']}"
                f" | embedding={item['has_embedding']} model={item['has_embedding_model']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
