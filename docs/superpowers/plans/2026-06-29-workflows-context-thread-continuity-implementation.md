# Workflows Context Thread Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add post-capture thread continuity to the workflows slice so a saved result can be attached to one ongoing thread, display its confirmed thread later, and only show an automatic thread suggestion when the signal is clearly strong enough.

**Architecture:** Build this in three layers. First add thread persistence and capture-level thread state plus manual attach primitives. Then add the immediate result-page chooser and confirmed-thread display. Only after that add automatic suggestion ranking and the quiet `Continue there / Keep separate / Choose another` block.

**Tech Stack:** Firebase Functions (Flask), Firestore, plain JavaScript in `public/workflows.js`, Python `unittest`

## Global Constraints

- Contexts are where related thinking lives. Workflows are what Memnon does with that thinking.
- A standalone note is valid. Not every capture needs a thread.
- Threads are topic-based, not workflow-based.
- Threads are user-owned and private by default.
- Suggest one likely thread only when clearly helpful.
- Do not auto-attach.
- Do not suggest fresh threads on reopened saved results.
- Show confirmed thread quietly on reopened results.
- If Memnon is not clearly helping, it should stay quiet.
- No context dashboard.
- No folders/tags overhaul.
- No multi-context membership.
- No thread merge/split.
- No agentic orchestration.
- No visible confidence language.
- No `why this thread was suggested` UI.
- No broad layout redesign.

---

## File Map

### Existing files to modify

- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/models.py`
  - Extend the workflows data model with thread state and optional display payloads.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/repository.py`
  - Add Firestore persistence for workflow threads and capture thread decisions.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/service.py`
  - Add thread CRUD helpers, decision application, display shaping, and suggestion ranking.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/blueprint.py`
  - Add endpoints for listing/creating threads and recording thread decisions.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/public/workflows.js`
  - Render confirmed thread display, immediate post-capture chooser, and later the suggestion block.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/public/workflows.css`
  - Add quiet styling for the related-thread block and chooser.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_service.py`
  - Add service-level behavior tests for thread persistence, suppression, and suggestion ranking.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_api.py`
  - Add API tests for thread list/create and capture decision endpoints.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_local_app.py`
  - Add local app persistence tests so thread decisions survive app instance recreation.
- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_static_contract.py`
  - Add frontend contract tests for quiet thread copy and no suggestion on reopened results.

### New files to create

- `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_threading.py`
  - Focused unit tests for thread suggestion ranking and suppression logic.

## Minimal V1 Data Model

### Firestore collections

- `users/{uid}/workflow_captures/{capture_id}`
- `users/{uid}/workflow_contexts/{context_id}`

### `workflow_contexts` document shape

```python
{
    "context_id": "ctx-abc123",
    "title": "Workflows UI/UX",
    "summary": "Ongoing product thinking about the workflows route.",
    "status": "active",  # or "archived"
    "seed_capture_id": "cap-123",
    "created_at": firestore.SERVER_TIMESTAMP,
    "updated_at": firestore.SERVER_TIMESTAMP,
    "last_activity_at": firestore.SERVER_TIMESTAMP,
}
```

### Capture-level thread state

Persist under the capture record as a single nested object:

```python
{
    "threading": {
        "confirmed_context_id": None,
        "suggested_context_id": None,
        "suggested_context_title": None,
        "suggestion_active": False,
        "context_decision": None,
        "suggestion_basis": None,
        "suggested_at": None,
        "context_decision_at": None,
    }
}
```

### Result payload display contract

Return a minimal display object in the result payload:

```python
{
    "related_thread": {
        "confirmed_title": None,
        "suggested_title": None,
        "suggestion_active": False,
    }
}
```

This keeps the frontend simple and prevents it from reading raw persistence flags directly.

## Interfaces

### Repository

Add these methods to `FirestoreWorkflowRepository` and all fake repositories used in tests:

```python
def list_active_contexts(self, uid: str, limit: int = 12) -> list[dict]:
    raise NotImplementedError
def create_context(
    self,
    uid: str,
    *,
    context_id: str,
    title: str,
    summary: str,
    seed_capture_id: str | None,
    now: str,
) -> dict:
    raise NotImplementedError
def update_capture_threading(self, uid: str, capture_id: str, threading: dict) -> None:
    raise NotImplementedError
def get_context(self, uid: str, context_id: str) -> dict | None:
    raise NotImplementedError
```

### Service

Add these methods to `WorkflowService`:

```python
def list_active_contexts(self, uid: str, limit: int = 12) -> list[dict]:
    raise NotImplementedError
def create_context(self, uid: str, *, title: str, summary: str = "", seed_capture_id: str | None = None) -> dict:
    raise NotImplementedError
def apply_context_decision(
    self,
    uid: str,
    capture_id: str,
    *,
    action: str,
    context_id: str | None = None,
    new_context_title: str | None = None,
) -> dict:
    raise NotImplementedError
def suggest_context_for_capture(self, uid: str, record: dict) -> dict | None:
    raise NotImplementedError
```

### HTTP API

Add these endpoints in `blueprint.py`:

```python
GET /workflows/contexts
POST /workflows/contexts
POST /workflows/captures/<capture_id>/context-decision
```

Request body for decision:

```json
{
  "action": "confirmed" | "kept_separate" | "selected_different_context" | "created_new_context",
  "context_id": "ctx-123",
  "new_context_title": "Voice capture"
}
```

## Recommendation on Scope Split

Implement this in three tasks.

Reason:

- the current codebase has no thread persistence at all
- result rendering is card-based and simple
- suggestion ranking without stable persistence would be hard to verify and easy to overfit

The recommended first commit is:

**persist workflow threads and manual capture thread decisions**

That proves the continuity data path before automatic suggestion logic is introduced.

### Task 1: Persist workflow threads and capture thread state

**Files:**
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/models.py`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/repository.py`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/service.py`
- Test: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_service.py`
- Test: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_local_app.py`

**Interfaces:**
- Consumes: existing `WorkflowCaptureRecord.to_dict()` result shape
- Produces:
  - `WorkflowThreadState.to_dict() -> dict[str, Any]`
  - `WorkflowService.create_context(uid: str, *, title: str, summary: str = "", seed_capture_id: str | None = None) -> dict`
  - `WorkflowService.apply_context_decision(uid: str, capture_id: str, *, action: str, context_id: str | None = None, new_context_title: str | None = None) -> dict`
  - repository methods listed above

- [ ] **Step 1: Write the failing thread-state tests**

```python
def test_service_can_create_active_context():
    repo = FakeRepository()
    service = WorkflowService(
        repository=repo,
        note_generator=lambda *_args, **_kwargs: {},
        now_provider=lambda: "2026-06-29T12:00:00Z",
        api_key_provider=lambda: "test-key",
    )

    created = service.create_context(
        "user-1",
        title="Workflows UI/UX",
        summary="Ongoing product thinking about the workflows route.",
        seed_capture_id="cap-seed",
    )

    assert created["title"] == "Workflows UI/UX"
    assert created["status"] == "active"
    assert created["seed_capture_id"] == "cap-seed"


def test_service_can_confirm_context_for_existing_capture():
    repo = FakeRepository()
    service = WorkflowService(
        repository=repo,
        note_generator=lambda *_args, **_kwargs: {
            "title": "Workflows page conversation with Jordan",
            "framing_line": "A saved note shaped around one concrete next step.",
            "key_point": "The result card still feels too generic.",
            "next_step": "Revise the result card.",
        },
        now_provider=lambda: "2026-06-29T12:00:00Z",
        api_key_provider=lambda: "test-key",
    )
    capture = service.create_text_capture(
        uid="user-1",
        source_text="Met with Jordan about the workflows page. Action: revise the result card.",
        context_hint="",
    )
    context = service.create_context("user-1", title="Workflows UI/UX", summary="")

    updated = service.apply_context_decision(
        "user-1",
        capture.capture_id,
        action="confirmed",
        context_id=context["context_id"],
    )

    assert updated["threading"]["confirmed_context_id"] == context["context_id"]
    assert updated["threading"]["suggestion_active"] is False
    assert updated["result"]["related_thread"]["confirmed_title"] == "Workflows UI/UX"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest \
  tests.test_workflows_service.WorkflowServiceTests.test_service_can_create_active_context \
  tests.test_workflows_service.WorkflowServiceTests.test_service_can_confirm_context_for_existing_capture \
  -v
```

Expected: FAIL because `create_context` / `apply_context_decision` / thread state do not exist yet.

- [ ] **Step 3: Add the thread-state models**

```python
@dataclass
class WorkflowThreadState:
    confirmed_context_id: str | None = None
    suggested_context_id: str | None = None
    suggested_context_title: str | None = None
    suggestion_active: bool = False
    context_decision: str | None = None
    suggestion_basis: str | None = None
    suggested_at: str | None = None
    context_decision_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

Extend `WorkflowResultPayload` with:

```python
related_thread: dict[str, Any] | None = None
```

Extend `WorkflowCaptureRecord` with:

```python
threading: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4: Add repository persistence**

```python
def _context_doc(self, uid: str, context_id: str):
    return self.db.collection("users").document(uid).collection("workflow_contexts").document(context_id)


def list_active_contexts(self, uid: str, limit: int = 12):
    query = (
        self.db.collection("users")
        .document(uid)
        .collection("workflow_contexts")
        .where("status", "==", "active")
        .order_by("last_activity_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    items = []
    for snap in query.stream():
        payload = snap.to_dict() or {}
        payload["context_id"] = snap.id
        items.append(payload)
    return items


def update_capture_threading(self, uid: str, capture_id: str, threading: dict):
    self._doc(uid, capture_id).set({"threading": threading, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
```

- [ ] **Step 5: Add service methods and thread display shaping**

```python
def _build_related_thread_payload(record: dict, context: dict | None) -> dict:
    threading = record.get("threading") or {}
    return {
        "confirmed_title": context.get("title") if context else None,
        "suggested_title": threading.get("suggested_context_title"),
        "suggestion_active": bool(threading.get("suggestion_active")),
    }


def create_context(self, uid: str, *, title: str, summary: str = "", seed_capture_id: str | None = None) -> dict:
    context_id = f"ctx-{secrets.token_hex(6)}"
    now = self.now_provider()
    return self.repository.create_context(
        uid,
        context_id=context_id,
        title=title.strip(),
        summary=summary.strip(),
        seed_capture_id=seed_capture_id,
        now=now,
    )
```

For `apply_context_decision`, implement only:

- `confirmed`
- `kept_separate`

in Task 1.

Do not implement suggestion ranking yet.

- [ ] **Step 6: Run the targeted tests to verify they pass**

Run:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest \
  tests.test_workflows_service.WorkflowServiceTests.test_service_can_create_active_context \
  tests.test_workflows_service.WorkflowServiceTests.test_service_can_confirm_context_for_existing_capture \
  -v
```

Expected: PASS

- [ ] **Step 7: Run the local app persistence tests**

Run:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest tests.test_workflows_local_app -v
```

Expected: PASS, including any new test that recreates the app and confirms the thread state persists.

- [ ] **Step 8: Commit**

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
git add functions/workflows/models.py functions/workflows/repository.py functions/workflows/service.py tests/test_workflows_service.py tests/test_workflows_local_app.py
git commit -m "feat: persist workflow thread state"
```

### Task 2: Add manual thread attach endpoints and immediate result-page controls

**Files:**
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/blueprint.py`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/service.py`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/public/workflows.js`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/public/workflows.css`
- Test: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_api.py`
- Test: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_static_contract.py`

**Interfaces:**
- Consumes:
  - `WorkflowService.list_active_contexts(uid, limit=12) -> list[dict]`
  - `WorkflowService.apply_context_decision(uid: str, capture_id: str, *, action: str, context_id: str | None = None, new_context_title: str | None = None) -> dict`
- Produces:
  - `GET /workflows/contexts`
  - `POST /workflows/captures/<capture_id>/context-decision`
  - frontend helpers:
    - `renderRelatedThreadBlock(payload, options)`
    - `submitThreadDecision(captureId, action, options = {})`

- [ ] **Step 1: Write failing API tests**

```python
def test_list_active_contexts_returns_active_threads_only(self):
    context_a = service.create_context("user-1", title="Workflows UI/UX", summary="")
    context_b = service.create_context("user-1", title="Voice capture", summary="")
    repo.contexts[("user-1", context_b["context_id"])]["status"] = "archived"

    response = client.get("/workflows/contexts")

    assert response.status_code == 200
    assert [item["title"] for item in response.get_json()["items"]] == ["Workflows UI/UX"]


def test_context_decision_endpoint_confirms_thread_for_capture(self):
    capture = service.create_text_capture(
        uid="user-1",
        source_text="Met with Jordan about the workflows page. Action: revise the result card.",
        context_hint="",
    )
    context = service.create_context("user-1", title="Workflows UI/UX", summary="")

    response = client.post(
        f"/workflows/captures/{capture.capture_id}/context-decision",
        json={"action": "confirmed", "context_id": context["context_id"]},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["threading"]["confirmed_context_id"] == context["context_id"]
    assert payload["result"]["related_thread"]["confirmed_title"] == "Workflows UI/UX"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest \
  tests.test_workflows_api.WorkflowApiTests.test_list_active_contexts_returns_active_threads_only \
  tests.test_workflows_api.WorkflowApiTests.test_context_decision_endpoint_confirms_thread_for_capture \
  -v
```

Expected: FAIL because the endpoints do not exist yet.

- [ ] **Step 3: Add the new endpoints**

```python
@blueprint.route("/contexts", methods=["GET"])
def list_contexts():
    uid = verify_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    service = service_provider()
    return jsonify({"items": service.list_active_contexts(uid)})


@blueprint.route("/contexts", methods=["POST"])
def create_context():
    uid = verify_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    if len(title) < 2:
        return jsonify({"error": "title required"}), 400
    service = service_provider()
    created = service.create_context(
        uid,
        title=title,
        summary=(payload.get("summary") or "").strip(),
        seed_capture_id=payload.get("seed_capture_id"),
    )
    return jsonify(created), 201


@blueprint.route("/captures/<capture_id>/context-decision", methods=["POST"])
def apply_context_decision(capture_id: str):
    uid = verify_token(request)
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    service = service_provider()
    updated = service.apply_context_decision(
        uid,
        capture_id,
        action=(payload.get("action") or "").strip(),
        context_id=payload.get("context_id"),
        new_context_title=(payload.get("new_context_title") or "").strip() or None,
    )
    return jsonify(updated)
```

- [ ] **Step 4: Add the manual result-page controls**

For Task 2, do not add automatic suggestion yet. Add a quiet block only when:

- the current route is an immediate result route
- the capture has no confirmed thread
- there is at least one active thread

Use copy like:

```javascript
This belongs with an ongoing thread.
```

Actions:

- `Keep with a thread`
- `Keep separate`

This is a temporary plumbing surface for the first slice. Task 3 replaces it with the final suggestion copy.

Implementation shape:

```javascript
async function loadActiveThreads() {
  const payload = await apiFetch("/api/workflows/contexts");
  return payload.items || [];
}

async function submitThreadDecision(captureId, action, options = {}) {
  return apiFetch(`${API_CAPTURES_PATH}/${encodeURIComponent(captureId)}/context-decision`, {
    method: "POST",
    body: JSON.stringify({
      action,
      context_id: options.contextId || null,
      new_context_title: options.newContextTitle || null,
    }),
  });
}
```

- [ ] **Step 5: Add a lightweight chooser**

Use an inline chooser block under the result card, not a new route and not a modal.

```javascript
function renderThreadChooser(threads) {
  return `
    <div class="workflows-thread-chooser">
      ${threads.map((thread) => `
        <button type="button" class="workflows-thread-option" data-context-id="${escapeHtml(thread.context_id)}">
          ${escapeHtml(thread.title)}
        </button>
      `).join("")}
    </div>
  `;
}
```

Task 2 may omit `Create new thread` if needed to keep this slice small.

- [ ] **Step 6: Run API and static contract tests**

Run:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest tests.test_workflows_api tests.test_workflows_static_contract -v
node --check public/workflows.js
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
git add functions/workflows/blueprint.py functions/workflows/service.py public/workflows.js public/workflows.css tests/test_workflows_api.py tests/test_workflows_static_contract.py
git commit -m "feat: add manual workflow thread attachment"
```

### Task 3: Add automatic suggestion ranking and final v1 result-page behavior

**Files:**
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/functions/workflows/service.py`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/public/workflows.js`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/public/workflows.css`
- Create: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_threading.py`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_service.py`
- Modify: `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_static_contract.py`

**Interfaces:**
- Consumes:
  - persisted thread records
  - persisted capture thread state
- Produces:
  - `WorkflowService.suggest_context_for_capture(uid, record) -> dict | None`
  - final result payload shape:

```python
{
    "related_thread": {
        "confirmed_title": "Workflows UI/UX",
        "suggested_title": "Workflows UI/UX",
        "suggestion_active": True,
    }
}
```

- [ ] **Step 1: Write failing ranking and suppression tests**

Create `/Users/eitan/memnon-worktrees/workflows-vertical-slice/tests/test_workflows_threading.py` with:

```python
def test_explicit_context_hint_beats_other_threads():
    repo = FakeRepository()
    service = WorkflowService(
        repository=repo,
        note_generator=lambda *_args, **_kwargs: {
            "title": "Workflows page conversation with Jordan",
            "framing_line": "A saved note shaped around one concrete next step.",
            "key_point": "The result card still feels too generic.",
            "next_step": "Revise the result card.",
        },
        now_provider=lambda: "2026-06-29T12:00:00Z",
        api_key_provider=lambda: "test-key",
    )
    service.create_context("user-1", title="Workflows UI/UX", summary="")
    service.create_context("user-1", title="Voice capture", summary="")
    capture = service.create_text_capture(
        uid="user-1",
        source_text="Met with Jordan about the workflows page. Action: revise the result card.",
        context_hint="workflows ui/ux",
    )
    suggested = service.suggest_context_for_capture("user-1", capture.to_dict())
    assert suggested["suggested_context_title"] == "Workflows UI/UX"


def test_no_suggestion_when_no_active_threads_exist():
    repo = FakeRepository()
    service = WorkflowService(
        repository=repo,
        note_generator=lambda *_args, **_kwargs: {
            "title": "Workflows page conversation with Jordan",
            "framing_line": "A saved note shaped around one concrete next step.",
            "key_point": "The result card still feels too generic.",
            "next_step": "Revise the result card.",
        },
        now_provider=lambda: "2026-06-29T12:00:00Z",
        api_key_provider=lambda: "test-key",
    )
    capture = service.create_text_capture(
        uid="user-1",
        source_text="Met with Jordan about the workflows page. Action: revise the result card.",
        context_hint="",
    )
    assert service.suggest_context_for_capture("user-1", capture.to_dict()) is None


def test_no_suggestion_for_weak_saved_note():
    repo = FakeRepository()
    service = WorkflowService(
        repository=repo,
        note_generator=lambda *_args, **_kwargs: {
            "title": "Unused",
            "framing_line": "Unused",
            "key_point": "Unused",
            "next_step": "Unused",
        },
        now_provider=lambda: "2026-06-29T12:00:00Z",
        api_key_provider=lambda: "test-key",
    )
    service.create_context("user-1", title="Workflows UI/UX", summary="")
    capture = service.create_text_capture(
        uid="user-1",
        source_text="follow up tomorrow",
        context_hint="",
    )
    assert service.suggest_context_for_capture("user-1", capture.to_dict()) is None


def test_no_suggestion_for_noisy_voice_result_even_with_matching_words():
    repo = FakeRepository()
    service = WorkflowService(
        repository=repo,
        note_generator=lambda *_args, **_kwargs: {
            "title": "Unused",
            "framing_line": "Unused",
            "key_point": "Unused",
            "next_step": "Unused",
        },
        now_provider=lambda: "2026-06-29T12:00:00Z",
        api_key_provider=lambda: "test-key",
    )
    service.create_context("user-1", title="Voice capture", summary="")
    capture = service.create_text_capture(
        uid="user-1",
        source_text="Thanks for listening. Subscribe wherever you get your podcasts and join us next episode.",
        context_hint="voice capture",
        input_type="voice",
    )
    assert service.suggest_context_for_capture("user-1", capture.to_dict()) is None


def test_prior_confirmed_pattern_raises_existing_thread_priority():
    repo = FakeRepository()
    service = WorkflowService(
        repository=repo,
        note_generator=lambda *_args, **_kwargs: {
            "title": "Workflows page conversation with Jordan",
            "framing_line": "A saved note shaped around one concrete next step.",
            "key_point": "The result card still feels too generic.",
            "next_step": "Revise the result card.",
        },
        now_provider=lambda: "2026-06-29T12:00:00Z",
        api_key_provider=lambda: "test-key",
    )
    target = service.create_context("user-1", title="Workflows UI/UX", summary="")
    other = service.create_context("user-1", title="Voice capture", summary="")
    prior = service.create_text_capture(
        uid="user-1",
        source_text="Met with Jordan about the workflows page. Action: revise the result card.",
        context_hint="",
    )
    service.apply_context_decision("user-1", prior.capture_id, action="confirmed", context_id=target["context_id"])
    current = service.create_text_capture(
        uid="user-1",
        source_text="Jordan thinks the workflows page still feels too generic.",
        context_hint="",
    )
    suggested = service.suggest_context_for_capture("user-1", current.to_dict())
    assert suggested["suggested_context_title"] == "Workflows UI/UX"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest tests.test_workflows_threading -v
```

Expected: FAIL because no ranking helper exists yet.

- [ ] **Step 3: Implement narrow v1 ranking**

Implement scoring in `service.py` with one helper:

```python
def suggest_context_for_capture(self, uid: str, record: dict) -> dict | None:
    if _should_suppress_thread_suggestion(record):
        return None

    threads = self.repository.list_active_contexts(uid, limit=12)
    if not threads:
        return None

    scored = [
        (self._score_context_match(record, thread), thread)
        for thread in threads
    ]
    scored.sort(key=lambda item: item[0], reverse=True)

    best_score, best_thread = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else -1

    if best_score < 5:
        return None
    if best_score - runner_up_score < 2:
        return None

    return {
        "suggested_context_id": best_thread["context_id"],
        "suggested_context_title": best_thread["title"],
        "suggestion_active": True,
        "suggestion_basis": "ranked_match",
        "suggested_at": self.now_provider(),
    }
```

Use these weights:

- explicit context hint exact/near match: `+5`
- repeated named entity match: `+4`
- title/topic phrase overlap: `+4`
- recent activity recency boost: `+2`
- prior confirmed attachment pattern: `+2`
- weak/noisy/mixed/ambiguous source: suppress or large negative

- [ ] **Step 4: Replace the temporary block with final copy**

Result-page behavior for immediate post-capture only:

```javascript
This looks related to Workflows UI/UX.
```

Actions:

- `Continue there`
- `Keep separate`
- `Choose another`

Rules:

- show only when `related_thread.suggestion_active === true`
- do not show on reopened saved results
- if `confirmed_title` exists and `suggestion_active === false`, show only quiet display text

Implementation helpers:

```javascript
function isImmediateResultNavigation(payload) {
  return Boolean(payload?.threading?.suggestion_active);
}

function renderRelatedThreadSuggestion(payload, threads = []) {
  const related = payload?.result?.related_thread || {};
  if (!related.suggestion_active || !related.suggested_title) {
    return "";
  }
  return `
    <div class="workflows-related-thread-block">
      <p class="workflows-related-thread-copy">This looks related to ${escapeHtml(related.suggested_title)}.</p>
      <div class="workflows-related-thread-actions">
        <button type="button" class="btn btn-primary" id="confirm-related-thread">Continue there</button>
        <button type="button" class="btn btn-outline" id="keep-thread-separate">Keep separate</button>
        <button type="button" class="btn btn-quiet" id="choose-another-thread">Choose another</button>
      </div>
      ${renderThreadChooser(threads)}
    </div>
  `;
}
function renderConfirmedThreadDisplay(payload) {
  const related = payload?.result?.related_thread || {};
  if (!related.confirmed_title) {
    return "";
  }
  return `<p class="workflows-related-thread-confirmed">Related to ${escapeHtml(related.confirmed_title)}</p>`;
}
```

- [ ] **Step 5: Decide `Choose another` scope**

Include `Choose another` in Task 3 if the inline chooser from Task 2 already exists and is stable.

If Task 2 needed to ship smaller, then Task 3 must add:

- `Choose another`
- recent active thread list
- `Create new thread: <title>` inside chooser only

Do not expose new thread creation on the main result page.

- [ ] **Step 6: Run full workflows verification**

Run:

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
.venv/bin/python -m unittest tests.test_workflows_routing tests.test_workflows_service tests.test_workflows_threading tests.test_workflows_local_app tests.test_workflows_api tests.test_workflows_static_contract -v
node --check public/workflows.js
```

Expected: PASS

- [ ] **Step 7: Manual browser verification**

Run local backend and static server, then verify:

1. capture with no active threads → no suggestion block
2. capture with one clear matching thread → suggestion block appears
3. `Continue there` confirms the thread and removes the prompt
4. `Keep separate` hides the prompt and leaves result standalone
5. reopened saved result shows confirmed thread quietly
6. reopened saved result with no confirmed thread shows nothing
7. weak/noisy capture shows no suggestion block

- [ ] **Step 8: Commit**

```bash
cd /Users/eitan/memnon-worktrees/workflows-vertical-slice
git add functions/workflows/service.py public/workflows.js public/workflows.css tests/test_workflows_threading.py tests/test_workflows_service.py tests/test_workflows_static_contract.py
git commit -m "feat: add workflow thread suggestions"
```

## What Should Remain Deferred

Keep these deferred even after Task 3:

- suggestions on reopened saved results
- multi-thread membership
- archived thread suggestion
- thread merge/split
- hierarchy
- dashboard route
- folders/tags overhaul
- visible explanation UI
- visible confidence language
- autonomous clustering
- agentic thread orchestration

## Recommended First Commit

**`feat: persist workflow thread state`**

Why first:

- it proves the Firestore model
- it gives capture records a stable continuity shape
- it allows later UI work to refresh without recomputing unstable thread data
- it isolates storage bugs before ranking logic is introduced

## Success Criteria For The First Commit

The first commit is successful if all of these are true:

- a user-owned active thread can be created and listed
- a saved workflow capture can persist `threading` fields
- confirming a thread for an existing capture persists `confirmed_context_id`
- the fetched result payload includes quiet confirmed-thread display data
- no suggestion logic is required yet
- existing workflows capture and result tests still pass

## Spec Coverage Check

- thread persistence: Task 1
- confirmed thread display later: Tasks 1 and 2
- quiet result-page controls: Tasks 2 and 3
- one confirmed thread per result: Task 1
- no suggestion on reopened results: Task 3 tests and render rules
- suppression for weak/noisy results: Task 3 ranking and tests
- `Choose another` as secondary path: Task 3
- deferred dashboard/project-management behavior: explicit defer list above
