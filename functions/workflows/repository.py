from __future__ import annotations

from firebase_admin import firestore


class FirestoreWorkflowRepository:
    def __init__(self, db):
        self.db = db

    def _doc(self, uid: str, capture_id: str):
        return self.db.collection("users").document(uid).collection("workflow_captures").document(capture_id)

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
