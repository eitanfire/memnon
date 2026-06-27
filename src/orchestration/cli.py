from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parents[2]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

from src.voice_pipeline import load_config
from src.orchestration.engine import orchestrate_from_metadata


def run_orchestration_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--metadata", required=True)
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    result = orchestrate_from_metadata(Path(args.metadata), config)
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_orchestration_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
