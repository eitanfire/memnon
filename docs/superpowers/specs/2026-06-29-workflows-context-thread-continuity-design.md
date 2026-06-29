# Memnon Workflows — Context Thread Continuity v1

## Goal

Define the first version of context continuity for `workflows.memnon.app`.

This feature is not about adding more tools. It is about helping related thinking stay together over time without turning Memnon into a dashboard, a project manager, or one giant undifferentiated chat.

The v1 question is:

**How does Memnon know what ongoing thought this belongs with?**

## Product Stance

Memnon should not primarily ask users to pick tools. It should help them continue the right thread.

The core rule is:

**Route to context before routing to output.**

That is the long-term product direction, not the exact execution order of the first slice.

For v1, implement a narrower proof:

1. shape the useful result first
2. if signal is strong enough, suggest one likely ongoing thread
3. let the user confirm, reject, or choose another

Do not auto-attach in v1.

## Anchor Lines

**Contexts are where related thinking lives. Workflows are what Memnon does with that thinking.**

**A standalone note is valid. Not every capture needs a thread.**

## Terminology

Internally, the system may use the term `context`.

In user-facing copy, prefer:

- `thread`
- `related thread`
- `ongoing thread`

Avoid showing internal language such as:

- context assignment
- routing suggestion
- workflow classification
- confidence

The distinction the product should preserve is:

- **Context/thread** = where this thought belongs over time
- **Output/workflow** = what Memnon does with it right now

## What A Thread Is

A thread is a topic-based container for ongoing related thinking.

Examples:

- `Memnon product direction`
- `Workflows UI/UX`
- `Voice capture`
- `InnEdCO conversations`
- `Teaching reflections`
- `Teach League strategy`

Threads are not workflow-based buckets.

Do not create threads like:

- `Follow-ups`
- `Tasks`
- `Reflections`
- `Research`

Those are output types, not continuity containers.

## What A Workflow Is

A workflow is the useful result Memnon creates from a capture.

Examples:

- saved note
- follow-up draft
- task list
- decision summary
- reflection synthesis
- product idea

Threads can contain many output types over time.

The user should not have to choose a workflow before capture. The flow remains:

1. capture first
2. Memnon shapes the result
3. Memnon may suggest the likely thread

## V1 Product Behavior

The v1 loop is:

1. capture something
2. Memnon produces a useful result
3. if the result is usable and the source is trustworthy, Memnon may suggest one likely thread
4. the user confirms once
5. later, the saved result quietly shows where it lives

The product promise is:

**Capture something messy. Get a useful result. Keep related thinking together.**

## Result-Page Behavior

The result page gains one new optional block beneath the primary result card.

This block appears only when all three are true:

- the capture already produced a usable result
- the source is trustworthy enough to attach
- one existing thread is clearly better than leaving the note standalone

If the block appears, the preferred copy is:

`This looks related to Workflows UI/UX.`

Actions:

- `Continue there`
- `Keep separate`
- `Choose another`

### Interaction Rules

`Continue there`

- confirms the suggested thread
- attaches the result to that thread
- ends the decision

`Keep separate`

- rejects the suggestion
- preserves the result as a standalone saved result
- ends the decision

`Choose another`

- opens a lightweight chooser
- allows selecting another existing thread
- allows creating a new thread only inside the chooser

`Choose another` should be visually quieter than `Continue there`.

### When The Block Should Not Appear

Do not show:

- an empty related-thread state
- `No related thread found`
- a warning that the note is unassigned

If Memnon is not clearly helping, it should stay quiet.

## Reopened Saved Results

V1 should not generate fresh thread suggestions when a saved result is reopened later.

Rules:

- if a thread was confirmed, show it quietly
- if no thread was confirmed, show nothing
- do not re-ask
- do not reopen a review flow

Example quiet display:

`Related to Workflows UI/UX`

This keeps continuity additive rather than turning saved results into a correction queue.

## Suggestion Logic

Memnon should suggest a thread only when the result is usable, the source is trustworthy, and one existing thread is clearly better than leaving the note standalone.

The threshold should be relative, not absolute.

It is not enough that a note overlaps with some thread. One thread must be meaningfully better than the alternatives.

### Signals To Use

Signals that can contribute to ranking:

- explicit context hint from capture
- repeated named entities
  - people
  - products
  - projects
  - organizations
  - events
- thread title/topic phrase overlap
- recent activity in the thread
- prior confirmed attachment patterns for similar captures

### Signals To Suppress Or Down-Rank

Suppress or heavily down-rank suggestion when the source is:

- weak
- tiny
- noisy
- mixed
- ambiguous enough that no one thread clearly wins

Weak or noisy notes should not produce a thread suggestion even if they happen to contain matching words.

### New Thread Creation

Do not propose new thread creation on the main result page in v1.

If the user chooses `Choose another`, the chooser may offer:

- a short recent-thread list
- `Create new thread: Voice capture`

That keeps the main surface simple while still allowing continuity to begin when needed.

### Learning In V1

Treat learning as ranking memory only.

Useful signals to record:

- accepted suggestion
- kept separate
- selected different thread
- created new thread

These signals should improve future ranking, but should not enable automatic attachment in v1.

## V1 Data Model

Keep the model small.

### 1. Threads

Internal object name may remain `context`.

Required fields:

- `context_id`
- `title`
- `status`
  - `active`
  - `archived`
- `created_at`
- `updated_at`
- `last_activity_at`

Optional fields:

- `summary`
- `seed_capture_id`

### 2. Result-Level Continuity Fields

Each saved result should gain:

- `confirmed_context_id: nullable`
- `suggested_context_id: nullable`
- `suggested_context_title: nullable`
- `suggestion_active: boolean`
- `context_decision: null | confirmed | kept_separate | selected_different_context | created_new_context`

Optional internal field:

- `suggestion_basis`

The product rule is:

- no suggestion shown → no decision needed
- suggestion shown → one narrow decision is available
- once the user acts → `suggestion_active` becomes false

### 3. Membership Model

One result belongs to at most one confirmed thread in v1.

Many results may belong to the same thread.

Do not add:

- multi-thread membership
- hierarchy
- merge/split logic

### 4. Display Rule

Confirmed thread exists:

- display it quietly on reopened results

No confirmed thread:

- display nothing

Do not create any `unassigned` UI.

## Implementation Implications

This spec implies only a narrow expansion of the current workflows slice.

### Service / backend implications

- the result payload may include an optional thread suggestion block
- suggestion should be computed only for immediate post-capture rendering
- suggestion persistence should preserve the exact suggestion shown so refresh does not recompute a different answer
- once acted on, the result should carry only the confirmed thread display state

### UI implications

- add one optional thread suggestion block to the result page
- add a lightweight chooser, not a dashboard
- show confirmed thread quietly on reopened results when present
- do not add a new top-level route for thread management in v1

### Product implications

- continuity is introduced without changing the capture surface
- standalone saved results remain valid first-class outcomes
- thread suggestion is a helper, not a requirement

## Explicitly Deferred

Out of scope for v1:

- automatic attachment without confirmation
- thread suggestions on reopened results
- multiple suggested threads on the main result page
- thread dashboards
- folders or tags overhaul
- project management features
- workflow-specific thread types
- multi-thread membership
- thread hierarchy
- thread merge or split
- autonomous clustering of old notes
- visible explanation UI such as `why this thread was suggested`
- visible confidence language
- context-aware agentic orchestration
- global reclassification of old results

## Why This Boundary Matters

This feature succeeds only if it feels lighter than manual organization.

The failure modes are clear:

- too many suggestions
- weak suggestions
- repeated asking
- standalone notes made to feel incomplete
- thread management leaking into the core flow

The v1 discipline is therefore:

- result first
- continuity second
- suggestion only when clearly helpful
- user confirmation once
- silence when not helpful

## V1 Success Condition

Memnon should feel different from both:

- a pile of disconnected saved notes
- a drawer full of separate AI tools

The feature is successful if a user can capture something, get a useful result, and feel that Memnon is helping related thinking continue without asking them to organize a system.
