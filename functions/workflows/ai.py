from __future__ import annotations

import json
import os
import urllib.request


def generate_professional_note(source_text: str, context_hint: str, profile: dict, api_key: str) -> dict:
    prompt = f"""Turn this captured thought into one useful professional note.

Return strict JSON with:
- title
- framing_line
- body
- task_list

Rules:
- one note only
- no more than two task list items
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
