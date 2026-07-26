import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.service import WorkflowService
from workflows.quality import transcript_quality_check


NOISY_PODCAST_TRANSCRIPT = """Americans have been spending their time in the preceding year, shout out by the way to economist Joey Politano for spotting this. The amount of time we spent socializing in person is down again just 35 minutes a day up a bit from its pandemic low but still down 10 percent from 2019 off 25 percent in 2003. Our daily production team includes Andy Corbin, Maria Hollenhorst, Sarah Leeson, Sean McHenry and Sophia Terenzia. Will Story is the supervising senior for June 30. To what extent has the U.S. placed pressure, if any, on Romania to drop the investigations? Well there's an interesting confluence of the very"""

WEAK_EXTERNAL_MEDIA_TRANSCRIPT = (
    "Okay, this next category is the more. "
    "A food reviewer says the tasting menu is expensive, but the pacing and variety make the meal feel worth it."
)


class FakeRepository:
    def __init__(self):
        self.records = {}
        self.contexts = {}
        self.user_profiles = {
            "user-1": {
                "lane": "professional",
                "profession": "teacher",
                "reflection_style": "practical",
                "reflect_config": {"selected_guides": ["parker_palmer"]},
            }
        }

    def load_user_profile(self, uid):
        return self.user_profiles.get(uid, {"lane": "professional", "profession": "professional"})

    def save_capture(self, uid, record):
        self.records[(uid, record.capture_id)] = record.to_dict()
        return record.capture_id

    def get_capture(self, uid, capture_id):
        return self.records.get((uid, capture_id))

    def list_captures(self, uid, limit=50):
        items = [
            value
            for (record_uid, _capture_id), value in self.records.items()
            if record_uid == uid
        ]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return items[:limit]

    def create_context(self, uid, *, context_id, title, summary, seed_capture_id, now):
        context = {
            "context_id": context_id,
            "title": title,
            "summary": summary,
            "status": "active",
            "seed_capture_id": seed_capture_id,
            "created_at": now,
            "updated_at": now,
            "last_activity_at": now,
        }
        self.contexts[(uid, context_id)] = context
        return context

    def get_context(self, uid, context_id):
        return self.contexts.get((uid, context_id))

    def list_active_contexts(self, uid, limit=12):
        items = [
            value
            for (context_uid, _context_id), value in self.contexts.items()
            if context_uid == uid and value.get("status") == "active"
        ]
        items.sort(key=lambda item: item.get("last_activity_at", ""), reverse=True)
        return items[:limit]

    def update_capture_threading(self, uid, capture_id, threading):
        record = self.records[(uid, capture_id)]
        record["threading"] = dict(threading)
        record["updated_at"] = threading.get("context_decision_at", record.get("updated_at"))

    def update_capture_feedback(self, uid, capture_id, feedback_choice, feedback_note, feedback_updated_at):
        record = self.records[(uid, capture_id)]
        record["feedback_choice"] = feedback_choice
        record["feedback_note"] = feedback_note
        record["feedback_updated_at"] = feedback_updated_at

    def touch_context_activity(self, uid, context_id, now):
        context = self.contexts[(uid, context_id)]
        context["last_activity_at"] = now
        context["updated_at"] = now
        return context


class WorkflowServiceTests(unittest.TestCase):
    def test_service_triggers_continuity_bridge_for_text_capture_with_default_context_flag(self):
        repo = FakeRepository()
        bridge_calls = []
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Product direction conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card.",
            },
            now_provider=lambda: "2026-07-02T16:00:00Z",
            api_key_provider=lambda: "test-key",
            continuity_bridge_writer=lambda **payload: bridge_calls.append(payload),
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the product direction. Action: revise the result card.",
            context_hint="product review",
        )

        self.assertEqual(len(bridge_calls), 1)
        bridge_call = bridge_calls[0]
        self.assertEqual(bridge_call["uid"], "user-1")
        self.assertEqual(bridge_call["include_teaching_context"], True)
        self.assertEqual(bridge_call["capture_record"]["capture_id"], record.capture_id)
        self.assertEqual(bridge_call["capture_record"]["source_event"]["input_type"], "text")
        self.assertEqual(bridge_call["profile"]["reflection_style"], "practical")

    def test_service_triggers_continuity_bridge_for_voice_and_file_captures(self):
        repo = FakeRepository()
        bridge_calls = []
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workshop plan draft",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The plan needs one tighter opener and one clear follow-up.",
                "next_step": "Tighten the opening before sharing it.",
            },
            now_provider=lambda: "2026-07-02T16:00:00Z",
            api_key_provider=lambda: "test-key",
            continuity_bridge_writer=lambda **payload: bridge_calls.append(payload),
        )

        service.create_text_capture(
            uid="user-1",
            source_text="Talked through the workshop plan. Action: tighten the opening before sharing it.",
            context_hint="product review",
            input_type="voice",
        )
        service.create_text_capture(
            uid="user-1",
            source_text="# Workshop plan\n\nDraft the opening more tightly, then send the revision by Friday.",
            context_hint="product review",
            input_type="file",
            source_metadata={
                "source_filename": "workshop-plan.md",
                "source_file_type": "text/markdown",
                "source_file_extension": ".md",
                "source_file_size_bytes": 88,
            },
            include_teaching_context=False,
        )

        self.assertEqual(
            [call["capture_record"]["source_event"]["input_type"] for call in bridge_calls],
            ["voice", "file"],
        )
        self.assertEqual(bridge_calls[1]["include_teaching_context"], False)
        self.assertEqual(
            bridge_calls[1]["capture_record"]["source_event"]["source_filename"],
            "workshop-plan.md",
        )

    def test_regenerate_capture_re_triggers_continuity_bridge_with_fresh_content(self):
        repo = FakeRepository()
        bridge_calls = []
        generated = {
            "title": "Original title",
            "framing_line": "Original framing.",
            "summary": "Original summary text.",
            "next_step": "Original next step.",
        }
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: generated,
            now_provider=lambda: "2026-07-02T16:00:00Z",
            api_key_provider=lambda: "test-key",
            continuity_bridge_writer=lambda **payload: bridge_calls.append(payload),
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the product direction. Action: revise the result card.",
            context_hint="product review",
        )
        self.assertEqual(len(bridge_calls), 1)
        self.assertIn("Original summary text", bridge_calls[0]["capture_record"]["result"]["primary_artifact"]["summary"])

        generated = {
            "title": "Regenerated title",
            "framing_line": "Regenerated framing.",
            "summary": "Regenerated summary text, materially different from the first pass.",
            "next_step": "Regenerated next step.",
        }
        service.regenerate_capture("user-1", record.capture_id)

        self.assertEqual(len(bridge_calls), 2)
        self.assertIn(
            "Regenerated summary text",
            bridge_calls[1]["capture_record"]["result"]["primary_artifact"]["summary"],
        )

    def test_immediate_capture_includes_contextual_suggestions_when_signal_is_clear(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "BoulderJS meetup recap",
                "framing_line": "A saved result shaped around the strongest public takeaway.",
                "summary": "The meetup recap should become a public-facing post for the community.",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-03T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "BoulderJS meetup recap: thanks to everyone who came tonight. "
                "Share the highlights and invite the community to next week's event."
            ),
            context_hint="",
        )

        suggestions = record.result.get("contextual_suggestions") or []
        self.assertEqual([item["type"] for item in suggestions], ["draft_social_post"])
        self.assertEqual(
            record.event_manifest["contextual_suggestions"]["shown_types"],
            ["draft_social_post"],
        )

    def test_reopened_capture_hides_contextual_suggestions(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Product strategy podcast notes",
                "framing_line": "A saved result shaped around the sharpest professional takeaway.",
                "summary": "The notes point toward retention and positioning analysis.",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-03T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Podcast notes on product strategy, retention, and pricing tradeoffs. "
                "The transcript is worth analyzing through a professional lens."
            ),
            context_hint="",
        )

        reopened = service.get_capture("user-1", record.capture_id)

        self.assertNotIn("contextual_suggestions", reopened["result"])
        self.assertEqual(
            reopened["event_manifest"]["contextual_suggestions"]["shown_types"],
            ["analyze_professionally"],
        )

    def test_feedback_rerender_preserves_immediate_contextual_suggestions(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "BoulderJS meetup recap",
                "framing_line": "A saved result shaped around the strongest public takeaway.",
                "summary": "The meetup recap should become a public-facing post for the community.",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-03T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "BoulderJS meetup recap: thanks to everyone who came tonight. "
                "Share the highlights and invite the community to next week's event."
            ),
            context_hint="",
        )

        updated = service.apply_feedback_choice(
            "user-1",
            record.capture_id,
            feedback_choice="useful",
        )

        suggestions = updated["result"].get("contextual_suggestions") or []
        self.assertEqual([item["type"] for item in suggestions], ["draft_social_post"])

    def test_thread_decision_rerender_preserves_immediate_contextual_suggestions(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "BoulderJS meetup recap",
                "framing_line": "A saved result shaped around the strongest public takeaway.",
                "summary": "The meetup recap should become a public-facing post for the community.",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-03T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "BoulderJS meetup recap: thanks to everyone who came tonight. "
                "Share the highlights and invite the community to next week's event."
            ),
            context_hint="boulderjs",
        )
        context = service.create_context("user-1", title="BoulderJS", summary="")

        updated = service.apply_context_decision(
            "user-1",
            record.capture_id,
            action="confirmed",
            context_id=context["context_id"],
        )

        suggestions = updated["result"].get("contextual_suggestions") or []
        self.assertEqual([item["type"] for item in suggestions], ["draft_social_post"])

    def test_apply_contextual_suggestion_creates_new_saved_result_without_mutating_original(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "BoulderJS meetup recap",
                "framing_line": "A saved result shaped around the strongest public takeaway.",
                "summary": "The meetup recap should become a public-facing post for the community.",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-03T12:00:00Z",
            api_key_provider=lambda: "test-key",
            social_post_generator=lambda *_args, **_kwargs: {
                "title": "BoulderJS next meetup",
                "framing_line": "A concise social draft built from the capture.",
                "body": "Thanks to everyone who came to BoulderJS tonight. Join us next week for the next meetup.",
                "sections": [],
                "copy_text": "Thanks to everyone who came to BoulderJS tonight. Join us next week for the next meetup.",
            },
            professional_analysis_generator=lambda *_args, **_kwargs: {
                "title": "Unused",
                "framing_line": "Unused",
                "body": "Unused",
                "sections": [],
                "copy_text": "Unused",
            },
        )

        original = service.create_text_capture(
            uid="user-1",
            source_text=(
                "BoulderJS meetup recap: thanks to everyone who came tonight. "
                "Share the highlights and invite the community to next week's event."
            ),
            context_hint="",
        )

        derived = service.apply_contextual_suggestion(
            "user-1",
            original.capture_id,
            suggestion_type="draft_social_post",
        )

        self.assertNotEqual(derived["capture_id"], original.capture_id)
        self.assertEqual(
            original.result["contextual_suggestions"][0]["type"],
            "draft_social_post",
        )
        self.assertNotIn("contextual_suggestions", derived["result"])
        self.assertEqual(
            derived["event_manifest"]["contextual_suggestions"]["origin"],
            "derived_result",
        )
        self.assertEqual(
            derived["event_manifest"]["contextual_suggestions"]["parent_capture_id"],
            original.capture_id,
        )

    def test_service_can_create_active_context(self):
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

        self.assertEqual(created["title"], "Workflows UI/UX")
        self.assertEqual(created["status"], "active")
        self.assertEqual(created["seed_capture_id"], "cap-seed")

    def test_service_can_confirm_context_for_existing_capture(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
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

        self.assertEqual(updated["threading"]["confirmed_context_id"], context["context_id"])
        self.assertFalse(updated["threading"]["suggestion_active"])
        self.assertEqual(
            updated["result"]["related_thread"]["confirmed_title"],
            "Workflows UI/UX",
        )

    def test_reopened_capture_with_no_thread_state_stays_quiet(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
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

        reopened = service.get_capture("user-1", capture.capture_id)

        self.assertEqual(reopened.get("threading"), {})
        self.assertNotIn("related_thread", reopened["result"])

    def test_immediate_capture_includes_ranked_thread_suggestion(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
                "next_step": "Revise the result card.",
            },
            now_provider=lambda: "2026-06-29T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        context = service.create_context("user-1", title="Workflows UI/UX", summary="")

        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="workflows ui/ux",
        )

        self.assertEqual(capture.threading["suggested_context_id"], context["context_id"])
        self.assertTrue(capture.threading["suggestion_active"])
        self.assertEqual(
            capture.result["related_thread"],
            {
                "confirmed_title": None,
                "suggested_title": "Workflows UI/UX",
                "suggestion_active": True,
            },
        )

    def test_kept_separate_clears_prior_confirmed_thread_linkage(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
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
        service.apply_context_decision(
            "user-1",
            capture.capture_id,
            action="confirmed",
            context_id=context["context_id"],
        )

        updated = service.apply_context_decision(
            "user-1",
            capture.capture_id,
            action="kept_separate",
        )

        self.assertNotIn("confirmed_context_id", updated["threading"])
        self.assertEqual(updated["threading"]["context_decision"], "kept_separate")
        self.assertNotIn("related_thread", updated["result"])

    def test_feedback_choice_persists_without_mutating_result_or_threading(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
                "next_step": "Revise the result card.",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="",
        )
        original_result = capture.result.copy()
        original_threading = capture.threading.copy()

        updated = service.apply_feedback_choice(
            "user-1",
            capture.capture_id,
            feedback_choice="useful",
        )

        self.assertEqual(updated["feedback_choice"], "useful")
        self.assertEqual(updated["feedback_updated_at"], "2026-07-01T18:00:00Z")
        self.assertEqual(updated["result"], original_result)
        self.assertEqual(updated["threading"], original_threading)

    def test_feedback_choice_can_be_replaced(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
                "next_step": "Revise the result card.",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="",
        )

        service.apply_feedback_choice(
            "user-1",
            capture.capture_id,
            feedback_choice="useful",
        )
        updated = service.apply_feedback_choice(
            "user-1",
            capture.capture_id,
            feedback_choice="not_useful",
        )

        self.assertEqual(updated["feedback_choice"], "not_useful")
        self.assertEqual(updated["feedback_updated_at"], "2026-07-01T18:00:00Z")

    def test_feedback_choice_rejects_invalid_value(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
                "next_step": "Revise the result card.",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="",
        )

        with self.assertRaises(ValueError):
            service.apply_feedback_choice(
                "user-1",
                capture.capture_id,
                feedback_choice="too_generic",
            )

    def test_feedback_choice_does_not_change_kept_separate_behavior(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
                "next_step": "Revise the result card.",
            },
            now_provider=lambda: "2026-07-01T18:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="",
        )
        service.apply_context_decision(
            "user-1",
            capture.capture_id,
            action="kept_separate",
        )

        updated = service.apply_feedback_choice(
            "user-1",
            capture.capture_id,
            feedback_choice="not_useful",
        )

        self.assertEqual(updated["feedback_choice"], "not_useful")
        self.assertEqual(updated["threading"]["context_decision"], "kept_separate")
        self.assertNotIn("related_thread", updated["result"])

    def test_selecting_another_existing_thread_records_alternate_decision(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result card still feels too generic.",
                "next_step": "Revise the result card.",
            },
            now_provider=lambda: "2026-06-29T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        suggested_context = service.create_context("user-1", title="Workflows UI/UX", summary="")
        alternate_context = service.create_context("user-1", title="Voice capture", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="workflows ui/ux",
        )

        self.assertEqual(capture.threading["suggested_context_id"], suggested_context["context_id"])

        updated = service.apply_context_decision(
            "user-1",
            capture.capture_id,
            action="selected_different_context",
            context_id=alternate_context["context_id"],
        )

        self.assertEqual(updated["threading"]["confirmed_context_id"], alternate_context["context_id"])
        self.assertEqual(updated["threading"]["context_decision"], "selected_different_context")
        self.assertEqual(
            updated["result"]["related_thread"]["confirmed_title"],
            "Voice capture",
        )
        reopened = service.get_capture("user-1", capture.capture_id)
        self.assertEqual(reopened["threading"]["context_decision"], "selected_different_context")
        self.assertEqual(
            reopened["result"]["related_thread"]["confirmed_title"],
            "Voice capture",
        )
        self.assertFalse(reopened["threading"].get("suggestion_active"))

    def test_creating_new_thread_from_decision_confirms_it_quietly_on_reopen(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Voice capture product direction",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result should stay attached to the new thread.",
                "next_step": "Keep the result linked quietly on reopen.",
            },
            now_provider=lambda: "2026-06-29T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Need a dedicated thread for voice capture product direction. Action: keep the result linked quietly on reopen.",
            context_hint="",
        )

        updated = service.apply_context_decision(
            "user-1",
            capture.capture_id,
            action="created_new_context",
            new_context_title="Voice capture",
        )

        confirmed_context_id = updated["threading"]["confirmed_context_id"]
        self.assertTrue(confirmed_context_id)
        self.assertEqual(updated["threading"]["context_decision"], "created_new_context")
        self.assertEqual(
            updated["result"]["related_thread"]["confirmed_title"],
            "Voice capture",
        )
        created_context = repo.get_context("user-1", confirmed_context_id)
        self.assertIsNotNone(created_context)
        self.assertEqual(created_context["title"], "Voice capture")
        self.assertEqual(created_context["seed_capture_id"], capture.capture_id)

        reopened = service.get_capture("user-1", capture.capture_id)
        self.assertEqual(
            reopened["result"]["related_thread"],
            {
                "confirmed_title": "Voice capture",
                "suggested_title": None,
                "suggestion_active": False,
            },
        )

    def test_service_can_record_voice_capture_metadata(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to feel more like a saved object than a generated response.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the product direction. "
                "Action: revise the result card before the next demo."
            ),
            context_hint="product review",
            input_type="voice",
        )

        self.assertEqual(record.input_type, "voice")
        self.assertEqual(record.source_event["input_type"], "voice")
        self.assertEqual(
            record.result["primary_artifact"]["metadata_line"],
            "Voice note · Jun 27, 2026 · Product review",
        )
        self.assertTrue(record.result["primary_artifact"]["source_excerpt"])
        self.assertIn("sections", record.result["primary_artifact"])

    def test_service_preserves_voice_audio_review_metadata_when_present(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Voice capture follow-up",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The result needs to preserve a trustworthy review path.",
                "next_step": "Keep the audio review affordance on the result page.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Talked through the dashboard voice flow. "
                "Action: keep the audio review affordance on the result page."
            ),
            context_hint="voice QA",
            input_type="voice",
            source_metadata={
                "source_audio_storage_path": "workflow-voice-audio/user-1/cap-voice.webm",
                "source_audio_content_type": "audio/webm",
                "source_audio_filename": "voice-note.webm",
                "source_audio_size_bytes": 2048,
            },
        )

        self.assertEqual(record.source_event["source_audio_storage_path"], "workflow-voice-audio/user-1/cap-voice.webm")
        self.assertEqual(record.source_event["source_audio_content_type"], "audio/webm")
        self.assertEqual(record.source_event["source_audio_filename"], "voice-note.webm")
        self.assertEqual(record.source_event["source_audio_size_bytes"], 2048)

    def test_service_can_record_uploaded_file_metadata(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Workshop plan draft",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The plan needs one tighter opener and one clear follow-up.",
                "next_step": "Tighten the opening before sharing it.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text="# Workshop plan\n\nDraft the opening more tightly, then send the revision by Friday.",
            context_hint="product review",
            input_type="file",
            source_metadata={
                "source_filename": "workshop-plan.md",
                "source_file_type": "text/markdown",
                "source_file_extension": ".md",
                "source_file_size_bytes": 84,
            },
        )

        self.assertEqual(record.input_type, "file")
        self.assertEqual(record.source_event["input_type"], "file")
        self.assertEqual(record.source_event["source_filename"], "workshop-plan.md")
        self.assertEqual(record.source_event["source_file_type"], "text/markdown")
        self.assertEqual(record.source_event["source_file_extension"], ".md")
        self.assertEqual(record.source_event["source_file_size_bytes"], 84)
        self.assertEqual(
            record.result["primary_artifact"]["metadata_line"],
            "Uploaded file · Jun 27, 2026 · Product review",
        )
        self.assertTrue(record.result["primary_artifact"]["source_excerpt"])

    def test_file_capture_quality_floor_rejects_generic_saved_title(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Saved",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "",
                "next_step": "",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Need to send the draft to Maya on Tuesday and review project alpha notes before the weekly review."
            ),
            context_hint="",
            input_type="file",
            source_metadata={
                "source_filename": "live-question.txt",
                "source_file_type": "text/plain",
                "source_file_extension": ".txt",
                "source_file_size_bytes": 98,
            },
        )

        artifact = record.result["primary_artifact"]
        self.assertNotEqual((artifact["title"] or "").strip().lower(), "saved")
        self.assertNotEqual((artifact["title"] or "").strip().lower(), "saved note")
        self.assertTrue((artifact["body"] or "").strip())
        self.assertTrue((artifact["summary"] or "").strip())

    def test_service_capture_summary_can_include_short_uploaded_filename(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Workshop plan draft",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The plan needs one tighter opener and one clear follow-up.",
                "next_step": "Tighten the opening before sharing it.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text="Draft the opening more tightly, then send the revision by Friday.",
            context_hint="",
            input_type="file",
            source_metadata={
                "source_filename": "plan.md",
                "source_file_type": "text/markdown",
                "source_file_extension": ".md",
                "source_file_size_bytes": 64,
            },
        )

        items = service.list_capture_summaries("user-1")

        self.assertEqual(items[0]["capture_id"], record.capture_id)
        self.assertEqual(items[0]["metadata_line"], "Uploaded file · Jun 27, 2026")

    def test_service_file_metadata_includes_recording_context_from_filename(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Saved note",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "Keep the strongest point and revisit with one clear direction.",
                "next_step": "",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "This recording captures presentation ideas, audience questions, and the strongest story to lead with. "
                "Action: draft a clean three-point talk track for tomorrow."
            ),
            context_hint="",
            input_type="file",
            source_metadata={
                "source_filename": "Lincoln St 10-2.txt",
                "source_file_type": "text/plain",
                "source_file_extension": ".txt",
                "source_file_size_bytes": 64,
            },
        )

        items = service.list_capture_summaries("user-1")
        artifact = record.result["primary_artifact"] or record.result["saved_note_artifact"]
        self.assertEqual(
            artifact["metadata_line"],
            "Uploaded file · Jun 27, 2026",
        )
        self.assertEqual(items[0]["metadata_line"], "Lincoln St 10 · Jun 27, 2026")

    def test_service_file_metadata_strips_code_like_prefix_from_derived_context(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Saved note",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "Keep one clear talk-point from the recording.",
                "next_step": "",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        service.create_text_capture(
            uid="user-1",
            source_text=(
                "Recorded talk prep notes and examples for a lightning talk. "
                "Action: tighten the opening and one closing takeaway."
            ),
            context_hint="",
            input_type="file",
            source_metadata={
                "source_filename": "ZR 7-13 Flashtalk.txt",
                "source_file_type": "text/plain",
                "source_file_extension": ".txt",
                "source_file_size_bytes": 72,
            },
        )

        items = service.list_capture_summaries("user-1")
        self.assertEqual(items[0]["metadata_line"], "Flashtalk · Jun 27, 2026")

    def test_service_file_metadata_falls_back_when_derived_label_too_long(self):
        repo = FakeRepository()
        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Saved note",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "Keep the strongest point and revisit with one clear direction.",
                "next_step": "",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        service.create_text_capture(
            uid="user-1",
            source_text=(
                "This recording captures preparation details and practice notes for a long presentation thread. "
                "Action: tighten the opening and close with one concrete ask."
            ),
            context_hint="",
            input_type="file",
            source_metadata={
                "source_filename": "Very Long Conference Planning Session And Rehearsal Notes 12-2.txt",
                "source_file_type": "text/plain",
                "source_file_extension": ".txt",
                "source_file_size_bytes": 96,
            },
        )

        items = service.list_capture_summaries("user-1")
        self.assertEqual(items[0]["metadata_line"], "Uploaded file · Jun 27, 2026")

    def test_service_creates_one_professional_note_record(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "The system feels too generic because the result does not yet feel like a saved object.",
                "next_step": "Revise the result card so it includes a stronger title, one excerpt, and one concrete next step.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the product direction. She thinks the system feels too generic "
                "because it is trying to do too much at once instead of making one strong call. "
                "Action: revise the result card this week."
            ),
            context_hint="product review",
        )

        self.assertEqual(record.result["route_kind"], "direct_professional_note")
        self.assertEqual(record.result["primary_artifact"]["kind"], "professional_note")
        self.assertEqual(
            record.result["primary_artifact"]["title"],
            "Product direction conversation with Jordan",
        )
        self.assertEqual(
            record.result["primary_artifact"]["status"],
            "Saved and shaped",
        )
        self.assertEqual(
            record.result["primary_artifact"]["metadata_line"],
            "Pasted note · Jun 27, 2026 · Product review",
        )
        self.assertEqual(
            [section["label"] for section in record.result["primary_artifact"]["sections"]],
            ["Next step"],
        )
        self.assertIn("system feels too generic", record.result["primary_artifact"]["summary"])
        self.assertIn("Revise the result card", record.result["primary_artifact"]["sections"][0]["text"])
        self.assertIn("feels too generic", record.result["primary_artifact"]["source_excerpt"])
        self.assertIn("product direction", record.result["primary_artifact"]["framing_line"].lower())
        self.assertEqual(record.result["secondary_artifacts"], [])
        self.assertEqual(record.source_event["profile_snapshot"]["lane"], "professional")

    def test_service_fetches_saved_capture(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Teacher workflow note",
                "framing_line": "Pulled from your product reflection.",
                "summary": "Start by clarifying the single change that reduces user friction first.",
                "next_step": "Clarify the first workflow before broadening scope.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the product direction. She thinks the system feels too generic "
                "because it is trying to do too much at once instead of making one strong call. "
                "Action: revise the result card this week."
            ),
            context_hint="product review",
        )

        fetched = service.get_capture("user-1", record.capture_id)
        self.assertEqual(fetched["capture_id"], record.capture_id)
        self.assertEqual(fetched["result"]["route_kind"], "direct_professional_note")

    def test_service_lists_saved_results_in_reverse_chronological_order(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Teacher workflow note",
                "framing_line": "Pulled from your product reflection.",
                "summary": "Start by clarifying the single change that reduces user friction first.",
                "next_step": "Clarify the first workflow before broadening scope.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        first = service.create_text_capture(
            uid="user-1",
            source_text="follow up tomorrow",
            context_hint="",
        )
        service.now_provider = lambda: "2026-06-28T09:15:00Z"
        second = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the product direction. "
                "Action: revise the result card before the next demo."
            ),
            context_hint="product review",
        )

        items = service.list_capture_summaries("user-1")

        self.assertEqual([item["capture_id"] for item in items], [second.capture_id, first.capture_id])
        self.assertEqual(items[0]["title"], second.result["primary_artifact"]["title"])
        self.assertEqual(items[0]["next_route"], f"/workflows/result/{second.capture_id}")
        self.assertEqual(items[0]["status"], "Saved and shaped")
        self.assertEqual(items[1]["title"], first.result["saved_note_artifact"]["title"])
        self.assertEqual(items[1]["status"], "Saved as a small note")

    def test_service_list_summaries_include_feedback_choice(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Teacher workflow note",
                "framing_line": "Pulled from your product reflection.",
                "summary": "Start by clarifying the single change that reduces user friction first.",
                "next_step": "Clarify the first workflow before broadening scope.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the product direction. "
                "Action: revise the result card before the next demo."
            ),
            context_hint="product review",
        )

        items_before = service.list_capture_summaries("user-1")
        self.assertEqual(items_before[0]["feedback_choice"], "")

        service.apply_feedback_choice(
            "user-1",
            record.capture_id,
            feedback_choice="useful",
        )

        items_after = service.list_capture_summaries("user-1")
        self.assertEqual(items_after[0]["feedback_choice"], "useful")

    def test_service_persists_feedback_note_alongside_choice(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Teacher workflow note",
                "framing_line": "Pulled from your product reflection.",
                "summary": "Start by clarifying the single change that reduces user friction first.",
                "next_step": "Clarify the first workflow before broadening scope.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the product direction. "
                "Action: revise the result card before the next demo."
            ),
            context_hint="product review",
        )

        updated = service.apply_feedback_choice(
            "user-1",
            record.capture_id,
            feedback_choice="not_useful",
            feedback_note="The next step didn't match what the summary described.",
        )
        self.assertEqual(updated["feedback_note"], "The next step didn't match what the summary described.")

        fetched = service.get_capture("user-1", record.capture_id)
        self.assertEqual(fetched["feedback_note"], "The next step didn't match what the summary described.")

        items = service.list_capture_summaries("user-1")
        self.assertEqual(
            items[0]["feedback_note"], "The next step didn't match what the summary described."
        )

    def test_regenerate_capture_upgrades_legacy_schema_preserving_timestamps_and_feedback(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "Mentorship Program Overview",
                "framing_line": "Saved as a note worth reopening.",
                "summary": "The program spans two phases and focuses on peer mentorship.",
                "next_step": "Access the shared slide deck.",
                "source_quote": "cultivate the culture of peer mentorship",
            },
            now_provider=lambda: "2026-07-19T09:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "This talk is about a two-phase program to cultivate the culture of "
                "peer mentorship among computer science students."
            ),
            context_hint="",
            input_type="file",
            source_metadata={"source_filename": "talk.txt"},
        )

        # Simulate a legacy pre-2026-07-18 record: old "Key point" section shape,
        # no top-level summary field, plus existing feedback that must survive.
        key = ("user-1", record.capture_id)
        legacy_artifact = dict(repo.records[key]["result"]["primary_artifact"])
        legacy_artifact.pop("summary", None)
        legacy_artifact["sections"] = [
            {"label": "Key point", "text": "This document pulls together related materials."},
        ]
        repo.records[key]["result"]["primary_artifact"] = legacy_artifact
        repo.records[key]["created_at"] = "2026-07-17T12:36:00Z"
        repo.records[key]["feedback_choice"] = "not_useful"
        repo.records[key]["feedback_note"] = "Key point was boilerplate."

        updated = service.regenerate_capture("user-1", record.capture_id)
        artifact = updated["result"]["primary_artifact"]

        self.assertTrue(artifact["summary"])
        self.assertNotIn(
            "Key point", [section["label"] for section in artifact["sections"]]
        )
        self.assertEqual(updated["created_at"], "2026-07-17T12:36:00Z")
        self.assertEqual(updated["feedback_choice"], "not_useful")
        self.assertEqual(updated["feedback_note"], "Key point was boilerplate.")

    def test_list_summaries_flag_dev_fixture_captures_without_false_positives(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args, **_kwargs: {
                "title": "",
                "framing_line": "",
                "summary": "",
                "next_step": "",
            },
            now_provider=lambda: "2026-07-19T09:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        # Known dev-pollution shape: no filename, generic title, empty/product-review context.
        record = service.create_text_capture(
            uid="user-1",
            source_text="follow up tomorrow",
            context_hint="product review",
        )
        key = ("user-1", record.capture_id)
        artifact = repo.records[key]["result"]["primary_artifact"] or repo.records[key]["result"]["saved_note_artifact"]
        artifact["title"] = "Saved"

        # A real file capture with a genuine recording-style filename must never be flagged.
        real_record = service.create_text_capture(
            uid="user-1",
            source_text="This talk is about a two-phase mentorship program for CS students.",
            context_hint="",
            input_type="file",
            source_metadata={"source_filename": "Sheraton New Orleans Hotel.txt"},
        )

        items = {item["capture_id"]: item for item in service.list_capture_summaries("user-1")}
        self.assertTrue(items[record.capture_id]["looks_like_dev_data"])
        self.assertFalse(items[real_record.capture_id]["looks_like_dev_data"])

    def test_service_distinguishes_weak_saved_notes_from_ambiguous_ones(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Unused",
                "framing_line": "Unused",
                "summary": "Unused",
                "next_step": "Unused",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        weak_record = service.create_text_capture(
            uid="user-1",
            source_text="follow up tomorrow",
            context_hint="",
        )
        ambiguous_record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Not sure what this should become. Something about the product direction I think. "
                "Could be a note to myself, a follow-up, or maybe just something to hold onto."
            ),
            context_hint="",
        )

        self.assertEqual(weak_record.result["route_kind"], "saved_note")
        self.assertEqual(weak_record.result["saved_note_artifact"]["state"], "weak_signal")
        self.assertEqual(
            weak_record.result["saved_note_artifact"]["status"],
            "Saved as a small note",
        )
        self.assertEqual(
            weak_record.result["saved_note_artifact"]["metadata_line"],
            "Pasted note · Jun 27, 2026",
        )
        self.assertEqual(
            [section["label"] for section in weak_record.result["saved_note_artifact"]["sections"]],
            ["Next step"],
        )
        self.assertIn(
            "Follow up tomorrow",
            weak_record.result["saved_note_artifact"]["sections"][0]["text"],
        )
        self.assertEqual(ambiguous_record.result["route_kind"], "saved_note")
        self.assertEqual(ambiguous_record.result["saved_note_artifact"]["state"], "needs_direction")
        self.assertEqual(ambiguous_record.result["likely_themes"], [])
        self.assertEqual(
            ambiguous_record.result["saved_note_artifact"]["status"],
            "Saved, needs direction",
        )
        self.assertEqual(
            ambiguous_record.result["saved_note_artifact"]["metadata_line"],
            "Pasted note · Jun 27, 2026",
        )
        self.assertNotIn(
            " or ",
            ambiguous_record.result["saved_note_artifact"]["title"].lower(),
        )
        self.assertEqual(
            [section["label"] for section in ambiguous_record.result["saved_note_artifact"]["sections"]],
            ["Why keep this"],
        )
        self.assertIn(
            "worth keeping",
            ambiguous_record.result["saved_note_artifact"]["sections"][0]["text"].lower(),
        )
        self.assertIn(
            "direction",
            ambiguous_record.result["saved_note_artifact"]["sections"][0]["text"].lower(),
        )
        self.assertNotEqual(
            weak_record.result["saved_note_artifact"]["framing_line"],
            ambiguous_record.result["saved_note_artifact"]["framing_line"],
        )

    def test_service_sharpens_generic_product_note_output(self):
        repo = FakeRepository()

        def generic_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Memnon Workflows note",
                "framing_line": "Shaped from your note into one practical artifact to review.",
                "summary": "The note already points toward one useful direction and is worth shaping into a concrete next step.",
                "next_step": "Clarify the single action this note is meant to support before expanding scope.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=generic_ai,
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "I’m thinking about Memnon workflows and the Granola teardown. "
                "The big insight is that Granola splits smartness from durability. "
                "Chat gives the better interpretation, but Quick Note creates the saved object. "
                "Memnon should make the best generated result also be the durable saved artifact."
            ),
            context_hint="product review",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(record.result["route_kind"], "direct_professional_note")
        self.assertIn("Granola", artifact["title"])
        self.assertNotEqual(artifact["title"], "Memnon Workflows note")
        self.assertIn(
            "splits smartness from durability",
            artifact["summary"].lower(),
        )
        self.assertEqual([section["label"] for section in artifact["sections"]], [])
        self.assertNotIn("Next step", artifact["body"])

    def test_service_preserves_actual_ambiguity_for_saved_notes(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "summary": "Unused",
                "next_step": "Unused",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text="This could be a reflection, but it also might be something I need to send to Kyle.",
            context_hint="",
        )

        artifact = record.result["saved_note_artifact"]
        self.assertEqual(artifact["state"], "needs_direction")
        self.assertNotIn(" or ", artifact["title"].lower())
        self.assertIn("kyle", artifact["title"].lower())
        self.assertIn("direction", artifact["sections"][0]["text"].lower())
        self.assertIn("kyle", artifact["sections"][0]["text"].lower())

    def test_service_grounds_generic_summary_in_source_for_pasted_document(self):
        # Document-mode genre-guessing (asserting "this document pulls together...")
        # was retired 2026-07-18: a false claim on real transcripts convicted it.
        # A generic proposed summary now falls back to a grounded sentence from the
        # source instead of a confident, potentially wrong, genre claim.
        repo = FakeRepository()

        def generic_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Professional note",
                "framing_line": "Shaped from your note into one practical artifact to review.",
                "summary": "The note already points toward one useful direction and is worth shaping into a concrete next step.",
                "next_step": "Be able to toggle this calendar on and off as needed",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=generic_ai,
            now_provider=lambda: "2026-06-28T10:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "RAI Interactive Journal for the 2026 Responsible AI Fellowship. "
                "Meeting agendas, feedback on talks, maybe a few open questions for the cohort, "
                "and notes that could shape the next session. "
                "Themes include what resonated, what felt unresolved, and what to revisit. "
                "Be able to toggle this calendar on and off as needed."
            ),
            context_hint="My Thread for the 2026 Responsible AI Fellowship",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(record.result["route_kind"], "direct_professional_note")
        self.assertIn("Responsible AI Fellowship", artifact["title"])
        self.assertIn("RAI Interactive Journal", artifact["summary"])
        self.assertNotIn("pulls together", artifact["summary"].lower())
        self.assertNotIn("reusable reference note", artifact["summary"].lower())
        self.assertEqual(
            artifact["sections"][0]["text"],
            "Be able to toggle this calendar on and off as needed",
        )

    def test_transcript_quality_check_marks_production_credit_audio_as_noisy(self):
        quality = transcript_quality_check(
            "Our daily production team includes Andy Corbin, Maria Hollenhorst, Sarah Leeson, "
            "Sean McHenry and Sophia Terenzia. Will Story is the supervising senior for June 30."
        )

        self.assertEqual(quality["quality"], "noisy")
        self.assertIn("production_credits", quality["signals"])

    def test_transcript_quality_check_marks_outro_language_as_noisy(self):
        quality = transcript_quality_check(
            "Thanks for listening. Subscribe wherever you get your podcasts and join us next episode."
        )

        self.assertEqual(quality["quality"], "noisy")
        self.assertIn("outro_language", quality["signals"])

    def test_transcript_quality_check_marks_weak_external_media_as_mixed(self):
        quality = transcript_quality_check(WEAK_EXTERNAL_MEDIA_TRANSCRIPT)

        self.assertEqual(quality["quality"], "mixed")
        self.assertIn("transition_opening", quality["signals"])
        self.assertIn("external_media", quality["signals"])

    def test_service_downgrades_noisy_voice_transcript_to_saved_note_without_next_step(self):
        repo = FakeRepository()
        note_calls = []

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            note_calls.append(allow_next_step)
            return {
                "title": "Trends in American Socialization Time",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The average time Americans spend socializing in person has decreased.",
                "next_step": "Consider discussing the implications of these social trends in upcoming curriculum materials.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-28T10:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=NOISY_PODCAST_TRANSCRIPT,
            context_hint="",
            input_type="voice",
        )

        artifact = record.result["saved_note_artifact"]
        self.assertEqual(record.result["route_kind"], "saved_note")
        self.assertEqual(artifact["state"], "needs_direction")
        self.assertEqual(note_calls, [])
        self.assertIn("audio", artifact["framing_line"].lower())
        self.assertIn("review", artifact["sections"][0]["text"].lower())
        self.assertNotIn("Next step", [section["label"] for section in artifact["sections"]])

    def test_service_downgrades_weak_external_media_voice_transcript_to_saved_review_note(self):
        repo = FakeRepository()
        note_calls = []

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            note_calls.append(allow_next_step)
            return {
                "title": "Evaluating the Value of Extended Experiences",
                "framing_line": (
                    "This note captures reflections on the significance of time and value in "
                    "experiential settings, which can inform future lesson planning."
                ),
                "summary": (
                    "Extended experiences can feel shorter than they are, indicating a high level "
                    "of engagement and enjoyment."
                ),
                "next_step": "Consider how to structure lessons that maximize student engagement.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-29T11:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=WEAK_EXTERNAL_MEDIA_TRANSCRIPT,
            context_hint="",
            input_type="voice",
        )

        artifact = record.result["saved_note_artifact"]
        self.assertEqual(record.result["route_kind"], "saved_note")
        self.assertEqual(note_calls, [])
        self.assertEqual(artifact["status"], "Saved for review")
        self.assertNotIn("okay", artifact["title"].lower())
        self.assertNotIn("this next category", artifact["title"].lower())
        self.assertIn("tasting menu", artifact["title"].lower())
        self.assertEqual(
            artifact["source_excerpt"],
            "A food reviewer says the tasting menu is expensive, but the pacing and variety make the meal feel worth it.",
        )
        self.assertIn("review", artifact["sections"][0]["text"].lower())
        self.assertNotIn("Next step", [section["label"] for section in artifact["sections"]])

    def test_service_preserves_mixed_voice_transcript_but_suppresses_next_step(self):
        repo = FakeRepository()
        note_calls = []

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            note_calls.append(allow_next_step)
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "Jordan thinks the result needs to feel more like a saved object.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-28T10:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan today about the workflows page for Memnon. "
                "The result still feels too generic and not enough like a saved object worth revisiting. "
                "Our production team includes Andy Corbin and Maria Hollenhorst."
            ),
            context_hint="product review",
            input_type="voice",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(record.result["route_kind"], "direct_professional_note")
        self.assertEqual(note_calls, [False])
        self.assertEqual([section["label"] for section in artifact["sections"]], [])
        self.assertIn("mixed audio", artifact["framing_line"].lower())
        self.assertIn("saved object", artifact["summary"].lower())

    def test_clean_voice_transcript_with_explicit_action_keeps_next_step(self):
        repo = FakeRepository()
        note_calls = []

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            note_calls.append(allow_next_step)
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "Jordan wants the result card to feel more durable and grounded.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-28T10:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan about the workflows page for Memnon. "
                "Action: revise the result card before the next demo."
            ),
            context_hint="product review",
            input_type="voice",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(note_calls, [True])
        self.assertEqual([section["label"] for section in artifact["sections"]], ["Next step"])
        self.assertIn("Revise the result card", artifact["sections"][0]["text"])

    def test_clean_voice_transcript_without_action_signal_does_not_invent_next_step(self):
        repo = FakeRepository()
        note_calls = []

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            note_calls.append(allow_next_step)
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "summary": "Jordan wants the result card to feel more durable and grounded.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-28T10:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan about the workflows page for Memnon. "
                "The result still feels too generic and not enough like a saved object worth revisiting."
            ),
            context_hint="product review",
            input_type="voice",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(note_calls, [False])
        self.assertEqual([section["label"] for section in artifact["sections"]], [])
        self.assertNotIn("Next step", artifact["body"])
        self.assertIn("worth revisiting", artifact["framing_line"].lower())
        self.assertNotIn("practical artifact", artifact["framing_line"].lower())

    def test_service_neutralizes_teacher_profile_for_unrelated_voice_note(self):
        repo = FakeRepository()
        captured_profiles = []

        def biased_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            captured_profiles.append(profile.copy())
            if profile.get("profession") == "teacher":
                return {
                    "title": "Evaluating the Value of Extended Experiences",
                    "framing_line": (
                        "This note captures reflections on the significance of time and value in "
                        "experiential settings, which can inform future lesson planning."
                    ),
                    "summary": (
                        "Extended experiences can feel shorter than they are, indicating a high level "
                        "of engagement and enjoyment."
                    ),
                    "next_step": "Consider how to structure lessons that maximize student engagement.",
                }
            return {
                "title": "Restaurant review on tasting menu pacing",
                "framing_line": "A saved note shaped around one grounded takeaway.",
                "summary": (
                    "The reviewer argues that a long tasting menu feels worthwhile when each course stays memorable."
                ),
                "next_step": "",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=biased_ai,
            now_provider=lambda: "2026-06-29T11:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "The reviewer says the tasting menu is expensive, but the pacing and variety make the meal feel worth it. "
                "By the time dessert arrives, the evening feels shorter than it really was because the experience stays engaging."
            ),
            context_hint="",
            input_type="voice",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(captured_profiles[0]["profession"], "professional")
        self.assertIn("restaurant review", artifact["title"].lower())
        self.assertNotIn("lesson", artifact["framing_line"].lower())
        self.assertNotIn("student", artifact["summary"].lower())

    def test_service_preserves_teacher_profile_for_teaching_note(self):
        repo = FakeRepository()
        captured_profiles = []

        def profile_echo_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            captured_profiles.append(profile.copy())
            return {
                "title": "AP Computer Science curriculum planning",
                "framing_line": "A saved teaching note with one concrete next step.",
                "summary": "The first unit needs a tighter pacing plan for students who are new to programming.",
                "next_step": "Revise the first unit before August planning week.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=profile_echo_ai,
            now_provider=lambda: "2026-06-29T11:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        service.create_text_capture(
            uid="user-1",
            source_text=(
                "I want to revisit lesson planning for AP Computer Science. "
                "My students need a clearer first unit and the curriculum pacing is still too loose. "
                "Action: revise the first unit before August planning week."
            ),
            context_hint="",
            input_type="voice",
        )

        self.assertEqual(captured_profiles[0]["profession"], "teacher")

    def test_service_skips_low_signal_lead_sentence_in_source_excerpt(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            return {
                "title": "Restaurant review on tasting menu pacing",
                "framing_line": "A saved note shaped around one grounded takeaway.",
                "summary": "The reviewer argues the tasting menu feels worth the time when each course stays memorable.",
                "next_step": "",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-29T11:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Four, five, six. The reviewer says the tasting menu is expensive, "
                "but the pacing and variety make the meal feel worth it."
            ),
            context_hint="",
            input_type="voice",
        )

        self.assertEqual(
            record.result["primary_artifact"]["source_excerpt"],
            "The reviewer says the tasting menu is expensive, but the pacing and variety make the meal feel worth it.",
        )

    def test_service_skips_transition_opening_for_voice_source_excerpt(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "A saved note shaped around one concrete next step.",
                "summary": "The result needs a stronger title and one clear next step.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-29T11:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Okay, so here's the thing. "
                "Met with Jordan about the workflows page for Memnon. "
                "The result needs a stronger title and one clear next step. "
                "Action: revise the result card before the next demo."
            ),
            context_hint="",
            input_type="voice",
        )

        self.assertEqual(
            record.result["primary_artifact"]["source_excerpt"],
            "The result needs a stronger title and one clear next step.",
        )

    def test_voice_note_rewrites_action_heavy_title(self):
        # Summary no longer rejects action-describing proposed text (that was a
        # single-sentence key_point constraint to avoid duplicating next_step;
        # keeping them distinct is now the prompt's job -- see ai.py). Title
        # rewriting away from action-heavy raw text is unchanged.
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            return {
                "title": "Revision Needed for Workflows Page Result Card Ahead of Next Demo",
                "framing_line": "This note captures key takeaways from a meeting regarding necessary updates to the workflows page.",
                "summary": "Jordan flagged that the workflows page result card needs a stronger title.",
                "next_step": "Revise the result card before the next demo.",
            }

        service = WorkflowService(
            repository=repo,
            note_generator=fake_ai,
            now_provider=lambda: "2026-06-29T11:00:00-06:00",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan about the workflows page. "
                "The result needs a stronger title and one clear next step. "
                "Action: revise the result card before the next demo."
            ),
            context_hint="",
            input_type="voice",
        )

        artifact = record.result["primary_artifact"]
        self.assertEqual(
            artifact["title"],
            "Workflows Page conversation with Jordan",
        )
        self.assertEqual(
            artifact["summary"],
            "Jordan flagged that the workflows page result card needs a stronger title.",
        )
        self.assertEqual(
            artifact["sections"][0]["text"],
            "Revise the result card before the next demo",
        )


if __name__ == "__main__":
    unittest.main()
