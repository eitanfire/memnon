# Task 7 Report: Document Operator Workflow and Final Verification

## Scope

Implemented Task 7 within the allowed ownership boundary:

- `docs/private-orchestration.md`

No unrelated files were modified.

## Documentation Coverage

The operator guide now answers the five required questions:

- how to enable orchestration in config
- where manifests, review queue entries, outbox drafts, research notes, and BoulderJS packets land
- how to rerun orchestration from one metadata file
- what "auto-generate but never auto-send" means in practice
- when the social-agent CLI runs, and when the layer stops at the packet layer

## Verification

Ran the required focused unittest discovery:

```bash
python3 -m unittest discover -s tests -p 'test_orchestration_*.py' -v
```

Result:

- 17 tests run
- 0 failures
- 0 errors

Ran the required CLI help smoke check:

```bash
python3 src/orchestration/cli.py --help
```

Result:

- exit code 0
- usage text includes `--config` and `--metadata`

Fresh verification evidence from the final pass:

- `python3 -m unittest discover -s tests -p 'test_orchestration_*.py' -v` passed with 17 tests run.
- `python3 src/orchestration/cli.py --help` exited 0 and printed the `--config` and `--metadata` options.

## Commit

Created commit:

- `docs: add private orchestration operator guide`

## Fix Note

Updated the operator guide to state that every processed source event emits both `event_manifest.json` and `review_queue_entry`, to name Slack/Discord/text in the no-auto-send boundary, and to make the BoulderJS handoff explicitly file-based and deterministic.

## Verification Update

- `python3 -m unittest discover -s tests -p 'test_orchestration_*.py' -v` passed with 17 tests run, 0 failures, and 0 errors.
- `python3 src/orchestration/cli.py --help` exited 0 and showed `--config` and `--metadata`.

## Final Fix Note

Added a concise "Inspect Outputs" section to the operator guide so reruns can be validated by checking the manifest, review queue, and BoulderJS output files under `runtime/orchestration/`.

## Final Verification

- `python3 -m unittest discover -s tests -p 'test_orchestration_*.py' -v` passed.
- `python3 src/orchestration/cli.py --help` exited 0.
