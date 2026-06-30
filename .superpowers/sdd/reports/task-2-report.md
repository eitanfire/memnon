# Task 2 Report: Strengthen backend saved-result shaping for general captures

## What I implemented

- Tightened the saved-result prompt in `functions/workflows/ai.py` so it explicitly:
  - prefers concrete topics/people/products/decisions over generic role labels
  - avoids injecting teacher framing when the source is not teacher-specific
  - treats `framing_line` as a reason the saved object is worth reopening
- Updated the local fallback generator in `functions/workflows/local_app.py` to:
  - use `derive_specific_title(source_text, context_hint, "Saved note")`
  - use `derive_key_point(...)` instead of a generic placeholder
  - emit `Shaped from your note into one saved result worth reopening.`
  - leave `next_step` empty when `allow_next_step=False`
- Strengthened backend shaping in `functions/workflows/service.py` by:
  - deriving imperative next steps from source text starting with `send` or `ask`
  - preferring a grounded source-derived next step when the model proposes a mismatched generic action
  - suppressing `Next step` sections when the capture does not support surfacing an action
  - preserving inferred next steps for document-like captures and concrete product-direction `should` statements
  - centralizing section construction through `_build_primary_sections(...)`
- Added a prompt-level regression test in `tests/test_workflows_result_quality.py` to verify non-teacher restraint and the new framing guidance.

## TDD RED/GREEN evidence

### RED

1. Existing parity failures before production edits:

```bash
/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality tests.test_workflows_service -v
```

Result:
- `FAIL`: `test_follow_up_note_prefers_action_shaped_next_step`
- `FAIL`: `test_non_action_capture_can_omit_next_step`

2. New failing prompt-restraint test before prompt edits:

```bash
/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality.WorkflowResultQualityTests.test_generated_output_does_not_default_to_professional_labeling -v
```

Result:
- `FAIL`: missing prompt rule `if the source is not teacher-specific, do not inject teacher framing from the saved profile`

### GREEN

1. New prompt-restraint test after prompt update:

```bash
/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality.WorkflowResultQualityTests.test_generated_output_does_not_default_to_professional_labeling -v
```

Result:
- `OK`

2. Targeted parity failures after service shaping changes:

```bash
/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality.WorkflowResultQualityTests.test_follow_up_note_prefers_action_shaped_next_step tests.test_workflows_result_quality.WorkflowResultQualityTests.test_non_action_capture_can_omit_next_step -v
```

Result:
- `OK`

3. Full focused suite from the brief:

```bash
/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality tests.test_workflows_service tests.test_workflows_api -v
```

Result:
- `Ran 48 tests`
- `OK`

## Tests run and results

- `/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality tests.test_workflows_service -v`
  - initial RED, 2 failures
- `/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality.WorkflowResultQualityTests.test_generated_output_does_not_default_to_professional_labeling -v`
  - RED, then GREEN after prompt change
- `/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality.WorkflowResultQualityTests.test_follow_up_note_prefers_action_shaped_next_step tests.test_workflows_result_quality.WorkflowResultQualityTests.test_non_action_capture_can_omit_next_step -v`
  - GREEN
- `/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_service.WorkflowServiceTests.test_service_sharpens_generic_product_note_output -v`
  - GREEN after widening the next-step gate for concrete product-direction `should` language
- `/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality tests.test_workflows_service tests.test_workflows_api -v`
  - GREEN, 48 tests passed

## Files changed

- `functions/workflows/ai.py`
- `functions/workflows/local_app.py`
- `functions/workflows/service.py`
- `tests/test_workflows_result_quality.py`

## Self-review findings

- The next-step suppression is intentionally conservative for text captures and is now driven by explicit action support instead of blindly trusting generic model output.
- Document-like captures still preserve an inferred organizing next step.
- Non-teacher captures remain free of teacher-flavored framing at the prompt and fallback layers.
- I did not modify workflow families, thread states, routes, or teacher-specific behavior outside the non-teacher neutrality guard.

## Concerns

- The full focused suite passed, but the test run still surfaces an existing `ResourceWarning` from the API test process about an unclosed file handle. It did not fail the suite and was outside this task’s write scope.

---

## Fix pass: review findings follow-up

### What I changed

- Tightened `functions/workflows/local_app.py` so the local fallback generator only emits `next_step` when `_should_surface_next_step(...)` says the source clearly supports one. This removes the local/service inconsistency for non-action general captures.
- Narrowed the `functions/workflows/service.py` `derive_artifact_next_step(...)` override:
  - generic model actions still fall back to grounded extraction
  - clearly mismatched model actions still fall back to grounded extraction
  - specific model actions that stay meaningfully aligned with the source now survive even when they are not verbatim extracts
- Replaced the weak prompt-restraint regression in `tests/test_workflows_result_quality.py` with a prompt-sensitive fake responder that only returns neutral framing when the actual prompt includes the required non-teacher restraint and saved-result framing guidance.
- Added focused regression coverage in `tests/test_workflows_result_quality.py` for:
  - the local fallback generator on a non-action benchmark input
  - preservation of a good specific model-generated next step

### TDD evidence

#### RED

```bash
/Users/eitan/memnon/functions/venv/bin/python -m unittest \
  tests.test_workflows_result_quality.WorkflowResultQualityTests.test_generated_output_does_not_default_to_professional_labeling \
  tests.test_workflows_result_quality.WorkflowResultQualityTests.test_local_fallback_generator_omits_next_step_for_non_action_note \
  tests.test_workflows_result_quality.WorkflowResultQualityTests.test_specific_model_next_step_survives_when_grounded_in_source -v
```

Result before production edits:
- `FAIL`: `test_local_fallback_generator_omits_next_step_for_non_action_note`
- `FAIL`: `test_specific_model_next_step_survives_when_grounded_in_source`
- prompt-restraint test stayed meaningful and green because it now validates prompt-conditioned behavior instead of a trivial mocked payload

#### GREEN

```bash
/Users/eitan/memnon/functions/venv/bin/python -m unittest \
  tests.test_workflows_result_quality.WorkflowResultQualityTests.test_generated_output_does_not_default_to_professional_labeling \
  tests.test_workflows_result_quality.WorkflowResultQualityTests.test_local_fallback_generator_omits_next_step_for_non_action_note \
  tests.test_workflows_result_quality.WorkflowResultQualityTests.test_specific_model_next_step_survives_when_grounded_in_source \
  tests.test_workflows_result_quality.WorkflowResultQualityTests.test_follow_up_note_prefers_action_shaped_next_step \
  tests.test_workflows_result_quality.WorkflowResultQualityTests.test_non_action_capture_can_omit_next_step -v
```

Result after the fix:
- `Ran 5 tests`
- `OK`

### Tests run

```bash
/Users/eitan/memnon/functions/venv/bin/python -m unittest tests.test_workflows_result_quality tests.test_workflows_service tests.test_workflows_api -v
```

Result:
- `Ran 50 tests`
- `OK`

### Remaining concerns

- The focused suite still emits an existing `ResourceWarning` from the API test process about an unclosed file handle. The suite passed, and this remains outside the requested scope and write list.
