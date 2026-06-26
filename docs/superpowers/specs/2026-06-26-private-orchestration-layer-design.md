# Private Orchestration Layer Design

## Goal

Create a private, post-capture orchestration layer that sits on top of Memnon and related personal tools. Given one processed recording, the layer should automatically determine which downstream workflows should run, generate the relevant artifacts, and route them to the correct destinations without automatically sending external messages.

This layer is not a new lane. It is an operator system for fanning a single source event out into multiple workflow jobs across Memnon, BoulderJS social drafting, research capture, and personal outreach.

## Why This Exists

Memnon already handles capture, transcription, summarization, note writing, lane routing, and metadata generation. Your private workflow has expanded beyond that core. A single recording may now need to do several things at once:

- create a reflection or professional note
- generate product-research evidence
- update a conference or outreach ledger
- draft BoulderJS social posts
- prepare follow-up messages

Trying to express those as more lanes would overload the lane abstraction. Lanes shape a primary note. They do not describe multi-destination orchestration. The missing abstraction is a system that treats each recording as a source event that can trigger several distinct workflow jobs.

## Product Boundary

The public Memnon repo remains responsible for:

- capture
- transcription
- base structured note generation
- lane routing
- metadata emission

The private orchestration layer is responsible for:

- reading processed Memnon outputs
- analyzing the source event
- deciding which workflows should fire
- generating workflow-specific artifacts
- writing audit manifests and review queue entries

This should start as a private companion project or private module, not as a public Memnon core feature. It may read Memnon outputs, call Memnon-adjacent tooling, and invoke other local repos such as `boulderjs-social-agent`, but it should not force the public Memnon repo to absorb private operator logic.

## Source Event Model

Each processed recording becomes a `source_event`.

A source event contains the original material and the base structured interpretation needed for downstream routing:

- transcript
- transcript preview
- summary
- action items
- suggested tags
- note path
- archived audio path
- lane
- processed timestamp
- source project or context
- optional enriched analysis fields such as people, orgs, event type, promises, product feedback, and publishable ideas

The system should treat the source event as the canonical input. Everything downstream should be derived from that object rather than re-reading arbitrary files ad hoc.

## Core Pipeline

The orchestration pipeline is:

1. analyze source event
2. apply suppressor rules
3. apply mandatory hard rules
4. run LLM classification on remaining candidate workflows
5. create workflow jobs
6. generate artifact bundles
7. write `event_manifest.json`
8. write `review_queue_entry`

This ordering is intentional. Suppressors prevent obviously wrong jobs. Hard rules force known-safe routing. The LLM fills semantic gaps and assigns confidence where fixed rules are not enough.

## Workflow Ontology

The system should use a stable workflow vocabulary grouped into families.

### Primary Note Workflows

- `reflect_note_bundle`
- `professional_note_bundle`
- `gpt_handoff_bundle`

### Research Workflows

- `research_note`
- `conference_evidence_update`
- `speaker_outreach_note`
- `user_story_capture`
- `positioning_theme_capture`

### Content Workflows

- `boulderjs_recap_packet`
- `social_content_packet`
- `image_selection_packet`

### Relationship Workflows

- `follow_up_bundle`
- `intro_bundle`
- `invite_bundle`

### Operator Workflows

- `review_queue_entry`
- `event_manifest`

Version one should implement only a subset of these; the full ontology still matters now because it gives the classifier and routing layer a stable language.

## Version One Scope

Version one should implement these five workflows:

1. `reflect_note_bundle`
2. `professional_note_bundle`
3. `research_note`
4. `boulderjs_recap_packet`
5. `follow_up_bundle`

And these two mandatory infrastructure outputs for every source event:

- `event_manifest.json`
- `review_queue_entry`

`people_memory` is explicitly out of scope for v1. It depends on entity resolution, deduplication, update-vs-append policy, and stable person records. That is a separate data problem and should be treated as v2.

## Conflict Resolution and Routing Precedence

Routing precedence must be explicit.

### 1. Suppressor Rules

Suppressors block workflows even if the LLM is moderately confident.

Examples:

- do not create a `follow_up_bundle` when no named people, organizations, or explicit commitments appear
- do not create a `boulderjs_recap_packet` for a private reflection recording
- do not create a `research_note` from a trivial accidental recording

Suppressors are first-class rules, not just absent positives.

### 2. Mandatory Hard Rules

Hard rules always fire the workflow.

Examples:

- a recording containing BoulderJS context, presenter framing, demo/Q&A structure, or clear meetup recap signals should fire `boulderjs_recap_packet`
- a recording captured intentionally into the Memnon professional path should fire `professional_note_bundle`

### 3. LLM Classification

The LLM only evaluates workflows not already suppressed or mandated. It should return:

- candidate workflow types
- confidence scores
- short reasoning
- extracted structured hints if useful, such as likely people, promises, or content angles

### 4. Threshold Policy

- confidence `>= 0.80`: auto-generate
- confidence `0.50 - 0.79`: auto-generate and mark `needs_review`
- confidence `< 0.50`: do not generate unless a mandatory hard rule already fired

### 5. Rule/LLM Disagreement

If a mandatory hard rule fires but the LLM is low confidence, the workflow still runs and is marked:

- `forced_by_rule: true`
- `review_priority: high`

This keeps the system auditable and tunable. Hard rules win, but disagreement is surfaced rather than hidden.

## Source Event → Workflow Jobs → Artifact Bundles

One source event can produce many workflow jobs.

Each workflow job has:

- a workflow type
- a confidence score
- a reason
- a status
- a destination
- review flags

Each workflow job writes one artifact bundle at one output path. A bundle may contain multiple files.

Examples:

- `boulderjs_recap_packet`
  - output path: one draft directory
  - files: `linkedin.md`, `bluesky.md`, `image-checklist.md`, `manifest.json`

- `follow_up_bundle`
  - output path: one outbox directory per event or thread
  - files: `email.md`, `slack.md`, `discord.md`, `linkedin-dm.md`, `text.md`

- `research_note`
  - output path: one research-note record or file

This preserves the “one owner, one output path” rule without forcing every workflow into a single-file output.

## Destinations

Each workflow needs a stable destination contract.

### Reflect Note Bundle

Writes to the existing reflection-note destination or a private adjunct directory that Memnon can already read.

### Professional Note Bundle

Writes to the existing professional-note destination or the current professional archive path.

### Research Note

Writes to a structured research store. This may be JSON, JSONL, or a format that can be imported into the existing research view. The main requirement is that the output is queryable and append-only by default.

### BoulderJS Recap Packet

Writes to the `boulderjs-social-agent` draft input/output path or to an adapter directory that the social agent can consume deterministically.

Version one should define the adapter contract explicitly instead of leaving it implicit. The orchestration layer should write one packet directory per event, containing at minimum:

- `event.json`
- `talk.json`
- `abstract.txt`
- `thoughts.txt`
- `source-links.json`

The social-agent executor can then either:

- call the social agent CLI directly with those files, or
- transform the packet into the exact CLI arguments the social agent already accepts

The important constraint is that the orchestration layer should not rely on hand-built prompts or fragile string concatenation at integration time. The packet must be deterministic and file-based.

### Follow-Up Bundle

Writes to an `outbox/` directory. External messages are drafted automatically but not sent automatically.

### Event Manifest

Writes to a `manifests/` directory, one manifest per source event.

### Review Queue Entry

Writes to a `review-queue/` directory or equivalent queue store used to inspect what was generated and what needs human approval.

Version one should use a JSON format with one file per source event. The minimum schema should include:

- `source_event_id`
- `created_at`
- `workflow_jobs`
- `artifacts_generated`
- `needs_review`
- `review_priority`
- `external_drafts_pending`
- `forced_by_rule_jobs`
- `notes`

## Automation Policy

The system should auto-generate artifacts but not auto-send external communications by default.

Allowed automatic behavior:

- generate notes
- generate research artifacts
- generate recap packets
- generate follow-up drafts
- append manifests and review entries

Disallowed automatic behavior in v1:

- sending email
- posting to LinkedIn
- sending Slack/Discord/DM messages

Generation is reversible. Sending is not. That boundary should be enforced in code, not left as operator discipline.

## Analysis Layer

The source-event analysis layer should extract enough structure to support routing and artifact generation. This layer should itself be a mix of:

- rule-based extraction for obvious, deterministic signals
- LLM enrichment for semantic interpretation, summarization, and weak-signal classification

Analysis should happen before routing so suppressors and mandatory hard rules can operate on structured signals rather than only raw transcript text.

The analyzed event should expose fields like:

- `event_type`
- `named_people`
- `named_orgs`
- `commitments`
- `follow_up_requests`
- `product_feedback`
- `research_signals`
- `publishable_angles`
- `reflection_signals`
- `professional_signals`

The analysis result should be persisted inside the event manifest so routing decisions can be inspected later.

## Manifest and Review Queue

Every processed source event should produce:

### `event_manifest.json`

This should include:

- source-event metadata
- analysis result
- applied suppressors
- applied hard rules
- LLM classification output
- created workflow jobs
- artifact bundle paths
- generation timestamps

### `review_queue_entry`

This should summarize:

- what was generated
- what requires review
- any high-priority disagreement between rules and LLM
- any external-facing drafts waiting for human approval

In v1 this should be implemented as a JSON file, append-only at the event level, with one review entry per source event.

This infrastructure is mandatory even when no major workflow fires, because it gives the system an audit trail and keeps low-signal recordings inspectable.

## Suggested Repository Structure

The private layer should start with a file-oriented structure like:

- `schemas/`
- `analyzers/`
- `policies/`
- `executors/`
- `destinations/`
- `runtime/manifests/`
- `runtime/review-queue/`
- `runtime/outbox/`

The first version should prefer explicit files and deterministic output over a service-heavy design. You need visibility more than elegance.

## Non-Goals for Version One

These are explicitly out of scope:

- automatic external sending
- speaker diarization or voice identity
- people-memory/CRM entity resolution
- generalized multi-user orchestration
- production-grade scheduling, retries, or distributed execution
- replacing Memnon’s current base pipeline

The goal is a usable private operator layer, not a polished product platform.

## Success Criteria

Version one succeeds if:

- one recording can produce multiple useful outputs automatically
- routing decisions are inspectable after the fact
- low-confidence cases are surfaced for review rather than silently dropped
- external follow-up drafts are created without being auto-sent
- BoulderJS/event recordings can automatically feed social, research, and follow-up workflows
- the system adds leverage without forcing the public Memnon repo to absorb private operator complexity

## Initial Implementation Strategy

Build the first version around the current Memnon metadata output and existing local repos. Do not redesign capture first. The orchestration layer should consume the outputs you already have and prove the routing model before you touch the public Memnon core.

That means the first implementation should:

- ingest Memnon metadata and note/transcript context
- create a normalized source-event object
- apply rule-based suppressors and mandatory routes
- call an LLM only for unresolved workflow decisions
- generate five v1 workflow bundles
- write manifests and review queue entries

If this proves useful, you can then decide whether parts belong back in public Memnon or should remain permanently private.
