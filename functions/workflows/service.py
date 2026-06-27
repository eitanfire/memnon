from __future__ import annotations

import secrets

from .models import WorkflowArtifact, WorkflowCaptureRecord, WorkflowResultPayload
from .routing import build_source_event, route_text_capture


class WorkflowService:
    def __init__(self, repository, note_generator, now_provider, api_key_provider):
        self.repository = repository
        self.note_generator = note_generator
        self.now_provider = now_provider
        self.api_key_provider = api_key_provider

    def create_text_capture(self, uid: str, source_text: str, context_hint: str):
        capture_id = f"cap-{secrets.token_hex(6)}"
        now = self.now_provider()
        profile = self.repository.load_user_profile(uid)
        source_event = build_source_event(source_text, context_hint, capture_id, now)
        source_event["profile_snapshot"] = profile
        decision = route_text_capture(source_text, context_hint, profile)

        primary_artifact = None
        if decision.primary_artifact_kind == "professional_note":
            generated = self.note_generator(source_text, context_hint, profile, self.api_key_provider())
            primary_artifact = WorkflowArtifact(
                artifact_id=f"{capture_id}-primary",
                kind="professional_note",
                title=generated["title"],
                framing_line=generated["framing_line"],
                body=generated["body"],
                status="Ready to review",
                primary_action="Copy",
                secondary_actions=["Edit", "Regenerate"],
            ).to_dict()

        result = WorkflowResultPayload(
            interpretation_line=decision.interpretation_line,
            route_kind=decision.route_kind,
            primary_artifact=primary_artifact,
            secondary_artifacts=[],
            review_queue=[],
            source_preview=source_event["source_preview"],
            likely_themes=decision.likely_themes,
        ).to_dict()

        record = WorkflowCaptureRecord(
            capture_id=capture_id,
            input_type="text",
            context_hint=context_hint,
            source_event=source_event,
            routing=decision.to_dict(),
            result=result,
            event_manifest={
                "source_event": source_event,
                "routing": decision.to_dict(),
                "artifact_count": 1 if primary_artifact else 0,
            },
            created_at=now,
            updated_at=now,
        )
        self.repository.save_capture(uid, record)
        return record

    def get_capture(self, uid: str, capture_id: str):
        return self.repository.get_capture(uid, capture_id)
