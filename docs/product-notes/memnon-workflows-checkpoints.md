# Memnon Workflows Checkpoints

Deployed checkpoints for production workflow milestones.

## 2026-07-02 — File Upload v1 Complete

- PR: `#11`
- Spec: `docs/superpowers/specs/2026-07-02-memnon-file-upload-v1.md`
- Implementation plan: `docs/superpowers/plans/2026-07-02-memnon-file-upload-v1.md`
- Base merge commit: `b0449ef693137b8e90b67cf58220ac0d16fe8416`
- Patch commit: `ce56476757658614b6364502832705ef87577f4f`
- Deployed commit: `ce56476757658614b6364502832705ef87577f4f`
- Deploy command: `firebase deploy --only hosting --project memnon-app`
- Deploy result: successful
- Production URL: `https://memnon.app/workflows`

Deployed behavior:

- File Upload v1 is live as a capture input, not a document feature.
- Upload accepts one `.txt` or `.md` file at a time with a hard `512 KB` limit.
- Uploaded-file captures route through the existing workflow result pipeline and save like any other workflow result.
- Immediate uploaded-file results show uploaded-file origin and source excerpt for trust.
- Reopened uploaded-file results now preserve filename-aware uploaded-file metadata.
- Reopened saved results still show no feedback controls.
- Saved-results list stays compact and does not become a file manager.
- Result Feedback v1 remains immediate-only.

Patch fix included in deployed state:

- Reopened uploaded-file results no longer fall back to generic artifact metadata such as `Saved note · Jul 2, 2026`.
- The client now resolves reopened-result source metadata from the full payload shape:
  - `payload.source_event`
  - `payload.event_manifest.source_event`
  - top-level `input_type`
  - top-level `created_at`

Verification on `main`:

- `PYTHONPATH=/Users/eitan/memnon /Users/eitan/memnon/.venv/bin/python -m unittest discover -s tests -p 'test_workflows*.py' -v`
  - 123 tests passed
- `node --check public/workflows.js`
  - passed

Production QA:

- Reopened uploaded-file result `cap-eccd72fcceff` rendered `Uploaded file · prod-qa.txt · Jul 2, 2026` and `From prod-qa.txt`.
- Reopened uploaded-file result showed no feedback controls.
- Reopened pasted-text result preserved normal pasted-note metadata.
- Reopened voice result preserved normal voice-note metadata.
- Saved-results list remained compact and showed no feedback controls.

Status:

- File Upload v1 is complete.
