# Task 3 Report

## Scope

Implemented automatic existing-thread suggestion ranking and the final v1 result-page behavior for workflows captures, limited to:

- `functions/workflows/service.py`
- `public/workflows.js`
- `public/workflows.css`
- `tests/test_workflows_threading.py`
- `tests/test_workflows_service.py`
- `tests/test_workflows_static_contract.py`

## What Changed

### Backend

- Added `WorkflowService.suggest_context_for_capture(uid, record)`.
- Added narrow ranked matching with the brief’s intended signals:
  - explicit context hint exact/near match
  - title/topic phrase overlap
  - recent activity boost
  - prior confirmed attachment pattern boost
  - repeated named-entity reuse from prior confirmed captures
- Added suppressive gating so weak saved notes and non-clean voice captures do not suggest a thread.
- Persisted immediate suggestion state onto newly created capture records.
- Kept reopened saved results quiet by stripping suggestion fields from `get_capture(...)` payloads while still showing a confirmed thread quietly when present.
- Fixed `list_active_contexts()` service access so repository implementations with `list_active_contexts(...)` are used directly.

### Frontend

- Replaced the generic Task 2 “ongoing thread” block with the final Task 3 immediate prompt:
  - `This looks related to <thread>.`
  - `Continue there`
  - `Keep separate`
  - `Choose another`
- Reused the existing inline chooser path for `Choose another`.
- Added quiet confirmed-thread display text for confirmed reopened results:
  - `Related to <thread>`
- Kept reopened unconfirmed results quiet.
- Added minimal styling for the final prompt and quiet action.

### Tests

- Added `tests/test_workflows_threading.py` for ranking and suppression behavior.
- Added service coverage for immediate capture suggestion payload shape.
- Updated static contract coverage for final result-page thread rendering behavior.

## TDD Notes

- Wrote failing threading tests first.
- Verified red state with:
  - `.venv/bin/python -m unittest tests.test_workflows_threading -v`
  - `.venv/bin/python -m unittest tests.test_workflows_service.WorkflowServiceTests.test_immediate_capture_includes_ranked_thread_suggestion -v`
- Implemented the minimal ranking/rendering changes needed to turn them green.

## Verification

### Automated

Passed:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest tests.test_workflows_routing tests.test_workflows_service tests.test_workflows_threading tests.test_workflows_local_app tests.test_workflows_api tests.test_workflows_static_contract -v
node --check public/workflows.js
```

Result: `70` Python tests passed, `node --check` passed.

### Manual browser verification

Verified locally with:

- backend: `WORKFLOWS_LOCAL_STORAGE_PATH=/tmp/workflows-task3-manual.json PYTHONPATH=functions .venv/bin/python -m workflows.local_app`
- static app: `.venv/bin/python scripts/run_workflows_static.py`
- URL: `http://127.0.0.1:8000/workflows`

Observed:

1. Capture with no active threads: no suggestion block shown.
2. Capture with one clear matching thread: suggestion block shown.
3. `Continue there`: confirms the thread and replaces the prompt with quiet confirmed text.
4. `Keep separate`: removes the prompt and leaves the result standalone.
5. Reopened saved result with confirmed thread: shows quiet `Related to ...` text only.
6. Reopened saved result with no confirmed thread: shows no thread UI.
7. Weak capture with active thread present: shows no suggestion block.
8. Extra sanity check: `Choose another` reveals the existing inline chooser rather than a new main-page flow.

## Deferred Items Left Deferred

Confirmed still deferred:

- suggestions on reopened saved results
- multi-thread membership
- archived thread suggestion
- thread merge/split
- hierarchy
- dashboard route
- folders/tags overhaul
- visible explanation UI
- visible confidence language
- autonomous clustering
- agentic thread orchestration

## Commit

Planned commit message:

`feat: add workflow thread suggestions`

## Review Fix Follow-Up

Applied the post-review suppression fix on top of commit `e0eceff`.

### Review Findings Fixed

1. Suppressed thread suggestions for ambiguous saved notes with `saved_note_artifact.state == "needs_direction"`, not just `weak_signal`.
2. Added explicit ranking coverage showing `suggest_context_for_capture(...)` returns `None` when:
   - active threads exist but the evidence stays below the score threshold
   - active threads exist but the best match is too close to the runner-up

### TDD Red/Green

Verified the bug first with:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest tests.test_workflows_threading.WorkflowThreadingTests.test_no_suggestion_for_ambiguous_saved_note -v
```

Result before the fix: `FAIL` because a `needs_direction` saved note with a strong context hint still returned a ranked suggestion.

### Covering Test Re-run

Ran:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest tests.test_workflows_threading -v
.venv/bin/python -m unittest tests.test_workflows_service -v
```

Result after the fix:

- `tests.test_workflows_threading`: `8` tests passed
- `tests.test_workflows_service`: `26` tests passed

### Scope

Kept the fix narrow:

- no product-surface changes
- reopened results remain quiet
- suggestion logic is more suppressive, not broader
