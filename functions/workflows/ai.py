from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import urllib.request


def _call_json_completion(prompt: str, api_key: str) -> dict:
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


def generate_professional_note(
    source_text: str,
    context_hint: str,
    profile: dict,
    api_key: str,
    *,
    allow_next_step: bool = True,
) -> dict:
    next_step_rule = (
        "next_step should be one concrete action when the source supports it\n"
        "- if the source does not support a concrete next step, return an empty string for next_step"
        if allow_next_step
        else "return an empty string for next_step\n"
        "- do not infer or invent a next step when the source does not explicitly contain one"
    )
    prompt = f"""Turn this captured thought into one useful professional note.

Return strict JSON with:
- title
- framing_line
- key_point
- next_step

Rules:
- one note only
- title should be specific enough to feel worth revisiting, but short enough to read like a saved note title rather than a headline
- prefer the concrete topic, person, product, or decision over generic role labels
- if the source is not teacher-specific, do not inject teacher framing from the saved profile
- framing_line should describe why this saved object is worth keeping, not just that it is professional
- framing_line should read like a saved object worth reopening, not an executive summary
- key_point should be one grounded, concrete takeaway
- when the source has multiple sentences, prefer the sentence with the clearest takeaway instead of repeating the opening scene-setting sentence
- {next_step_rule}
- if next_step is present, keep key_point distinct from the action; do not use the same sentence for both
- if the source looks like pasted document material, synthesize what the material is for or why it matters; do not merely describe the sections it contains
- for document-like source text, infer the organizing next action; do not copy a stray sentence verbatim unless it is explicitly marked as an action or next step
- practical, direct language
- avoid phrases like "professional note worth shaping", "important for ensuring", "crucial to ensure", "competitive landscape", or "clear direction for positioning"
- do not mention workflows, confidence, or routing
- use the saved profile only when it clearly matches the source text or context hint
- do not import domain-specific interpretations that are not present in the source text

Optional saved profile:
- lane: {profile.get("lane", "professional")}
- profession: {profile.get("profession", "professional")}
- reflection_style: {profile.get("reflection_style", "practical")}

Context hint: {context_hint or "(none)"}

Source text:
{source_text}
"""
    return _call_json_completion(prompt, api_key)


def generate_social_post(
    source_text: str,
    context_hint: str,
    profile: dict,
    api_key: str,
) -> dict:
    prompt = f"""Turn this saved result into one concise public-facing social post draft.

Return strict JSON with:
- title
- framing_line
- body
- sections

Rules:
- one draft only
- body should read like a clean post draft the user can copy as-is
- keep it public-facing and concise
- stay grounded in the source text
- do not expose internal workflow language
- do not add hashtags unless the source clearly supports them
- sections may be an empty array or at most one short support section
- do not mention derivation or lineage

Optional saved profile:
- lane: {profile.get("lane", "professional")}
- profession: {profile.get("profession", "professional")}

Context hint: {context_hint or "(none)"}

Source text:
{source_text}
"""
    return _call_json_completion(prompt, api_key)


def generate_professional_analysis(
    source_text: str,
    context_hint: str,
    profile: dict,
    api_key: str,
) -> dict:
    prompt = f"""Turn this saved result into one concise professional analysis.

Return strict JSON with:
- title
- framing_line
- body
- sections

Rules:
- one analysis only
- body should summarize the central read in plain language
- sections should contain 2 or 3 short analytical blocks with label and text
- keep it grounded in the source text
- do not add teacher-specific framing unless the source clearly supports it
- do not expose workflow language, derivation, or lineage
- avoid generic business filler

Optional saved profile:
- lane: {profile.get("lane", "professional")}
- profession: {profile.get("profession", "professional")}

Context hint: {context_hint or "(none)"}

Source text:
{source_text}
"""
    return _call_json_completion(prompt, api_key)


def load_openai_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "")


def transcribe_audio_bytes(audio_bytes: bytes, filename: str, api_key: str) -> str:
    boundary = "MemnonWhisper" + hashlib.md5(audio_bytes[:64]).hexdigest()
    ext = Path(filename).suffix.lower() or ".m4a"
    mime = "audio/mp4" if ext in (".m4a", ".mp4") else f"audio/{ext.lstrip('.')}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\nen\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read())["text"].strip()
