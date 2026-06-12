import json
import math
import os
import time
from typing import Any, Optional

try:
    from huggingface_hub import InferenceClient
except Exception:  # pragma: no cover - defensive fallback
    InferenceClient = None


EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_PROVIDER = os.environ.get("HUGGING_FACE_PROVIDER", "hf-inference").strip() or "hf-inference"
EMBEDDING_TIMEOUT_SECONDS = float(os.environ.get("HUGGING_FACE_TIMEOUT_SECONDS", "30"))
EMBEDDING_FAILURE_COOLDOWN_SECONDS = float(os.environ.get("HUGGING_FACE_FAILURE_COOLDOWN_SECONDS", "120"))

_EMBEDDING_COOLDOWN_UNTIL = 0.0
_EMBEDDING_LAST_FAILURE: dict[str, Any] = {}


def _log_hf_event(event: str, **payload: Any) -> None:
    print(json.dumps({
        "component": "hf_inference",
        "event": event,
        **payload,
    }, ensure_ascii=True, sort_keys=True))


def _normalize_model_label(model: Optional[str]) -> str:
    return model or "<default>"


def _classify_hf_error(exc: Exception) -> str:
    status_code = getattr(exc, "response_status_code", None) or getattr(exc, "status_code", None)
    if status_code in (401, 403):
        return "auth"
    if status_code == 429:
        return "rate_limit"
    if status_code and int(status_code) >= 500:
        return "provider_unavailable"

    message = str(exc).lower()
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "unauthorized" in message or "forbidden" in message or "invalid token" in message:
        return "auth"
    if "rate limit" in message or "too many requests" in message:
        return "rate_limit"
    if "service unavailable" in message or "bad gateway" in message or "temporarily unavailable" in message:
        return "provider_unavailable"
    if "json" in message or "parse" in message or "shape" in message:
        return "invalid_response"
    return "unknown"


def _cooldown_active() -> bool:
    return time.monotonic() < _EMBEDDING_COOLDOWN_UNTIL


def _start_cooldown(reason: str, model: Optional[str], exc: Exception) -> None:
    global _EMBEDDING_COOLDOWN_UNTIL, _EMBEDDING_LAST_FAILURE
    _EMBEDDING_COOLDOWN_UNTIL = time.monotonic() + EMBEDDING_FAILURE_COOLDOWN_SECONDS
    _EMBEDDING_LAST_FAILURE = {
        "reason": reason,
        "model": _normalize_model_label(model),
        "message": str(exc),
        "cooldown_seconds": EMBEDDING_FAILURE_COOLDOWN_SECONDS,
    }
    _log_hf_event("embedding_cooldown_started", **_EMBEDDING_LAST_FAILURE)


def embedding_runtime_status() -> dict[str, Any]:
    remaining = max(0.0, _EMBEDDING_COOLDOWN_UNTIL - time.monotonic())
    return {
        "provider": EMBEDDING_PROVIDER,
        "default_model": EMBEDDING_MODEL,
        "timeout_seconds": EMBEDDING_TIMEOUT_SECONDS,
        "cooldown_seconds": EMBEDDING_FAILURE_COOLDOWN_SECONDS,
        "cooldown_active": remaining > 0,
        "cooldown_remaining_seconds": round(remaining, 2),
        "last_failure": _EMBEDDING_LAST_FAILURE or None,
    }


def _mean_pool(matrix: list[list[float]]) -> list[float]:
    if not matrix:
        return []
    width = len(matrix[0])
    totals = [0.0] * width
    for row in matrix:
        if len(row) != width:
            continue
        for idx, value in enumerate(row):
            totals[idx] += float(value)
    return [value / max(len(matrix), 1) for value in totals]


def _coerce_embedding(payload: Any) -> list[float]:
    if payload is None:
        return []
    if hasattr(payload, "tolist"):
        payload = payload.tolist()
    if not isinstance(payload, list) or not payload:
        return []
    first = payload[0]
    if isinstance(first, list):
        return _mean_pool([[float(v) for v in row] for row in payload if isinstance(row, list)])
    return [float(v) for v in payload]


def embed_text_details(text: str, api_key: str, model: str = EMBEDDING_MODEL) -> dict[str, Any]:
    normalized_text = (text or "").strip()
    if InferenceClient is None:
        return {
            "vector": [],
            "model": model,
            "provider": EMBEDDING_PROVIDER,
            "error_type": "client_unavailable",
            "skipped": True,
        }
    if not api_key or not normalized_text:
        return {
            "vector": [],
            "model": model,
            "provider": EMBEDDING_PROVIDER,
            "error_type": "missing_input",
            "skipped": True,
        }
    if _cooldown_active():
        status = embedding_runtime_status()
        _log_hf_event("embedding_skipped_cooldown", requested_model=model, cooldown_remaining_seconds=status["cooldown_remaining_seconds"])
        return {
            "vector": [],
            "model": model,
            "provider": EMBEDDING_PROVIDER,
            "error_type": "cooldown_active",
            "cooldown_remaining_seconds": status["cooldown_remaining_seconds"],
            "skipped": True,
        }

    candidates = [model]
    if model:
        candidates.append(None)

    try:
        client = InferenceClient(api_key=api_key, provider=EMBEDDING_PROVIDER, timeout=EMBEDDING_TIMEOUT_SECONDS)
    except TypeError:
        client = InferenceClient(api_key=api_key, timeout=EMBEDDING_TIMEOUT_SECONDS)

    started = time.monotonic()
    last_exc: Optional[Exception] = None
    for candidate_model in candidates:
        try:
            if candidate_model:
                embedding = client.feature_extraction(normalized_text, model=candidate_model)
            else:
                embedding = client.feature_extraction(normalized_text)
            vector = _coerce_embedding(embedding)
            if vector:
                elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                resolved_model = candidate_model or model
                _log_hf_event(
                    "embedding_success",
                    model=_normalize_model_label(resolved_model),
                    provider=EMBEDDING_PROVIDER,
                    dimensions=len(vector),
                    elapsed_ms=elapsed_ms,
                )
                return {
                    "vector": vector,
                    "model": resolved_model,
                    "provider": EMBEDDING_PROVIDER,
                    "dimensions": len(vector),
                    "elapsed_ms": elapsed_ms,
                    "error_type": None,
                    "skipped": False,
                }
        except Exception as exc:
            last_exc = exc
            reason = _classify_hf_error(exc)
            _log_hf_event(
                "embedding_attempt_failed",
                model=_normalize_model_label(candidate_model),
                provider=EMBEDDING_PROVIDER,
                error_type=reason,
                message=str(exc),
            )
            if reason in {"auth", "rate_limit", "provider_unavailable", "timeout"}:
                _start_cooldown(reason, candidate_model, exc)
                break

    return {
        "vector": [],
        "model": model,
        "provider": EMBEDDING_PROVIDER,
        "error_type": _classify_hf_error(last_exc) if last_exc else "unknown",
        "skipped": False,
    }


def embed_text(text: str, api_key: str, model: str = EMBEDDING_MODEL) -> list[float]:
    return embed_text_details(text, api_key, model=model).get("vector", [])


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def rerank_candidates(
    query_embedding: list[float],
    candidates: list[dict],
) -> list[dict]:
    if not query_embedding:
        return sorted(candidates, key=lambda item: item.get("base_score", 0), reverse=True)

    def key(candidate: dict) -> tuple[float, float]:
        embedding = candidate.get("embedding_v1") or []
        semantic = cosine_similarity(query_embedding, embedding) if embedding else 0.0
        return (semantic, float(candidate.get("base_score", 0)))

    return sorted(candidates, key=key, reverse=True)
