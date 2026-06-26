# Task 6 Report: Wire the Engine into Memnon Processing and Add a Re-Run CLI

Status: DONE

Commit:
- 88f4fc0 feat: integrate orchestration engine into voice pipeline

Implemented:
- Added `src/orchestration/engine.py` with `orchestrate_from_metadata(metadata_path, config)`.
- Engine loads source-event metadata, runs analysis, applies suppressors and hard rules, selects workflow jobs, writes local bundles, writes BoulderJS recap packets, writes the event manifest, and writes the review queue entry.
- Added deterministic workflow hints in the engine from existing analysis output so policy selection can produce follow-up and research jobs without changing Tasks 1-5 modules.
- Added `src/orchestration/cli.py` with `run_orchestration_cli(argv)` and `main(argv)`, loading config through `src.voice_pipeline.load_config()` and printing JSON results.
- Added `maybe_run_orchestration(metadata_path, config)` in `src/voice_pipeline.py`.
- Wired `process_file()` to call `maybe_run_orchestration()` only after `write_metadata()` returns a metadata path.
- Added `tests/test_orchestration_engine.py` covering manifest creation, review queue creation, and multiple artifact bundle generation.

TDD evidence:
- Initial red run with the test in the correct repo failed with `ModuleNotFoundError: No module named 'src.orchestration.engine'`.
- First green attempt exposed a real integration mismatch: the brief skeleton's empty LLM output yielded only two jobs with existing policy behavior.
- Root cause: `select_workflow_jobs()` only emits hard-rule jobs unless candidate workflow hints are provided; existing policy tests already model follow-up and research hints as policy input.
- Minimal fix: derive follow-up and research candidate hints inside the engine from the already-computed analysis result, then continue to let policies apply suppressors and hard rules.

Verification:
- `python3 -m unittest discover -s tests -p 'test_orchestration_engine.py' -v`
- CLI smoke: `python3 -m src.orchestration.cli --config <temp>/config.json --metadata <temp>/metadata.json`

Concerns:
- Full repository baseline was intentionally not run because the task brief says optional dependencies such as `firebase_admin` keep the full baseline unclean.
- The engine uses deterministic analysis-derived workflow hints instead of a real LLM selector. This keeps the scope local to Task 6 and composes the existing policy module, but future LLM enrichment can replace or augment these hints.

## Fix Pass: Preserve `analysis.llm_hints`

Status: DONE

Review finding:
- `orchestrate_from_metadata()` must not overwrite actual LLM classification output from `analysis.llm_hints` with deterministic engine-local hints.
- It must use `analysis.llm_hints` when present and only fall back to deterministic hints when that field is empty.

Implemented:
- Extended `tests/test_orchestration_engine.py` with `test_orchestrate_from_metadata_preserves_analysis_llm_hints_when_present`.
- The new test patches `analyze_source_event()` to return an `AnalysisResult` containing explicit `llm_hints`.
- The test verifies the manifest preserves those exact hints in `llm_output`.
- The test verifies those hints are passed through to job selection by asserting `follow_up_bundle` appears in manifest workflow jobs.
- Updated `src/orchestration/engine.py` so `llm_output = analysis.llm_hints or _build_workflow_hints(analysis)`.
- Existing integration test continues to verify deterministic fallback produces multiple bundles when no hints exist.

RED evidence:
- Command: `python3 -m unittest discover -s tests -p 'test_orchestration_engine.py' -v`
- Result: failed with 2 tests run, 1 failure.
- Expected failure: `manifest["llm_output"]` was `{}` instead of `{"follow_up_bundle": {"confidence": 0.91, "reason": "llm classified explicit next step"}}`.

GREEN evidence:
- Command: `python3 -m unittest discover -s tests -p 'test_orchestration_engine.py' -v`
- Result: passed with 2 tests run, 0 failures.

Concerns:
- No full baseline run, per Task 6 instruction to keep verification focused.
