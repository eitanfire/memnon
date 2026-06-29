import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.service import WorkflowService
from tests.test_workflows_service import FakeRepository


class WorkflowThreadingTests(unittest.TestCase):
    def test_explicit_context_hint_beats_other_threads(self):
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

        self.assertEqual(suggested["suggested_context_title"], "Workflows UI/UX")

    def test_no_suggestion_when_no_active_threads_exist(self):
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

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_no_suggestion_for_weak_saved_note(self):
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

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_no_suggestion_for_ambiguous_saved_note(self):
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
            source_text=(
                "Not sure what this should become. Something about the product direction I think. "
                "Could be a note to myself, a follow-up, or maybe just something to hold onto."
            ),
            context_hint="workflows ui/ux",
        )

        self.assertEqual(
            capture.result["saved_note_artifact"]["state"],
            "needs_direction",
        )
        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_no_suggestion_when_evidence_score_stays_below_threshold(self):
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
        service.create_context("user-1", title="Hiring pipeline", summary="")
        service.create_context("user-1", title="Family travel", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Met with Jordan about the workflows page. "
                "Action: revise the result card before the next demo."
            ),
            context_hint="",
        )

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_no_suggestion_when_two_threads_are_close_runner_ups(self):
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
        service.create_context("user-1", title="Jordan design feedback", summary="")
        service.create_context("user-1", title="Jordan workflows review", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about workflows feedback for the result card.",
            context_hint="",
        )

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_no_suggestion_for_noisy_voice_result_even_with_matching_words(self):
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

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_prior_confirmed_pattern_raises_existing_thread_priority(self):
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
        service.create_context("user-1", title="Voice capture", summary="")
        prior = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="",
        )
        service.apply_context_decision(
            "user-1",
            prior.capture_id,
            action="confirmed",
            context_id=target["context_id"],
        )
        current = service.create_text_capture(
            uid="user-1",
            source_text=(
                "Jordan thinks the workflows page still feels too generic. "
                "Action: revise the result card before the next review."
            ),
            context_hint="",
        )

        suggested = service.suggest_context_for_capture("user-1", current.to_dict())

        self.assertEqual(suggested["suggested_context_title"], "Workflows UI/UX")


if __name__ == "__main__":
    unittest.main()
