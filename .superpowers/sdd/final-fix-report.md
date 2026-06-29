# Final Fix Report: Workflows Context Thread Continuity

## Scope

Implemented the final whole-branch fix wave for the remaining Important gap in thread chooser continuity:

- `Choose another` now supports:
  - selecting another existing thread
  - creating a new thread inside the chooser
- backend decision handling now distinguishes:
  - `confirmed`
  - `selected_different_context`
  - `created_new_context`

Included the two requested low-risk cleanups:

- Firestore `workflow_contexts` documents now persist `context_id`
- removed the duplicate `display: grid` declaration from `.workflows-related-thread-block`

## Changes Made

### Backend

Files:

- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/service.py`
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/repository.py`

Changes:

- extended `apply_context_decision()` to support `selected_different_context`
- extended `apply_context_decision()` to support `created_new_context`
- `created_new_context` now creates a context with `seed_capture_id` set to the current capture
- both new decision paths clear any active suggestion and persist quiet confirmed-thread state for reopen behavior
- Firestore context persistence now stores `context_id` inside the document body as required by the spec shape

### Frontend

Files:

- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/public/workflows.js`
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/public/workflows.css`

Changes:

- the main result-page suggestion surface still shows only:
  - `Continue there`
  - `Keep separate`
  - `Choose another`
- the chooser is now rendered lazily only after `Choose another` is clicked
- the chooser contains:
  - existing thread buttons
  - a create-new-thread form
- selecting an alternate existing thread now submits `selected_different_context`
- creating a thread from the chooser now submits `created_new_context`
- added minimal styling for the create-thread form
- removed the duplicate CSS declaration noted in review

## TDD Evidence

Added failing tests first for:

- alternate existing-thread selection records `selected_different_context`
- chooser-driven new-thread creation works and reopens as quiet confirmed display
- create-new-thread UI is exposed only in the chooser path, not the main page surface

Test files updated:

- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_service.py`
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_api.py`
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_static_contract.py`

## Verification

Focused red/green verification:

```bash
./.venv/bin/python -m unittest \
  tests.test_workflows_service.WorkflowServiceTests.test_selecting_another_existing_thread_records_alternate_decision \
  tests.test_workflows_service.WorkflowServiceTests.test_creating_new_thread_from_decision_confirms_it_quietly_on_reopen \
  tests.test_workflows_api.WorkflowApiTests.test_context_decision_endpoint_records_selected_different_context \
  tests.test_workflows_api.WorkflowApiTests.test_context_decision_endpoint_can_create_new_context \
  tests.test_workflows_static_contract.WorkflowsStaticContractTests.test_result_route_thread_controls_follow_immediate_result_rules \
  -v
```

Result: `5 tests, OK`

Covering workflows verification:

```bash
./.venv/bin/python -m unittest \
  tests.test_workflows_service \
  tests.test_workflows_api \
  tests.test_workflows_static_contract \
  tests.test_workflows_threading \
  tests.test_workflows_local_app \
  -v
```

Result: `71 tests, OK`

Broader workflows suite verification:

```bash
./.venv/bin/python -m unittest discover -s tests -p 'test_workflows*.py' -v
```

Result: `84 tests, OK`

## Notes

- The known unrelated `ResourceWarning` still appears during parts of the broader suite. It was explicitly out of scope for this fix wave and was not changed.
