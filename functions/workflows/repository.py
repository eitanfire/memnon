from __future__ import annotations

from firebase_admin import firestore


class FirestoreWorkflowRepository:
    def __init__(self, db):
        self.db = db

    def _doc(self, uid: str, capture_id: str):
        return self.db.collection("users").document(uid).collection("workflow_captures").document(capture_id)

    def _context_doc(self, uid: str, context_id: str):
        return self.db.collection("users").document(uid).collection("workflow_contexts").document(context_id)

    def load_user_profile(self, uid: str):
        snap = self.db.collection("users").document(uid).get()
        payload = snap.to_dict() if snap.exists else {}
        return {
            "lane": payload.get("lane", "professional"),
            "profession": payload.get("profession", "professional"),
            "reflection_style": payload.get("reflection_style", "practical"),
            "reflect_config": payload.get("reflect_config", {}),
        }

    def save_capture(self, uid: str, record):
        payload = record.to_dict()
        payload["updated_at"] = firestore.SERVER_TIMESTAMP
        payload["created_at"] = firestore.SERVER_TIMESTAMP
        self._doc(uid, record.capture_id).set(payload)
        return record.capture_id

    def get_capture(self, uid: str, capture_id: str):
        snap = self._doc(uid, capture_id).get()
        if not snap.exists:
            return None
        payload = snap.to_dict() or {}
        payload["capture_id"] = capture_id
        return payload

    def list_captures(self, uid: str, limit: int = 50):
        query = (
            self.db.collection("users")
            .document(uid)
            .collection("workflow_captures")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        items = []
        for snap in query.stream():
            payload = snap.to_dict() or {}
            payload["capture_id"] = snap.id
            items.append(payload)
        return items

    def create_context(self, uid: str, *, context_id: str, title: str, summary: str, seed_capture_id: str | None, now: str):
        payload = {
            "context_id": context_id,
            "title": title,
            "summary": summary,
            "status": "active",
            "seed_capture_id": seed_capture_id,
            "created_at": now,
            "updated_at": now,
            "last_activity_at": now,
        }
        self._context_doc(uid, context_id).set(payload)
        return {"context_id": context_id, **payload}

    def get_context(self, uid: str, context_id: str):
        snap = self._context_doc(uid, context_id).get()
        if not snap.exists:
            return None
        payload = snap.to_dict() or {}
        payload["context_id"] = context_id
        return payload

    def list_active_contexts(self, uid: str, limit: int = 12):
        query = (
            self.db.collection("users")
            .document(uid)
            .collection("workflow_contexts")
            .where("status", "==", "active")
            .order_by("last_activity_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        items = []
        for snap in query.stream():
            payload = snap.to_dict() or {}
            payload["context_id"] = snap.id
            items.append(payload)
        return items

    def update_capture_threading(self, uid: str, capture_id: str, threading: dict):
        self._doc(uid, capture_id).update(
            {"threading": threading, "updated_at": firestore.SERVER_TIMESTAMP},
        )

    def touch_context_activity(self, uid: str, context_id: str, now: str):
        self._context_doc(uid, context_id).set(
            {
                "last_activity_at": now,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
