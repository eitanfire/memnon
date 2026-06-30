# Memnon Workflows App Design

## Goal

Define the v1 product behavior, screen structure, and route/component breakdown for `workflows.memnon.app`.

This route is not a transcript viewer and not a workflow configuration tool. It is a focused browser surface for taking a captured thought and turning it into the most useful next step with as little extra burden as possible.

## Product Rule

After every capture, reduce load. Never expand it.

If a screen, action, or output gives the user more to inspect, sort through, or manage, it is probably wrong.

## V1 Product Behavior

Memnon makes a judgment, shows only the most useful next output or outputs, and involves the user only when that reduces risk or confusion.

V1 behavior constraints:

- show one primary output by default
- allow a second output only when it clearly supports the same job
- allow three outputs only when the capture clearly points to several distinct, useful next steps
- never show more than three outputs in v1
- skip routing when one obvious, low-risk action is clear
- stay quiet when visible orchestration adds no value
- keep transcripts secondary
- keep the review queue sparse and calm

## Route Model

V1 should use only three primary routes.

### 1. `/`

The capture screen.

Purpose:

- accept live voice, uploaded files, or pasted text
- avoid any workflow configuration
- move the user into processing with one clear action

### 2. `/resolve/:captureId`

The uncertainty resolution screen.

Purpose:

- handle the cases where the system cannot confidently move straight to a result
- keep uncertainty narrow and understandable
- either save the note quietly or resolve one ambiguity

This route should only appear when needed. If a clear next step is obvious, skip it.

### 3. `/result/:captureId`

The artifact page.

Purpose:

- show the most useful result first
- keep supporting outputs secondary
- surface any review-needed items without turning into an inbox

### Not in V1

Do not create separate first-class routes for:

- transcript browsing
- workflow browsing
- settings-heavy orchestration controls
- a standalone queue dashboard

The queue stays inside the result page in v1.

## Screen Spec

## 1. Capture Screen

### Purpose

Let the user hand Memnon something to work with without configuring anything.

### Copy

- title: `Memnon Workflows`
- subhead: `Capture a thought. Turn it into a useful next step.`

### Components

- `CaptureCard`
  - primary button:
    - idle: `Record now`
    - recording: `Recording…`
    - ready: `Recording ready`
  - quiet alternatives:
    - `Upload a file`
    - `Paste text`
  - optional context field
    - label: `Anything this should keep in mind?`
    - placeholder: `Example: this is about a product idea, a follow-up, or something I’m trying to think through.`
  - continue button
    - label: `Continue`
    - disabled until input exists

### Behavior

- recording happens in place
- upload shows a compact file chip with remove option
- paste expands inline
- no transcript preview
- no workflow selector
- no advanced options on this screen

### Success Condition

The user feels like they are giving Memnon material, not filling out a form.

## 2. Resolve Screen

The resolve screen has two states only.

### A. No Clear Next Step Yet

Use this when the capture does not yet point to one reliable outcome.

#### Copy

- title: `No clear next step yet`
- supporting line: `This has been saved as a note with likely themes.`

#### Components

- `ThemeList`
- `SourceExcerpt`
- primary action: `Add a little direction`
- secondary action: `Leave as note`
- quiet utility: `View source text`

#### Behavior

- Memnon has already done something useful before showing this state
- `Add a little direction` opens one lightweight clarification step
- `Leave as note` finishes the flow and lands on the saved note/result
- `View source text` opens a compact drawer or panel

#### Rules

- no list of possible workflows
- no multiple draft cards
- no confidence numbers
- no full transcript by default

### B. This Could Go Two Ways

Use this when the capture points to two plausible directions and one lightweight choice will resolve the ambiguity.

#### Copy

- title: `This could go two ways`
- supporting line: `Choose the direction you want to take.`

#### Components

- `DirectionChoiceCard`
  - option A
    - title: `Make it a follow-up`
    - line: `Draft a message or next step for someone else.`
  - option B
    - title: `Keep it as reflection`
    - line: `Save the thinking and pull out the key insight.`
- `SourceExcerpt`
- quiet utility: `View source text`
- quiet exit: `Leave as note`

#### Behavior

- show exactly two options
- do not show a third path in v1
- once the user picks one, resolve directly to one output
- do not reopen a second routing step after selection

## 3. Result Screen

### Purpose

Show the most useful result first and keep everything else secondary.

### Top Strip

- interpretation line
  - example: `This looks like a follow-up about the Credible demo.`
- quiet metadata
  - example: `Saved from voice note · 3 min ago`
- quiet utility
  - `View source text`

### Layout

- one large primary artifact card
- optional one or two secondary artifact cards
- review queue section only if needed

### Primary Artifact Card

#### Required Elements

- action-shaped title
  - `Draft reply to Kyle`
  - `Task list for tomorrow`
  - `Reflection note on today’s class`
- one short framing line
  - example: `Pulled from your note about following up after the meetup.`
- preview body
- live status
  - `Ready to review`
  - `Saved`
  - `Copied`
  - `Needs decision`

#### Actions

- one primary action:
  - `Copy`
  - `Open draft`
  - `Approve`
  - `Save`
- up to two secondary actions:
  - `Edit`
  - `Regenerate`
- quiet utilities:
  - `View source text`
  - `Why this was suggested`

#### Rules

- the user should be able to judge usefulness in under five seconds
- `Regenerate` is never the first action
- the first card should be clearly more important than anything below it

### Secondary Artifact Cards

#### Required Elements

- short title
- one-line description
- status
- one direct action

#### Rules

- smaller and quieter than the primary card
- available, not demanding
- if two cards overlap, suppress one before rendering
- if no meaningful secondary card exists, omit the section entirely

### Review Queue Section

#### Section Title

- `Needs your decision`

#### Per-item Contents

- short title
- one-line consequence
  - `Posting this will publish to LinkedIn.`
  - `Sending this will email Kyle.`
- actions:
  - `Approve`
  - `Edit first`
  - `Archive`

#### Rules

- only items needing judgment before something external happens belong here
- calm styling only
- queue should rarely exceed three items
- if it does, collapse, archive, or suppress until it is useful again

## Source Text Behavior

### Normal State

- quiet link: `View source text`

### Uncertain States

- show one excerpt inline first
- full source text available on request

### Rules

- source text is always preserved
- source text is never the main output
- show excerpts before showing the full transcript

## State Transitions

- if one clear, low-risk next step is obvious:
  - skip `/resolve/:captureId`
  - go directly to `/result/:captureId`

- if there is no clear next step:
  - go to `/resolve/:captureId` in the `No clear next step yet` state

- if there are two likely directions:
  - go to `/resolve/:captureId` in the `This could go two ways` state

- after a split choice:
  - resolve directly to one output on `/result/:captureId`

- after `Copy`:
  - update status to `Copied`

- after `Approve`:
  - update status to `Saved` or remove from the queue

- after `Edit`:
  - open a lightweight edit state first

- after `Regenerate`:
  - replace preview, but do not reorder the page unless the new result is clearly better

## Component Breakdown

V1 should be built from a small set of explicit components.

### Capture

- `WorkflowsCapturePage`
- `CaptureCard`
- `RecordButton`
- `UploadFileLink`
- `PasteTextPanel`
- `ContextField`
- `ContinueButton`

### Resolve

- `WorkflowsResolvePage`
- `ResolveHeader`
- `ThemeList`
- `SourceExcerpt`
- `DirectionChoiceCard`
- `SourceTextDrawer`

### Result

- `WorkflowsResultPage`
- `ResultTopStrip`
- `PrimaryArtifactCard`
- `SecondaryArtifactList`
- `SecondaryArtifactCard`
- `ReviewQueueSection`
- `ReviewQueueItem`
- `SourceTextDrawer`

### Shared

- `StatusBadge`
- `MetadataLine`
- `QuietLink`
- `EmptyStateMessage`

## Data Needs

V1 UI should assume these fields are available from the backend layer:

- `captureId`
- `sourceType`
- `sourceTextPreview`
- `sourceExcerpt`
- `themes`
- `interpretationLine`
- `artifacts[]`
- `reviewQueueItems[]`
- `needsResolve`
- `resolveMode`
  - `none`
  - `no_clear_next_step`
  - `two_likely_directions`
- `resolveChoices[]`

The UI should not need to know internal classifier labels or workflow ontology names.

## Copy Guardrails

Use:

- `This looks like…`
- `No clear next step yet`
- `Add a little direction`
- `Leave as note`
- `Needs your decision`
- `Why this was suggested`

Do not use:

- `Detected`
- `Classified as`
- `Confidence: 0.84`
- `Workflow output`
- `Choose a workflow`
- anthropomorphic phrasing like `Memnon isn’t sure` or `Help Memnon understand this`

## Out of Scope for V1

- transcript-first browsing
- operator dashboards
- standalone queue management
- deep settings for routing behavior
- user-facing workflow taxonomies
- more than three visible outputs on a single result page

## Success Criteria

This route is successful in v1 if:

- capture feels simpler than form entry
- the system often goes straight to one useful result
- uncertain cases are calm and narrow
- the result page feels like a prioritized desk, not a dashboard
- the queue remains sparse enough that it does not become a guilt surface
- the user can trust what appears without needing to inspect the full transcript first
