# Task 1 Report: Stabilize Contracts, Config, and Metadata Inputs

## Scope

Implemented changes only in the task-owned files:

- `src/orchestration/__init__.py`
- `src/orchestration/config.py`
- `src/orchestration/models.py`
- `schemas/source-event.schema.json`
- `schemas/workflow-job.schema.json`
- `schemas/artifact-bundle.schema.json`
- `schemas/review-queue-entry.schema.json`
- `tests/test_orchestration_contracts.py`
- `src/voice_pipeline.py`
- `config.example.json`

## TDD Record

### Red

Added `tests/test_orchestration_contracts.py` with the exact contract/config test from the brief, then ran:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_contracts.py' -v
```

Observed expected failure:

- `ModuleNotFoundError: No module named 'src.orchestration'`

This confirmed the new contract surface did not exist before implementation.

### Green

Implemented the minimal production changes needed to satisfy the new contract:

- Added `build_orchestration_config(config: dict[str, Any]) -> dict[str, Any]`
- Added orchestration dataclasses and `to_dict()` serialization helpers
- Added schema files for source event, workflow job, artifact bundle, and review queue entry
- Updated `transcribe_audio()` to return `(transcript, transcript_path)`
- Updated `write_metadata()` to accept and persist:
  - `entry_id`
  - `workflow`
  - `routing_reason`
  - `title`
  - `transcript_path`
  - `source_event_id`
- Added example orchestration config block to `config.example.json`

One intermediate test failure surfaced during green:

- `build_orchestration_config()` used `Path.resolve()`, which normalized `/tmp/...` to `/private/tmp/...` on macOS and broke the exact expected value.

Removed that normalization and reran the same focused test.

## Verification

Fresh verification command:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_contracts.py' -v
```

Result:

- `Ran 2 tests in 0.002s`
- `OK`

## Notes

- Verification was intentionally limited to the task-specific test file, per the environment note about missing optional dependencies in the broader repo baseline.
- No unrelated tracked changes were reverted.

## Outcome

Task 1 requirements were implemented with the requested TDD flow and focused `unittest` verification.

## Fix Pass: Reviewer Findings

### Scope

Addressed both reviewer findings within the original Task 1 file scope:

- `src/orchestration/config.py`
- `schemas/review-queue-entry.schema.json`
- `tests/test_orchestration_contracts.py`

### Review Findings Interpreted

1. `build_orchestration_config()` must treat `orchestration.runtime_dir` like other config-relative paths and anchor non-absolute values to `_config_dir`.
2. The review queue schema must constrain `workflow_jobs` to the concrete workflow-job contract instead of arbitrary object payloads.

### TDD Record

#### Red

Added focused test coverage for both findings, then ran:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_contracts.py' -v
```

Observed expected failures:

- `test_build_orchestration_config_resolves_relative_runtime_dir_from_config_dir`
  - actual: `custom-orchestration`
  - expected: `/tmp/memnon-config/custom-orchestration`
- `test_review_queue_schema_reuses_workflow_job_shape`
  - actual schema item: `{"type": "object"}`
  - expected schema item: `{"$ref": "./workflow-job.schema.json"}`

This confirmed both review findings reproduced before implementation.

#### Green

Applied the minimal fixes:

- Updated `build_orchestration_config()` to expand `orchestration.runtime_dir` and anchor non-absolute values to `_config_dir`.
- Tightened `schemas/review-queue-entry.schema.json` so `workflow_jobs.items` uses `"$ref": "./workflow-job.schema.json"`.

One intermediate rerun exposed the same macOS `/tmp` to `/private/tmp` normalization issue seen in the initial pass, this time on the config-relative path branch. Removed that canonicalization and reran the focused suite.

### Verification

Fresh verification command:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_contracts.py' -v
```

Result:

- `Ran 4 tests in 0.003s`
- `OK`
