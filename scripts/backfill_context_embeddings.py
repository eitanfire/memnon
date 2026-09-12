#!/usr/bin/env python3
"""Backfill Firestore workflow-context embeddings for Memnon.

Threads created before semantic thread matching shipped have no embedding_v1,
so they can only ever be reached by the lexical signals. This gives them the
same vector `create_context` now writes at creation time.

Uses the caller's gcloud auth via the Firestore REST API. Safe to run
repeatedly -- a context that already has an embedding is skipped unless
--force is passed.

Adds, per workflow_contexts document:
  - embedding_v1
  - embedding_model
  - embedding_provider
  - embedding_dim
  - embedding_version
  - embedding_created_at

Requires HUGGING_FACE_API_KEY in the environment.

    HUGGING_FACE_API_KEY=... python3 scripts/backfill_context_embeddings.py --dry-run
    HUGGING_FACE_API_KEY=... python3 scripts/backfill_context_embeddings.py --user <uid>
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

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "functions"))

from hf_inference import EMBEDDING_MODEL, EMBEDDING_PROVIDER, embed_text_details
from workflows.service import _context_embedding_source


def shell_output(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def get_project_id() -> str:
    return json.loads((REPO_ROOT / ".firebaserc").read_text())["projects"]["default"]


def get_access_token() -> str:
    return shell_output(["gcloud", "auth", "print-access-token"])


def firestore_request(url: str, method: str = "GET", payload: dict | None = None) -> dict | list:
    headers = {"Authorization": f"Bearer {get_access_token()}"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def get_string(fields: dict, key: str) -> str:
    return fields.get(key, {}).get("stringValue", "").strip()


def has_embedding(fields: dict) -> bool:
    return bool(fields.get("embedding_v1", {}).get("arrayValue", {}).get("values", []))


def list_user_documents(project_id: str, user_id: str | None) -> list[dict]:
    if user_id:
        base = f"projects/{project_id}/databases/(default)/documents/users/{user_id}"
        return [{"name": base}]
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
        "/documents/users?pageSize=100"
    )
    return firestore_request(url).get("documents", [])


def list_context_documents(project_id: str, user_id: str, limit: int) -> list[dict]:
    query_url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
        f"/documents/users/{user_id}:runQuery"
    )
    payload = {"structuredQuery": {"from": [{"collectionId": "workflow_contexts"}], "limit": limit}}
    rows = firestore_request(query_url, method="POST", payload=payload)
    return [row.get("document") for row in rows if row.get("document")]


def build_patch_fields(fields: dict, hf_key: str, force: bool) -> dict:
    if has_embedding(fields) and not force:
        return {}

    source_text = _context_embedding_source(get_string(fields, "title"), get_string(fields, "summary"))
    if not source_text:
        return {}

    result = embed_text_details(source_text, hf_key)
    vector = result.get("vector") or []
    if not vector:
        return {}

    return {
        "embedding_v1": {"arrayValue": {"values": [{"doubleValue": value} for value in vector]}},
        "embedding_model": {"stringValue": str(result.get("model") or EMBEDDING_MODEL)},
        "embedding_provider": {"stringValue": str(result.get("provider") or EMBEDDING_PROVIDER)},
        "embedding_dim": {"integerValue": str(int(result.get("dimensions") or len(vector)))},
        "embedding_version": {"stringValue": "v1"},
        "embedding_created_at": {"timestampValue": datetime.now(timezone.utc).isoformat()},
    }


def update_document(document_name: str, patch_fields: dict) -> None:
    query = "&".join(f"updateMask.fieldPaths={name}" for name in patch_fields.keys())
    url = f"https://firestore.googleapis.com/v1/{document_name}?{query}"
    firestore_request(url, method="PATCH", payload={"fields": patch_fields})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", dest="user_id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--force", action="store_true", help="re-embed contexts that already have a vector")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args()

    hf_key = os.environ.get("HUGGING_FACE_API_KEY", "").strip()
    if not hf_key:
        print("HUGGING_FACE_API_KEY is required for backfill.", file=sys.stderr)
        return 1

    project_id = get_project_id()
    updated = 0
    skipped = 0

    for user_doc in list_user_documents(project_id, args.user_id):
        user_id = user_doc["name"].split("/")[-1]
        contexts = list_context_documents(project_id, user_id, args.limit)
        if not contexts:
            continue
        print(f"user {user_id}: {len(contexts)} contexts")
        for context in contexts:
            fields = context.get("fields", {})
            context_id = context["name"].split("/")[-1]
            patch_fields = build_patch_fields(fields, hf_key, args.force)
            if not patch_fields:
                skipped += 1
                continue
            if args.dry_run:
                print(f"  would embed {context_id} ({get_string(fields, 'title') or 'untitled'})")
            else:
                update_document(context["name"], patch_fields)
                print(f"  updated {context_id} -> {int(patch_fields['embedding_dim']['integerValue'])} dims")
            updated += 1

    verb = "would_update" if args.dry_run else "updated"
    print(f"{verb}_contexts {updated} skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
