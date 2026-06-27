# Private Orchestration Layer

## Enable It

Add this block to `config.json`:

```json
"orchestration": {
  "enabled": true,
  "runtime_dir": "./runtime/orchestration",
  "enable_llm_enrichment": true,
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
cat runtime/orchestration/review-queue/<event_id>.json
```

## Safety Boundary

This layer generates files. It does not send emails, post on LinkedIn, or message people directly. The no-auto-send channel set includes Slack, Discord, and text messages.

## BoulderJS Behavior

The BoulderJS handoff is file-based and deterministic: the layer writes recap files to disk first, and those files are the handoff contract. If `run_social_agent_cli` is `false`, the layer stops after writing `event.json`, `talk.json`, `abstract.txt`, `thoughts.txt`, and `source-links.json`.

If `run_social_agent_cli` is `true`, the layer may call the `boulderjs-social-agent` draft CLI, but only after the required event/talk identifiers are configured and the file set is present.
