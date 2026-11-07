"""Per-user capture tokens: long-lived bearer tokens that can only create captures.

An iOS Shortcut or a native Action Button app cannot hold a Firebase ID token
(it expires after an hour), so each user may mint one capture token on setup.

- The raw token is shown once and never stored. Firestore keeps only its
  SHA-256 digest, as the document id in `capture_tokens/{digest}`, plus the
  same digest on the user document so a regenerated or revoked token stops
  working even if its lookup document were somehow left behind.
- Tokens carry the `mcap_` prefix so they are recognizable in logs and in
  secret scanners, and so Firebase ID-token checks can refuse them outright.
- One active token per user. Issuing a new one deletes the old one.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

CAPTURE_TOKEN_PREFIX = "mcap_"
CAPTURE_TOKEN_COLLECTION = "capture_tokens"
CAPTURE_TOKEN_RANDOM_BYTES = 32
USER_TOKEN_FIELD = "capture_token"


def generate_capture_token() -> str:
    return CAPTURE_TOKEN_PREFIX + secrets.token_urlsafe(CAPTURE_TOKEN_RANDOM_BYTES)


def hash_capture_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def is_capture_token(raw_token: str | None) -> bool:
    return bool(raw_token) and raw_token.startswith(CAPTURE_TOKEN_PREFIX)


def bearer_token(req) -> str:
    header = req.headers.get("Authorization", "") or ""
    if not header.startswith("Bearer "):
        return ""
    return header[7:].strip()


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def capture_token_summary(user_data: dict | None) -> dict:
    """What the setup page may see: never the token, never its digest."""
    meta = (user_data or {}).get(USER_TOKEN_FIELD) or {}
    if not meta.get("id"):
        return {"active": False}
    return {
        "active": True,
        "hint": meta.get("hint", ""),
        "created_at": meta.get("created_at"),
        "last_used_at": meta.get("last_used_at"),
    }


def issue_capture_token(db, uid: str, now: datetime | None = None) -> tuple[str, dict]:
    """Create or regenerate the user's capture token. Returns (raw token, summary).
    The previous token, if any, stops working immediately."""
    now = _now(now)
    user_ref = db.collection("users").document(uid)
    snapshot = user_ref.get()
    previous = ((snapshot.to_dict() or {}) if snapshot.exists else {}).get(USER_TOKEN_FIELD) or {}
    if previous.get("id"):
        db.collection(CAPTURE_TOKEN_COLLECTION).document(previous["id"]).delete()

    raw = generate_capture_token()
    digest = hash_capture_token(raw)
    db.collection(CAPTURE_TOKEN_COLLECTION).document(digest).set({
        "uid": uid,
        "created_at": now,
        "last_used_at": None,
    })
    meta = {"id": digest, "hint": raw[-4:], "created_at": now, "last_used_at": None}
    user_ref.set({USER_TOKEN_FIELD: meta}, merge=True)
    return raw, capture_token_summary({USER_TOKEN_FIELD: meta})


def revoke_capture_token(db, uid: str, delete_field) -> bool:
    """Delete the user's capture token. Returns whether one existed."""
    user_ref = db.collection("users").document(uid)
    snapshot = user_ref.get()
    previous = ((snapshot.to_dict() or {}) if snapshot.exists else {}).get(USER_TOKEN_FIELD) or {}
    if previous.get("id"):
        db.collection(CAPTURE_TOKEN_COLLECTION).document(previous["id"]).delete()
    user_ref.set({USER_TOKEN_FIELD: delete_field}, merge=True)
    return bool(previous.get("id"))


def verify_capture_token(db, raw_token: str, now: datetime | None = None) -> str | None:
    """Return the uid a capture token belongs to, or None. Records last_used_at."""
    if not is_capture_token(raw_token):
        return None
    digest = hash_capture_token(raw_token)
    lookup = db.collection(CAPTURE_TOKEN_COLLECTION).document(digest).get()
    if not lookup.exists:
        return None
    uid = (lookup.to_dict() or {}).get("uid")
    if not uid:
        return None
    user_ref = db.collection("users").document(uid)
    user_snapshot = user_ref.get()
    current = ((user_snapshot.to_dict() or {}) if user_snapshot.exists else {}).get(USER_TOKEN_FIELD) or {}
    if not hmac.compare_digest(str(current.get("id") or ""), digest):
        return None
    now = _now(now)
    db.collection(CAPTURE_TOKEN_COLLECTION).document(digest).set({"last_used_at": now}, merge=True)
    user_ref.set({USER_TOKEN_FIELD: {**current, "last_used_at": now}}, merge=True)
    return uid
