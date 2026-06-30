# Private Orchestration Layer

## Enable It

Add this block to `config.json`:

```json
"orchestration": {
  "enabled": true,
  "runtime_dir": "./runtime/orchestration",
  "enable_llm_enrichment": false,
  "default_boulderjs_event_number": 30,
  "default_boulderjs_talk_number": 41,
  "social_agent_repo_dir": "/absolute/path/to/boulderjs-social-agent",
  "run_social_agent_cli": false
}
```

## Output Directories

- `runtime/orchestration/manifests/`: one `event_manifest.json` per processed source event
- `runtime/orchestration/review-queue/`: one `review_queue_entry` per processed source event
- `runtime/orchestration/outbox/`: external follow-up drafts only, never auto-sent
- `runtime/orchestration/research/`: append-only local research artifacts
- `runtime/orchestration/note-bundles/`: reflect/professional bundle references
- `runtime/orchestration/boulderjs/`: deterministic recap packets and optional CLI handoff inputs

Every processed source event writes both `event_manifest.json` and `review_queue_entry`, even when no major workflow fires.
V1 ships with deterministic extraction only, so `enable_llm_enrichment` should stay `false` unless you are explicitly wiring a future enrichment path.

## Re-Run One Event

```bash
python3 src/orchestration/cli.py --config /absolute/path/to/config.json --metadata /absolute/path/to/runtime/metadata/example.json
```

## Inspect Outputs

After a rerun, validate the generated files in the runtime directory:

```bash
ls runtime/orchestration/manifests/
ls runtime/orchestration/review-queue/
ls runtime/orchestration/boulderjs/
cat runtime/orchestration/manifests/<event_id>/event_manifest.json
cat runtime/orchestration/review-queue/<event_id>/review_queue_entry.json
```

## Safety Boundary

This layer generates files. It does not send emails, post on LinkedIn, or message people directly. The no-auto-send channel set includes Slack, Discord, and text messages.

## BoulderJS Behavior

The BoulderJS handoff is file-based and deterministic: the layer writes recap files to disk first, and those files are the handoff contract. If `run_social_agent_cli` is `false`, the layer stops after writing `event.json`, `talk.json`, `abstract.txt`, `thoughts.txt`, and `source-links.json`.

If `run_social_agent_cli` is `true`, and the BoulderJS event/talk numbers plus `social_agent_repo_dir` are configured, the layer records the prepared `boulderjs-social-agent` draft command in the packet bundle notes as part of the downstream handoff. It does not execute the CLI automatically.
