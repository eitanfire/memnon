from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def build_orchestration_config(config: dict[str, Any]) -> dict[str, Any]:
    runtime_dir = Path(config["runtime_dir"])
    existing = dict(config.get("orchestration", {}))
    raw_base_dir = existing.get("runtime_dir")
    if raw_base_dir:
        expanded = Path(os.path.expandvars(os.path.expanduser(raw_base_dir)))
        if expanded.is_absolute():
            base_dir = expanded
        else:
            config_dir = Path(config.get("_config_dir", "."))
            base_dir = config_dir / expanded
    else:
        base_dir = runtime_dir / "orchestration"

    return {
        "enabled": bool(existing.get("enabled", False)),
        "runtime_dir": str(base_dir),
        "manifests_dir": str(base_dir / "manifests"),
        "review_queue_dir": str(base_dir / "review-queue"),
        "outbox_dir": str(base_dir / "outbox"),
        "research_dir": str(base_dir / "research"),
        "note_bundles_dir": str(base_dir / "note-bundles"),
        "boulderjs_dir": str(base_dir / "boulderjs"),
        "enable_llm_enrichment": bool(existing.get("enable_llm_enrichment", True)),
        "social_agent_repo_dir": existing.get("social_agent_repo_dir", ""),
        "run_social_agent_cli": bool(existing.get("run_social_agent_cli", False)),
        "default_boulderjs_event_number": existing.get("default_boulderjs_event_number"),
        "default_boulderjs_talk_number": existing.get("default_boulderjs_talk_number"),
    }
