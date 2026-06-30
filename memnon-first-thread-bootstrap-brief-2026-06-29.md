# Memnon Workflows — First Thread Creation Brief

## Purpose

Define how a fresh user with zero existing threads should reach thread continuity without turning Memnon into a manual organizer.

This brief is intentionally separate from the `Keep separate` bug fix. The bug fix is production behavior work. This document is product-shape work.

## Product North Star

Threads should emerge from repeated related captures, but only become real after user confirmation.

The distinction is:

- Context continuity: "this belongs with that ongoing thought"
- Organization overhead: "create a category and keep the system tidy"

Memnon should do the first and avoid drifting into the second.

## Guardrails

- Standalone notes remain first-class.
- Not every capture needs a thread.
- Memnon may notice continuity, but the user confirms before a thread becomes real.
- No silent thread creation.
- No auto-attach.
- No dashboard, folder, tag, or project framing.
- No "unassigned" state.
- No pressure to organize notes that are valid on their own.

## Long-Term Preferred Model

### Desired behavior

1. First capture on a new topic remains a standalone note.
2. A later capture strongly related to that earlier standalone note may trigger:
   - `This looks connected to an earlier note about Voice capture.`
3. The user chooses:
   - `Start thread`
   - `Keep separate`
4. If the user chooses `Start thread`:
   - create a new thread
   - attach the earlier result as the seed
   - attach the current result
   - show the confirmed thread quietly on reopened results
5. If the user chooses `Keep separate`:
   - both notes remain standalone
   - do not keep asking on those results

This preserves the trust boundary:

- Memnon can notice continuity.
- The user confirms before continuity becomes structure.

## Why This Is Not a Small Patch

The emergent-thread model is materially more complex than existing-thread suggestion.

It requires:

- retrieval over prior standalone results
- ranking current capture against earlier notes, not only existing threads
- seeding a new thread from two captures
- suppression rules so weak overlap does not create administrative noise
- decision-state handling for both linked results

This should not be folded into a bug fix or a small UI pass.

## Immediate Product Gap

A fresh user with zero threads cannot currently experience thread continuity in the UI.

That is a real usability gap, but the fix should not accidentally become the product model.

The rule is:

Bootstrap can exist, but it must not become the main product story.

## Options

### Option A — No bootstrap path yet

Wait for the richer emergent-thread model.

Pros:

- Keeps the product model pure
- Avoids introducing a thread-creation affordance that could drift toward folder behavior

Cons:

- Fresh users cannot use thread continuity at all
- The feature remains effectively hidden until a larger follow-up lands

### Option B — Quiet bootstrap path inside `Choose another`

Allow new-thread creation only as a secondary path inside the existing chooser.

Constraints:

- no top-level `Create thread` button on the result page
- no dashboard
- no category/folder/project language
- do not imply standalone notes are incomplete
- keep creation clearly secondary to `Continue there` and `Keep separate`

Pros:

- Fresh users can bootstrap the feature
- Keeps the creation affordance contained and low-prominence
- Avoids blocking thread continuity entirely while the richer model is still deferred

Cons:

- Introduces a compromise path that is not the long-term ideal
- Risks becoming sticky if not treated explicitly as bootstrap behavior

## Recommendation

Choose **Option B** as a pragmatic bootstrap, but name it clearly as a bootstrap compromise rather than the desired long-term interaction model.

Reason:

- Without some quiet creation path, new users cannot experience thread continuity at all.
- The danger is not the existence of `Create thread`.
- The danger is making thread creation feel like the primary job.

So the product stance should be:

- near term: quiet bootstrap path inside `Choose another`
- long term: threads emerge from repeated related captures with explicit confirmation

## Implementation Boundary

Do not implement this brief as part of the `Keep separate` fix.

The next implementation pass should explicitly choose between:

- shipping the quiet bootstrap path now
- waiting for the emergent-thread model

That choice should be made consciously, not implicitly through incremental UI changes.
