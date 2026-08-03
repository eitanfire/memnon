from __future__ import annotations

from pathlib import Path
import secrets

from flask import Blueprint, jsonify, request


SUPPORTED_AUDIO_CONTENT_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/x-m4a",
    "video/mp4",
    "audio/ogg",
}

MAX_INLINE_AUDIO_BYTES = 5 * 1024 * 1024
MAX_TEXT_FILE_BYTES = 512 * 1024
SUPPORTED_TEXT_FILE_EXTENSIONS = {".txt", ".md"}
TEXT_FILE_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def _is_multipart_request() -> bool:
    return (request.content_type or "").lower().startswith("multipart/form-data")


def _text_file_extension(filename: str | None) -> str:
    return Path(filename or "").suffix.lower()


def _coerce_optional_bool(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def create_workflows_blueprint(
    verify_token,
    service_provider,
    *,
    transcribe_audio=None,
    transcription_api_key_provider=None,
    archive_voice_capture_audio=None,
    download_voice_capture_audio=None,
):
    blueprint = Blueprint("workflows", __name__)

    def _sanitize_capture_payload(payload: dict) -> dict:
        body = dict(payload or {})
        event_manifest = dict(body.get("event_manifest") or {})
        event_manifest.pop("contextual_suggestions", None)
        if event_manifest:
            body["event_manifest"] = event_manifest
        return body

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
            include_teaching_context = _coerce_optional_bool(
                request.form.get("include_teaching_context")
            )
            if uploaded is None:
                return jsonify({"error": "audio file is required"}), 400

            filename = uploaded.filename or ""
            extension = _text_file_extension(filename)
            if extension in SUPPORTED_TEXT_FILE_EXTENSIONS:
                file_bytes = uploaded.read()
                if len(file_bytes) > MAX_TEXT_FILE_BYTES:
                    return jsonify({"error": "File is too large. Maximum size is 512 KB."}), 413
                try:
                    text = file_bytes.decode("utf-8").strip()
                except UnicodeDecodeError:
                    return jsonify({"error": "We couldn’t read text from this file."}), 400
                if not text:
                    return jsonify({"error": "We couldn’t read text from this file."}), 400

                record = service.create_text_capture(
                    uid=uid,
                    source_text=text,
                    context_hint=context_hint,
                    input_type="file",
                    include_teaching_context=include_teaching_context,
                    source_metadata={
                        "source_filename": filename,
                        "source_file_type": (uploaded.mimetype or uploaded.content_type or TEXT_FILE_CONTENT_TYPES[extension]).lower(),
                        "source_file_extension": extension,
                        "source_file_size_bytes": len(file_bytes),
                    },
                )
                body = _sanitize_capture_payload(record.to_dict())
                body["next_route"] = f"/today/result/{record.capture_id}"
                return jsonify(body), 201

            content_type = (uploaded.mimetype or uploaded.content_type or "").lower()
            if not content_type.startswith("audio/") and not content_type.startswith("video/"):
                return jsonify({"error": "File must be .txt or .md."}), 400

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

            capture_id = f"cap-{secrets.token_hex(6)}"
            source_metadata = None
            if archive_voice_capture_audio is not None:
                storage_path = archive_voice_capture_audio(
                    uid=uid,
                    capture_id=capture_id,
                    audio_bytes=audio_bytes,
                    filename=uploaded.filename or "voice-note.webm",
                    content_type=content_type,
                )
                if storage_path:
                    source_metadata = {
                        "source_audio_storage_path": storage_path,
                        "source_audio_content_type": content_type,
                        "source_audio_filename": uploaded.filename or "voice-note.webm",
                        "source_audio_size_bytes": len(audio_bytes),
                    }

            record = service.create_text_capture(
                uid=uid,
                capture_id=capture_id,
                source_text=text,
                context_hint=context_hint,
                input_type="voice",
                include_teaching_context=include_teaching_context,
                source_metadata=source_metadata,
            )
        else:
            payload = request.get_json(silent=True) or {}
            text = (payload.get("text") or "").strip()
            context_hint = (payload.get("context_hint") or "").strip()
            include_teaching_context = _coerce_optional_bool(
                payload.get("include_teaching_context")
            )
            if len(text.split()) < 3:
                return jsonify({"error": "text too short"}), 400

            record = service.create_text_capture(
                uid=uid,
                source_text=text,
                context_hint=context_hint,
                include_teaching_context=include_teaching_context,
            )

        body = _sanitize_capture_payload(record.to_dict())
        body["next_route"] = f"/today/result/{record.capture_id}"
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
        return jsonify(_sanitize_capture_payload(payload))

    @blueprint.route("/captures/<capture_id>/source-audio", methods=["GET"])
    def get_capture_source_audio(capture_id: str):
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        if download_voice_capture_audio is None:
            return jsonify({"error": "not found"}), 404

        service = service_provider()
        payload = service.get_capture(uid, capture_id)
        if payload is None:
            return jsonify({"error": "not found"}), 404

        source_event = payload.get("source_event") or {}
        if source_event.get("input_type") != "voice":
            return jsonify({"error": "not found"}), 404

        storage_path = (source_event.get("source_audio_storage_path") or "").strip()
        if not storage_path:
            return jsonify({"error": "not found"}), 404

        return (
            download_voice_capture_audio(storage_path),
            200,
            {
                "Content-Type": (source_event.get("source_audio_content_type") or "audio/webm"),
                "Cache-Control": "private, max-age=300",
            },
        )

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
                feedback_note=(payload.get("feedback_note") or "").strip()[:500],
            )
        except (KeyError, ValueError) as error:
            return _error_response(error)
        return jsonify(_sanitize_capture_payload(updated))

    @blueprint.route("/captures/<capture_id>/regenerate", methods=["POST"])
    def regenerate_capture(capture_id: str):
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        service = service_provider()
        try:
            updated = service.regenerate_capture(uid, capture_id)
        except (KeyError, ValueError) as error:
            return _error_response(error)
        return jsonify(_sanitize_capture_payload(updated))

    @blueprint.route("/captures/<capture_id>/suggestions", methods=["POST"])
    def apply_contextual_suggestion(capture_id: str):
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        service = service_provider()
        try:
            created = service.apply_contextual_suggestion(
                uid,
                capture_id,
                suggestion_type=(payload.get("suggestion_type") or "").strip(),
            )
        except (KeyError, ValueError) as error:
            return _error_response(error)
        body = _sanitize_capture_payload(created)
        body["next_route"] = f"/today/result/{body['capture_id']}"
        return jsonify(body), 201

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
        return jsonify(_sanitize_capture_payload(updated))

    return blueprint
