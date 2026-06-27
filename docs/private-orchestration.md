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

- `runtime/orchestration/manifests/`: one `event_manifest.json` per source event
- `runtime/orchestration/review-queue/`: one review JSON per source event
- `runtime/orchestration/outbox/`: external follow-up drafts only, never auto-sent
- `runtime/orchestration/research/`: append-only local research artifacts
- `runtime/orchestration/note-bundles/`: reflect/professional bundle references
- `runtime/orchestration/boulderjs/`: deterministic recap packets and optional CLI handoff inputs

## Re-Run One Event

```bash
python3 src/orchestration/cli.py --config /absolute/path/to/config.json --metadata /absolute/path/to/runtime/metadata/example.json
```

## Safety Boundary

This layer generates files. It does not send emails, post on LinkedIn, or message people directly.

## BoulderJS Behavior

If `run_social_agent_cli` is `false`, the layer stops after writing `event.json`, `talk.json`, `abstract.txt`, `thoughts.txt`, and `source-links.json`.

If `run_social_agent_cli` is `true`, the layer may call the `boulderjs-social-agent` draft CLI, but only when the needed event/talk identifiers are configured.
