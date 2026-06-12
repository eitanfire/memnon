import math
from typing import Any

try:
    from huggingface_hub import InferenceClient
except Exception:  # pragma: no cover - defensive fallback
    InferenceClient = None


EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


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


def embed_text(text: str, api_key: str, model: str = EMBEDDING_MODEL) -> list[float]:
    if InferenceClient is None or not api_key or not (text or "").strip():
        return []

    candidates = [model]
    if model:
        candidates.append(None)

    try:
        client = InferenceClient(api_key=api_key, provider="hf-inference", timeout=30)
    except TypeError:
        client = InferenceClient(api_key=api_key, timeout=30)

    for candidate_model in candidates:
        try:
            if candidate_model:
                embedding = client.feature_extraction(text, model=candidate_model)
            else:
                embedding = client.feature_extraction(text)
            vector = _coerce_embedding(embedding)
            if vector:
                return vector
        except Exception as exc:
            label = candidate_model or "<default>"
            print(f"[hf] feature_extraction failed for model={label}: {exc}")
    return []


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
