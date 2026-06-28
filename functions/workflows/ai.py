from __future__ import annotations

import json
import os
import urllib.request


def generate_professional_note(source_text: str, context_hint: str, profile: dict, api_key: str) -> dict:
    prompt = f"""Turn this captured thought into one useful professional note.

Return strict JSON with:
- title
- framing_line
- key_point
- next_step

Rules:
- one note only
- title should be specific enough to feel worth revisiting
- framing_line should explain what kind of saved object this is in content-aware language
- key_point should be one grounded, concrete takeaway
- next_step should be one concrete action when the source supports it
- if the source does not support a concrete next step, return an empty string for next_step
- if the source looks like pasted document material, synthesize what the material is for or why it matters; do not merely describe the sections it contains
- for document-like source text, infer the organizing next action; do not copy a stray sentence verbatim unless it is explicitly marked as an action or next step
- practical, direct language
- do not mention workflows, confidence, or routing

Saved profile:
- lane: {profile.get("lane", "professional")}
- profession: {profile.get("profession", "professional")}
- reflection_style: {profile.get("reflection_style", "practical")}

Context hint: {context_hint or "(none)"}

Source text:
{source_text}
"""

    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You return strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        raw = json.loads(response.read().decode("utf-8"))
    content = raw["choices"][0]["message"]["content"]
    return json.loads(content)


def load_openai_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "")
