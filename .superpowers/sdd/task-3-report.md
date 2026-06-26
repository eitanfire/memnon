# Task 3 Report: Routing Policies and Confidence Handling

## Scope

- Worked only in `src/orchestration/policies.py` and `tests/test_orchestration_policies.py`.
- Left unrelated untracked `.superpowers/` files untouched.

## Requirements Check

- Implemented suppressors before hard rules, then thresholded LLM output.
- Preserved rule/LLM disagreement handling with `forced_by_rule=True` and `review_priority="high"` when a hard rule fires against low-confidence LLM output.
- Kept verification focused to the Task 3 unittest file because the repo-wide baseline remains noisy from optional dependencies.

## TDD Record

### Red

1. Added the failing test file from the task brief at `tests/test_orchestration_policies.py`.
2. Ran:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_policies.py' -v
```

3. Observed expected failure:

```text
ModuleNotFoundError: No module named 'src.orchestration.policies'
```

This confirmed the new test was actually exercising missing production code.

### Green

1. Added `src/orchestration/policies.py` with:
   - `apply_suppressors(event, analysis) -> list[str]`
   - `apply_hard_rules(event, analysis) -> list[str]`
   - `select_workflow_jobs(event, analysis, llm_output) -> list[WorkflowJob]`
2. Implemented the minimal behavior from the approved brief:
   - suppress `follow_up_bundle` when there are no named people, orgs, or commitments
   - suppress `boulderjs_recap_packet` for reflection/private reflection events
   - suppress `research_note` for very short transcripts
   - force `boulderjs_recap_packet` for `boulderjs_demo`
   - force `professional_note_bundle` for `workflow == "professional"`
   - force `reflect_note_bundle` for `workflow == "reflect"`
   - admit LLM jobs only at confidence `>= 0.50`
   - mark thresholded jobs with `needs_review=True` when confidence is `< 0.80`
   - mark forced jobs with `forced_by_rule=True` and `review_priority="high"` when the LLM confidence is `< 0.50`

### Verification

Ran fresh after implementation and again after commit:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_policies.py' -v
```

Result:

```text
Ran 2 tests in 0.000s

OK
```

## Commit

- `1fd2c7e` `feat: add orchestration routing policies`

## Concerns

- No blocking concerns within Task 3 scope.
- The production implementation follows the exact brief, including the hard-rule confidence behavior for `_note_bundle` workflows. I did not widen scope beyond the approved task.

## Fix Pass: Reviewer Findings

### Scope

- Stayed within Task 3 write scope:
  - `src/orchestration/policies.py`
  - `tests/test_orchestration_policies.py`
- Appended this fix-pass report to the same Task 3 report file.

### Findings Addressed

1. `select_workflow_jobs` now rejects any LLM key outside the v1 workflow set.
2. Suppressors now have precedence over hard rules, so a suppressed workflow cannot also be emitted as a forced job.

### RED

Added two focused regression tests first:

- `test_out_of_scope_llm_workflow_keys_are_rejected`
- `test_suppressors_take_precedence_over_hard_rules`

Then ran:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_policies.py' -v
```

Observed expected failures:

```text
FAIL: test_out_of_scope_llm_workflow_keys_are_rejected
AssertionError: Items in the first set but not the second:
'unknown_bundle'

FAIL: test_suppressors_take_precedence_over_hard_rules
AssertionError: 'boulderjs_recap_packet' unexpectedly found in {'professional_note_bundle', 'boulderjs_recap_packet'}
```

This confirmed both reviewer findings reproduced before the implementation change.

### GREEN

Updated `src/orchestration/policies.py` to:

- define the canonical v1 workflow set in-module
- ignore any `llm_output` key not in that v1 set
- subtract suppressed workflows from the forced workflow set before job creation
- remove the `# type: ignore[arg-type]` escape hatch and replace it with explicit workflow-type narrowing

### Verification

Ran fresh after the fix:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_policies.py' -v
```

Result:

```text
Ran 4 tests in 0.000s

OK
```
