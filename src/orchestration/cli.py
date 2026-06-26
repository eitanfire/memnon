from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.voice_pipeline import load_config

from .engine import orchestrate_from_metadata


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
