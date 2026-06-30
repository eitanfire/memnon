from __future__ import annotations

import re
from typing import Any

from .models import AnalysisResult, SourceEvent

PEOPLE_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\b")
KNOWN_ORGS = ("BoulderJS", "Credible", "Galvanize", "Claude", "Cursor", "OpenAI")


def analyze_source_event(event: SourceEvent, config: dict[str, Any]) -> AnalysisResult:
    text = event.transcript
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]

    named_people = [
        value
        for value in dict.fromkeys(PEOPLE_PATTERN.findall(text))
        if value not in KNOWN_ORGS and value not in {"At", "The"}
    ]
    named_orgs = [org for org in KNOWN_ORGS if org.lower() in text.lower()]
    commitments = [
        sentence
        for sentence in sentences
        if any(
            token in sentence.lower()
            for token in ["follow up", "send", "introduce", "meet", "coordinate"]
        )
    ]
    follow_up_requests = [
        item for item in commitments if "follow up" in item.lower()
    ]
    product_feedback = [
        sentence
        for sentence in sentences
        if any(
            token in sentence.lower()
            for token in ["product", "demo", "onboarding", "workflow", "ui", "ux"]
        )
    ]
    publishable_angles = [
        sentence
        for sentence in sentences
        if any(
            token in sentence.lower()
            for token in ["trusted answers", "messy data", "semantic", "context"]
        )
    ]

    if "boulderjs" in text.lower():
        event_type = "boulderjs_demo"
    elif event.workflow == "reflect":
        event_type = "reflection"
    else:
        event_type = "professional_note"

    analysis = AnalysisResult(
        event_type=event_type,
        named_people=named_people,
        named_orgs=named_orgs,
        commitments=commitments,
        follow_up_requests=follow_up_requests,
        product_feedback=product_feedback,
        research_signals=[
            item
            for item in product_feedback
            if "teacher" in item.lower() or "school" in item.lower()
        ],
        publishable_angles=publishable_angles,
        reflection_signals=["reflection"] if event.workflow == "reflect" else [],
        professional_signals=["professional"] if event.workflow == "professional" else [],
    )

    if config.get("orchestration", {}).get("enable_llm_enrichment"):
        return enrich_analysis_with_llm(event, analysis, config)
    return analysis


def enrich_analysis_with_llm(
    event: SourceEvent,
    analysis: AnalysisResult,
    config: dict[str, Any],
) -> AnalysisResult:
    del event
    del config
    analysis.llm_reasoning = "LLM enrichment is unsupported in v1; deterministic extraction used."
    return analysis
