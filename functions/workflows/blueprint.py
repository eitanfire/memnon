from __future__ import annotations

from flask import Blueprint, jsonify, request


SUPPORTED_AUDIO_CONTENT_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/x-m4a",
    "video/mp4",
    "audio/ogg",
}

MAX_INLINE_AUDIO_BYTES = 5 * 1024 * 1024


def _is_multipart_request() -> bool:
    return (request.content_type or "").lower().startswith("multipart/form-data")


def create_workflows_blueprint(
    verify_token,
    service_provider,
    *,
    transcribe_audio=None,
    transcription_api_key_provider=None,
):
    blueprint = Blueprint("workflows", __name__)

    def _error_response(error: Exception):
        if isinstance(error, KeyError):
            return jsonify({"error": "not found"}), 404
        if isinstance(error, ValueError):
            return jsonify({"error": str(error)}), 400
        raise error

    @blueprint.route("/contexts", methods=["GET"])
    def list_contexts():
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        service = service_provider()
        return jsonify({"items": service.list_active_contexts(uid)})

    @blueprint.route("/contexts", methods=["POST"])
    def create_context():
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip()
        if len(title) < 2:
            return jsonify({"error": "title required"}), 400

        service = service_provider()
        created = service.create_context(
            uid,
            title=title,
            summary=(payload.get("summary") or "").strip(),
            seed_capture_id=payload.get("seed_capture_id"),
        )
        return jsonify(created), 201

    @blueprint.route("/captures", methods=["GET"])
    def list_captures():
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        service = service_provider()
        return jsonify({"items": service.list_capture_summaries(uid)})

    @blueprint.route("/captures", methods=["POST"])
    def create_capture():
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        service = service_provider()

        if _is_multipart_request():
            uploaded = request.files.get("file")
            context_hint = (request.form.get("context_hint") or "").strip()
            if uploaded is None:
                return jsonify({"error": "audio file is required"}), 400

            content_type = (uploaded.mimetype or uploaded.content_type or "").lower()
            if content_type not in SUPPORTED_AUDIO_CONTENT_TYPES:
                return jsonify({"error": "unsupported audio format"}), 400

            audio_bytes = uploaded.read()
            if not audio_bytes:
                return jsonify({"error": "audio file is empty"}), 400
            if len(audio_bytes) > MAX_INLINE_AUDIO_BYTES:
                return jsonify({"error": "audio file is too large for inline capture"}), 413

            if transcribe_audio is None or transcription_api_key_provider is None:
                return jsonify({"error": "audio capture unavailable"}), 500

            try:
                text = transcribe_audio(
                    audio_bytes,
                    uploaded.filename or "voice-note.webm",
                    transcription_api_key_provider(),
                ).strip()
            except Exception:
                return jsonify({"error": "transcription failed"}), 500

            if len(text.split()) < 3:
                return jsonify({"error": "text too short"}), 400

            record = service.create_text_capture(
                uid=uid,
                source_text=text,
                context_hint=context_hint,
                input_type="voice",
            )
        else:
            payload = request.get_json(silent=True) or {}
            text = (payload.get("text") or "").strip()
            context_hint = (payload.get("context_hint") or "").strip()
            if len(text.split()) < 3:
                return jsonify({"error": "text too short"}), 400

            record = service.create_text_capture(uid=uid, source_text=text, context_hint=context_hint)

        body = record.to_dict()
        body["next_route"] = f"/workflows/result/{record.capture_id}"
        return jsonify(body), 201

    @blueprint.route("/captures/<capture_id>", methods=["GET"])
    def get_capture(capture_id: str):
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        service = service_provider()
        payload = service.get_capture(uid, capture_id)
        if payload is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(payload)

    @blueprint.route("/captures/<capture_id>/feedback", methods=["POST"])
    def apply_feedback_choice(capture_id: str):
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        service = service_provider()
        try:
            updated = service.apply_feedback_choice(
                uid,
                capture_id,
                feedback_choice=(payload.get("feedback_choice") or "").strip(),
            )
        except (KeyError, ValueError) as error:
            return _error_response(error)
        return jsonify(updated)

    @blueprint.route("/captures/<capture_id>/context-decision", methods=["POST"])
    def apply_context_decision(capture_id: str):
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        service = service_provider()
        try:
            updated = service.apply_context_decision(
                uid,
                capture_id,
                action=(payload.get("action") or "").strip(),
                context_id=payload.get("context_id"),
                new_context_title=(payload.get("new_context_title") or "").strip() or None,
            )
        except (KeyError, ValueError) as error:
            return _error_response(error)
        return jsonify(updated)

    return blueprint
