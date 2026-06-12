#!/usr/bin/env python3
"""Backfill legacy Firestore note metadata for Memnon history retrieval.

This script uses the Firestore REST API with the caller's existing gcloud auth.
It can safely add:
  - created_at (derived from legacy date if missing)
  - history_source_text
  - transcript_excerpt
  - reflection_style

If HUGGING_FACE_API_KEY is present in the environment, it also adds embedding_v1.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions"))

from hf_inference import embed_text


def shell_output(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def get_project_id() -> str:
    firebaserc = json.loads((Path(__file__).resolve().parents[1] / ".firebaserc").read_text())
    return firebaserc["projects"]["default"]


def get_access_token() -> str:
    return shell_output(["gcloud", "auth", "print-access-token"])


def firestore_request(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
) -> dict | list:
    token = get_access_token()
    body = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_string_list(field: dict) -> list[str]:
    values = field.get("arrayValue", {}).get("values", [])
    return [item.get("stringValue", "") for item in values if item.get("stringValue")]


def parse_influenced_by(field: dict) -> list[dict]:
    values = field.get("arrayValue", {}).get("values", [])
    items = []
    for value in values:
        fields = value.get("mapValue", {}).get("fields", {})
        if not fields:
            continue
        item = {}
        for key, inner in fields.items():
            if "stringValue" in inner:
                item[key] = inner["stringValue"]
        if item:
            items.append(item)
    return items


def build_history_source_text(fields: dict) -> str:
    title = fields.get("title", {}).get("stringValue", "").strip()
    summary = fields.get("summary", {}).get("stringValue", "").strip()
    insight = fields.get("insight", {}).get("stringValue", "").strip()
    transcript_excerpt = fields.get("transcript_excerpt", {}).get("stringValue", "").strip()
    voice_labels = parse_string_list(fields.get("voice_labels", {}))
    influenced_by = parse_influenced_by(fields.get("influenced_by", {}))

    lines = []
    if title:
        lines.append(f"title: {title}")
    if summary:
        lines.append(f"summary: {summary}")
    if insight:
        lines.append(f"insight: {insight}")
    if transcript_excerpt:
        lines.append(f"transcript excerpt: {transcript_excerpt}")
    if voice_labels:
        lines.append(f"voices: {', '.join(voice_labels[:5])}")
    elif influenced_by:
        voices = [item.get("author", "").strip() for item in influenced_by if item.get("author")]
        if voices:
            lines.append(f"voices: {', '.join(voices[:5])}")
    return "\n".join(lines).strip()


def make_patch_fields(fields: dict, hf_key: str) -> dict:
    patch_fields: dict[str, dict] = {}

    if "created_at" not in fields:
        legacy_date = fields.get("date", {}).get("stringValue", "").strip()
        if legacy_date:
            patch_fields["created_at"] = {
                "timestampValue": f"{legacy_date}T12:00:00Z"
            }

    if "transcript_excerpt" not in fields:
        summary = fields.get("summary", {}).get("stringValue", "").strip()
        if summary:
            patch_fields["transcript_excerpt"] = {"stringValue": summary[:240]}

    if "reflection_style" not in fields:
        patch_fields["reflection_style"] = {"stringValue": "complete"}

    history_source_text = fields.get("history_source_text", {}).get("stringValue", "").strip()
    if not history_source_text:
        history_source_text = build_history_source_text(fields)
        if history_source_text:
            patch_fields["history_source_text"] = {"stringValue": history_source_text}

    if "embedding_v1" not in fields and history_source_text and hf_key:
        embedding = embed_text(history_source_text, hf_key)
        if embedding:
            patch_fields["embedding_v1"] = {
                "arrayValue": {
                    "values": [{"doubleValue": value} for value in embedding]
                }
            }

    return patch_fields


def update_document(project_id: str, document_name: str, patch_fields: dict) -> None:
    query = "&".join(f"updateMask.fieldPaths={name}" for name in patch_fields.keys())
    url = (
        f"https://firestore.googleapis.com/v1/{document_name}"
        f"?{query}"
    )
    firestore_request(url, method="PATCH", payload={"fields": patch_fields})


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", dest="user_id")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    project_id = get_project_id()
    hf_key = os.environ.get("HUGGING_FACE_API_KEY", "").strip()
    users = list_user_documents(project_id, args.user_id)
    updated = 0

    for user_doc in users:
        user_id = user_doc["name"].split("/")[-1]
        notes = list_note_documents(project_id, user_id, args.limit)
        if not notes:
            continue
        print(f"user {user_id}: {len(notes)} notes")
        for note in notes:
            fields = note.get("fields", {})
            patch_fields = make_patch_fields(fields, hf_key)
            if not patch_fields:
                continue
            update_document(project_id, note["name"], patch_fields)
            updated += 1
            print(f"  updated {note['name'].split('/')[-1]} -> {', '.join(sorted(patch_fields.keys()))}")

    print(f"updated_documents {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
