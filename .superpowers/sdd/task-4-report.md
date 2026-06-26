# Task 4 Report

## Scope

- Implemented only the owned files:
  - `src/orchestration/destinations.py`
  - `tests/test_orchestration_destinations.py`
- Left unrelated existing untracked `.superpowers/sdd/*` files untouched.
- Kept this task local-only. No BoulderJS packet logic was added.

## Requirements Source

- Task brief: `.superpowers/sdd/task-4-brief.md`

## TDD Record

### Red

Added `tests/test_orchestration_destinations.py` with the required end-to-end local artifact test covering:

- local note bundle output
- local research note output
- local follow-up draft output
- event manifest persistence
- review queue persistence
- `external_drafts_pending` review flag

Verification command:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_destinations.py' -v
```

Observed failure:

```text
ModuleNotFoundError: No module named 'src.orchestration.destinations'
```

This confirmed the test was failing for the expected missing implementation.

### Green

Implemented `src/orchestration/destinations.py` with the minimal behavior required by the brief:

- `write_artifact_bundle(job, event, analysis, config) -> ArtifactBundle`
  - writes local JSON note bundles for `reflect_note_bundle` and `professional_note_bundle`
  - writes local JSON research artifacts for `research_note`
  - writes local draft files for `follow_up_bundle`
  - rejects unsupported local bundle types with `ValueError`
- `write_event_manifest(...) -> str`
  - writes a manifest JSON containing source event, analysis, suppressors, hard rules, LLM output, jobs, bundles, and a generated timestamp
- `write_review_queue_entry(...) -> str`
  - writes a local review queue JSON entry containing workflow jobs, generated artifacts, review flags, and forced-rule job tracking

Re-ran the same focused unittest command.

Observed result:

```text
Ran 1 test in 0.005s

OK
```

## Verification

Focused verification used as requested, because the full repo baseline remains noisy due to optional missing dependencies such as `firebase_admin`.

Command run:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_destinations.py' -v
```

Exit status: `0`

## Files Changed

- `src/orchestration/destinations.py`
- `tests/test_orchestration_destinations.py`

## Concerns

- None within Task 4 scope.
- The new module is intentionally local-only and does not yet include BoulderJS packet output, per the brief.

## Fix Pass 1

### Reviewer Findings Addressed

1. Moved manifest and review outputs to per-event directories with canonical filenames:
   - `manifests/<source_event_id>/event_manifest.json`
   - `review-queue/<source_event_id>/review_queue_entry.json`
2. Preserved append-only review history on reruns by also writing:
   - `review-queue/<source_event_id>/history/<timestamp>.json`

### TDD Record

#### Red

Extended `tests/test_orchestration_destinations.py` so it now verifies:

- canonical per-event manifest path naming
- canonical per-event review queue path naming
- canonical review path stability across two writes
- append-only review history creation across two writes
- latest history payload matches the canonical review payload

Verification command:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_destinations.py' -v
```

Observed failure:

```text
AssertionError: .../manifests/evt-1.json != .../manifests/evt-1/event_manifest.json
```

This confirmed the existing implementation still used the flat manifest path and did not yet satisfy the reviewer contract.

#### Green

Updated `src/orchestration/destinations.py` to:

- route event manifests into a per-event directory with canonical filename
- route review queue entries into a per-event directory with canonical filename
- emit a timestamped history JSON entry on every review write before updating the canonical review entry

Re-ran the same focused unittest command.

Observed result:

```text
Ran 1 test in 0.006s

OK
```

### Fix Pass Verification

Command run:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_destinations.py' -v
```

Exit status: `0`
