# Workflows Storage Contract

This vertical slice persists one `WorkflowCaptureRecord` per `capture_id`.

Storage key:
- local file-backed dev store: `uid::capture_id`
- Firestore store: `users/{uid}/workflow_captures/{capture_id}`

Required top-level fields:
- `capture_id: string`
- `input_type: string`
- `context_hint: string`
- `source_event: object`
- `routing: object`
- `result: object`
- `event_manifest: object`
- `created_at: string | timestamp`
- `updated_at: string | timestamp`

Required `source_event` fields:
- `capture_id: string`
- `input_type: string`
- `source_text: string`
- `source_preview: string`
- `context_hint: string`
- `likely_themes: string[]`
- `created_at: string | timestamp`

Optional uploaded-file `source_event` fields:
- `source_filename: string`
- `source_file_type: string`
- `source_file_extension: string`
- `source_file_size_bytes: number`

Required `routing` fields:
- `route_kind: "direct_professional_note" | "saved_note"`
- `interpretation_line: string`
- `primary_artifact_kind: string`
- `secondary_artifact_kinds: string[]`
- `likely_themes: string[]`
- `saved_note_state: "" | "weak_signal" | "needs_direction"`

Required `result` fields:
- `interpretation_line: string`
- `route_kind: "direct_professional_note" | "saved_note"`
- `primary_artifact: object | null`
- `saved_note_artifact: object | null`
- `secondary_artifacts: object[]`
- `review_queue: object[]`
- `source_preview: string`
- `likely_themes: string[]`

Artifact shape for both `primary_artifact` and `saved_note_artifact`:
- `artifact_id: string`
- `kind: string`
- `title: string`
- `framing_line: string`
- `body: string`
- `status: string`
- `primary_action: string`
- `metadata_line: string`
- `source_excerpt: string`
- `sections: { label: string, text: string }[]`
- `copy_text: string`
- `state: string`
- `secondary_actions: string[]`

Persistence rule:
- The stored payload must be sufficient to re-render `/workflows/result/:id` without re-running routing or generation.
