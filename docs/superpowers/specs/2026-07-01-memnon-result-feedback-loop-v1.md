# Memnon Workflows — Result Feedback / Evaluation Loop v1

## 1. Product Goal

Add the smallest useful feedback loop for workflow results without turning Memnon into another system the user has to manage.

The v1 goal is narrow:

- learn whether the immediate result helped
- capture that signal on the result itself
- keep the feedback action optional, quiet, and one-tap

This is not a review queue, analytics surface, or result-correction workflow.

## 2. User-Facing Behavior

### Prompt

`How was this result?`

### Options

- `Useful`
- `Not useful`

### Placement

Show the feedback prompt only on the immediate post-capture result page.

Do not show feedback:

- on reopened saved results
- in saved-results lists

Place the prompt below the useful result content so it reads as an optional reaction, not the main task on the page.

### Interaction Rules

- feedback is optional
- one tap records the choice
- the selected option is visibly marked
- the user may change the choice while still on the page
- latest choice wins

Do not add:

- modal feedback flows
- comment boxes
- follow-up questions
- regeneration triggers
- review queues
- badges
- reminders
- `needs feedback` states

Feedback should be available as a quiet optional reaction, not presented as a task.

## 3. Persistence

Persist feedback on the saved result itself.

V1 fields:

- `feedback_choice: null | "useful" | "not_useful"`
- `feedback_updated_at: null | timestamp`

Rules:

- one current feedback choice per result
- latest choice wins
- no feedback event history
- no analytics tables
- no feedback history UI

## 4. Contract Boundaries

Feedback must attach to the correct saved result.

Feedback must not change:

- saved result content
- thread state
- context/thread suggestion state
- `Keep separate` semantics

Feedback must not imply the user has a task to resolve.

## 5. Why Binary Immediate-Only Feedback Is The V1 Choice

Binary feedback is the v1 choice because it captures a useful quality signal without asking the user to classify the failure.

Diagnostic labels such as:

- `Too generic`
- `Wrong next step`
- `Not what I meant`

would provide richer tuning data, but they also create small administrative labor at the exact moment Memnon should be reducing load.

Reopened-result feedback is also deferred in v1. Reopening is treated as a retrieval/use moment, not an evaluation moment.

The governing rule is:

**Learn whether the immediate result helped without making the user feel responsible for improving the system.**

## 6. Explicitly Deferred

Defer all of the following from v1:

- diagnostic feedback labels
- feedback on reopened saved results
- feedback in saved-results lists
- feedback event history
- feedback analytics surfaces
- review queues
- visible feedback history
- badges, reminders, or `needs feedback` states
- follow-up prompts after feedback
- teacher-specific feedback categories

## 7. Why This Is The Recommended Next Milestone

This is the recommended next milestone because it adds a lightweight steering signal for future result-quality work without opening a larger product surface.

It is a better next step than:

- benchmark expansion
- first-thread bootstrap
- more result-language cleanup by default
- teacher-context planning

because it helps future tuning rely less on manual inspection while avoiding:

- organizer/folder drift
- dashboards
- new review burdens
- premature vertical shaping

## 8. Acceptance Criteria For A Future Implementation Pass

A future implementation pass should satisfy all of the following:

- the immediate post-capture result page shows `How was this result?`
- the user can choose `Useful` or `Not useful` with one tap
- the selected option is visibly marked
- the user can change the choice while still on the page
- the current choice persists on that result record
- no feedback UI appears on reopened saved results
- no feedback UI appears in saved-results lists
- feedback does not alter result content
- feedback does not alter thread state
- feedback does not alter suggestion state
- feedback does not alter `Keep separate` behavior
- no modal, comment box, follow-up question, review queue, reminder, or badge is introduced
