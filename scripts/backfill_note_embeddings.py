#!/usr/bin/env python3
"""Backfill Firestore note embeddings and embedding metadata for Memnon.

Uses the caller's gcloud auth via the Firestore REST API. Safe to run repeatedly.

Adds, when possible:
  - embedding_v1
  - embedding_model
  - embedding_provider
  - embedding_dim
  - embedding_version
  - embedding_created_at

Requires HUGGING_FACE_API_KEY in the environment to actually generate embeddings.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions"))

from hf_inference import EMBEDDING_MODEL, EMBEDDING_PROVIDER, embed_text_details


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


def build_history_source_text(fields: dict) -> str:
    parts = [
        get_string(fields, "title"),
        get_string(fields, "summary"),
        get_string(fields, "insight"),
        get_string(fields, "transcript_excerpt"),
        get_string(fields, "participant_response_excerpt"),
    ]
    return "\n".join(part for part in parts if part)


def has_embedding(fields: dict) -> bool:
    values = fields.get("embedding_v1", {}).get("arrayValue", {}).get("values", [])
    return bool(values)


def parse_embedding(fields: dict) -> list[float]:
    values = fields.get("embedding_v1", {}).get("arrayValue", {}).get("values", [])
    parsed = []
    for item in values:
        if "doubleValue" in item:
            parsed.append(float(item["doubleValue"]))
        elif "integerValue" in item:
            parsed.append(float(item["integerValue"]))
    return parsed


def make_patch_fields(fields: dict, hf_key: str, force: bool) -> dict:
    patch_fields: dict[str, dict] = {}
    if not hf_key:
        return patch_fields

    existing_embedding = parse_embedding(fields)
    needs_embedding = force or not existing_embedding
    needs_metadata = bool(existing_embedding) and not get_string(fields, "embedding_model")
    if not needs_embedding and not needs_metadata:
        return patch_fields

    history_source_text = get_string(fields, "history_source_text") or build_history_source_text(fields)
    if not history_source_text and needs_embedding:
        return patch_fields

    vector = existing_embedding
    result = {"model": EMBEDDING_MODEL, "provider": EMBEDDING_PROVIDER, "dimensions": len(existing_embedding)}
    if needs_embedding:
        result = embed_text_details(history_source_text, hf_key)
        vector = result.get("vector") or []
        if not vector:
            return patch_fields

    if history_source_text:
        patch_fields["history_source_text"] = {"stringValue": history_source_text}
    if needs_embedding:
        patch_fields["embedding_v1"] = {
            "arrayValue": {
                "values": [{"doubleValue": value} for value in vector]
            }
        }
    if needs_embedding or needs_metadata:
        patch_fields["embedding_model"] = {"stringValue": str(result.get("model") or EMBEDDING_MODEL)}
        patch_fields["embedding_provider"] = {"stringValue": str(result.get("provider") or EMBEDDING_PROVIDER)}
        patch_fields["embedding_dim"] = {"integerValue": str(int(result.get("dimensions") or len(vector)))}
        patch_fields["embedding_version"] = {"stringValue": "v1"}
        patch_fields["embedding_created_at"] = {"timestampValue": datetime.now(timezone.utc).isoformat()}
    return patch_fields


def update_document(document_name: str, patch_fields: dict) -> None:
    query = "&".join(f"updateMask.fieldPaths={name}" for name in patch_fields.keys())
    url = f"https://firestore.googleapis.com/v1/{document_name}?{query}"
    firestore_request(url, method="PATCH", payload={"fields": patch_fields})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", dest="user_id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    project_id = get_project_id()
    hf_key = os.environ.get("HUGGING_FACE_API_KEY", "").strip()
    if not hf_key:
        print("HUGGING_FACE_API_KEY is required for backfill.", file=sys.stderr)
        return 1

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
            patch_fields = make_patch_fields(fields, hf_key, args.force)
            if not patch_fields:
                continue
            update_document(note["name"], patch_fields)
            updated += 1
            print(f"  updated {note['name'].split('/')[-1]} -> {', '.join(sorted(patch_fields.keys()))}")

    print(f"updated_documents {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
