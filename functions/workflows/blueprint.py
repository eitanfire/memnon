from __future__ import annotations

from flask import Blueprint, jsonify, request


def create_workflows_blueprint(verify_token, service_provider):
    blueprint = Blueprint("workflows", __name__)

    @blueprint.route("/captures", methods=["POST"])
    def create_capture():
        uid = verify_token(request)
        if not uid:
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        text = (payload.get("text") or "").strip()
        context_hint = (payload.get("context_hint") or "").strip()
        if len(text.split()) < 3:
            return jsonify({"error": "text too short"}), 400

        service = service_provider()
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

    return blueprint
