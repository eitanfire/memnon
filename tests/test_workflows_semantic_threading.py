import sys
import unittest
from pathlib import Path


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.service import (
    SEMANTIC_MAX_BOOST,
    SEMANTIC_SIMILARITY_FLOOR,
    WorkflowService,
    _cosine_similarity,
    _semantic_boost,
)
from tests.test_workflows_service import FakeRepository


# A capture whose wording shares no token with either thread title. Lexical
# scoring cannot reach it; only the embedding can.
PARAPHRASE_CAPTURE = (
    "Met with Priya about the tense retrospective. Action: schedule one-on-ones this week."
)
NOTE_TITLE = "Retro tension follow-up"


def _stub_embedder(calls: list[str] | None = None):
    """Deterministic stand-in for Hugging Face feature extraction.

    Three orthogonal concept axes. "Tense retrospective" language lands almost
    exactly on the same axis as "friction", which is the paraphrase relationship
    a real sentence embedding would capture and token overlap never can.
    """

    def embed(text: str) -> list[float]:
        if calls is not None:
            calls.append(text)
        lowered = (text or "").lower()
        if "friction" in lowered:
            return [1.0, 0.0, 0.0]
        if "deployment" in lowered or "pipeline" in lowered:
            return [0.0, 1.0, 0.0]
        if "tense" in lowered or "retrospective" in lowered or "tension" in lowered:
            return [0.98, 0.02, 0.0]
        return [0.0, 0.0, 1.0]

    return embed


def _build_service(embedding_provider=None, repo=None):
    return WorkflowService(
        repository=repo if repo is not None else FakeRepository(),
        note_generator=lambda *_args, **_kwargs: {
            "title": NOTE_TITLE,
            "framing_line": "A saved note shaped around one concrete next step.",
            "key_point": "The retrospective stalled.",
            "next_step": "Schedule one-on-ones.",
        },
        now_provider=lambda: "2026-06-29T12:00:00Z",
        api_key_provider=lambda: "test-key",
        embedding_provider=embedding_provider,
    )


def _seed_threads_and_capture(service):
    service.create_context("user-1", title="Team friction", summary="")
    service.create_context("user-1", title="Deployment pipeline", summary="")
    return service.create_text_capture(
        uid="user-1",
        source_text=PARAPHRASE_CAPTURE,
        context_hint="",
    )


class SemanticBoostMathTests(unittest.TestCase):
    def test_similarity_at_or_below_the_floor_contributes_nothing(self):
        self.assertEqual(_semantic_boost(0.0), 0)
        self.assertEqual(_semantic_boost(SEMANTIC_SIMILARITY_FLOOR), 0)

    def test_boost_is_capped_so_it_cannot_outrank_an_explicit_title_match(self):
        self.assertEqual(_semantic_boost(1.0), SEMANTIC_MAX_BOOST)
        # An exact context-hint title match is worth 5; semantics must stay under it.
        self.assertLess(SEMANTIC_MAX_BOOST, 5)

    def test_boost_rises_with_similarity(self):
        scores = [_semantic_boost(value) for value in (0.6, 0.75, 0.9, 1.0)]
        self.assertEqual(scores, sorted(scores))

    def test_cosine_handles_degenerate_vectors_without_raising(self):
        self.assertEqual(_cosine_similarity([], [1.0]), 0.0)
        self.assertEqual(_cosine_similarity([1.0, 0.0], [1.0]), 0.0)
        self.assertEqual(_cosine_similarity([0.0, 0.0], [1.0, 0.0]), 0.0)
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)


class SemanticThreadSuggestionTests(unittest.TestCase):
    def test_paraphrased_capture_matches_its_thread(self):
        service = _build_service(embedding_provider=_stub_embedder())
        capture = _seed_threads_and_capture(service)

        suggested = service.suggest_context_for_capture("user-1", capture.to_dict())

        self.assertIsNotNone(suggested)
        self.assertEqual(suggested["suggested_context_title"], "Team friction")

    def test_same_capture_finds_nothing_without_an_embedding_provider(self):
        """The lexical baseline -- this is what the feature actually changes."""
        service = _build_service(embedding_provider=None)
        capture = _seed_threads_and_capture(service)

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_capture_is_embedded_once_per_suggestion_not_once_per_thread(self):
        calls: list[str] = []
        service = _build_service(embedding_provider=_stub_embedder(calls))
        capture = _seed_threads_and_capture(service)
        calls.clear()  # drop the per-thread embeddings written at creation

        service.suggest_context_for_capture("user-1", capture.to_dict())

        self.assertEqual(len(calls), 1, f"expected one capture embedding, got {calls}")

    def test_threads_are_embedded_once_at_creation(self):
        calls: list[str] = []
        service = _build_service(embedding_provider=_stub_embedder(calls))

        context = service.create_context("user-1", title="Team friction", summary="")

        self.assertEqual(calls, ["Team friction"])
        self.assertEqual(context["embedding_v1"], [1.0, 0.0, 0.0])
        self.assertEqual(context["embedding_dim"], 3)


class SemanticDegradationTests(unittest.TestCase):
    """Embedding is an enhancement; every failure must fall back, never raise."""

    def test_embedding_failure_falls_back_to_lexical_scoring(self):
        def exploding_embedder(_text):
            raise RuntimeError("hugging face is down")

        service = _build_service(embedding_provider=exploding_embedder)
        capture = _seed_threads_and_capture(service)

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_empty_vector_from_cooldown_falls_back_to_lexical_scoring(self):
        service = _build_service(embedding_provider=lambda _text: [])
        capture = _seed_threads_and_capture(service)

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))

    def test_lexical_match_still_wins_when_embeddings_are_unavailable(self):
        """A thread the lexical signals can reach is unaffected by a dead provider."""
        service = _build_service(embedding_provider=lambda _text: [])
        service.create_context("user-1", title="Workflows UI/UX", summary="")
        service.create_context("user-1", title="Voice capture", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="workflows ui/ux",
        )

        suggested = service.suggest_context_for_capture("user-1", capture.to_dict())

        self.assertEqual(suggested["suggested_context_title"], "Workflows UI/UX")

    def test_mismatched_embedding_dimensions_score_zero_instead_of_raising(self):
        service = _build_service(embedding_provider=_stub_embedder())
        thread = {"context_id": "ctx-1", "embedding_v1": [1.0, 0.0]}

        self.assertEqual(service._semantic_match_boost([1.0, 0.0, 0.0], thread), 0)

    def test_thread_without_an_embedding_scores_zero(self):
        service = _build_service(embedding_provider=_stub_embedder())

        self.assertEqual(service._semantic_match_boost([1.0, 0.0, 0.0], {"context_id": "ctx-1"}), 0)


class ThreadSuggestionTelemetryTests(unittest.TestCase):
    """Every reason a suggestion does or does not appear must be recorded.

    Production shows 104 captures against 1 thread with 3 confirmed
    assignments, and no way to tell whether suggestions are never shown, shown
    and ignored, or not understood. Those are three different problems.
    """

    def _service_with_log(self, repo=None):
        events = []
        service = WorkflowService(
            repository=repo if repo is not None else FakeRepository(),
            note_generator=lambda *_a, **_k: {
                "title": NOTE_TITLE,
                "framing_line": "A saved note shaped around one concrete next step.",
                "key_point": "The retrospective stalled.",
                "next_step": "Schedule one-on-ones.",
            },
            now_provider=lambda: "2026-06-29T12:00:00Z",
            api_key_provider=lambda: "test-key",
            usage_logger=lambda uid, name, meta: events.append((name, meta)),
        )
        return service, events

    def _names(self, events):
        return [name for name, _meta in events]

    def _meta(self, events, name):
        return next(meta for event_name, meta in events if event_name == name)

    def test_no_threads_is_recorded_as_its_own_reason(self):
        service, events = self._service_with_log()
        capture = service.create_text_capture(
            uid="user-1", source_text=PARAPHRASE_CAPTURE, context_hint=""
        )
        events.clear()

        service.suggest_context_for_capture("user-1", capture.to_dict())

        self.assertIn("thread_suggestion_withheld", self._names(events))
        self.assertEqual(self._meta(events, "thread_suggestion_withheld")["reason"], "no_active_threads")

    def test_building_index_is_not_reported_as_no_threads(self):
        # The two are indistinguishable to the caller -- both yield [] -- but
        # they mean opposite things: one is a product problem, one is transient.
        from google.api_core.exceptions import FailedPrecondition

        class IndexBuilding(FakeRepository):
            def list_active_contexts(self, uid, limit=12):
                raise FailedPrecondition("index is currently building")

        service, events = self._service_with_log(repo=IndexBuilding())
        capture = service.create_text_capture(
            uid="user-1", source_text=PARAPHRASE_CAPTURE, context_hint=""
        )
        events.clear()

        service.suggest_context_for_capture("user-1", capture.to_dict())

        self.assertEqual(
            self._meta(events, "thread_suggestion_withheld")["reason"],
            "context_index_unavailable",
        )

    def test_below_threshold_records_the_score_it_fell_short_of(self):
        service, events = self._service_with_log()
        service.create_context("user-1", title="Deployment pipeline", summary="")
        capture = service.create_text_capture(
            uid="user-1", source_text=PARAPHRASE_CAPTURE, context_hint=""
        )
        events.clear()

        service.suggest_context_for_capture("user-1", capture.to_dict())

        meta = self._meta(events, "thread_suggestion_withheld")
        self.assertEqual(meta["reason"], "below_threshold")
        self.assertEqual(meta["candidate_count"], 1)
        self.assertIn("best_score", meta)
        self.assertEqual(meta["threshold"], WorkflowService.SUGGESTION_MIN_SCORE)
        self.assertFalse(meta["semantic_enabled"])

    def test_a_shown_suggestion_is_recorded_with_its_margin(self):
        service, events = self._service_with_log()
        service.create_context("user-1", title="Workflows UI/UX", summary="")
        service.create_context("user-1", title="Voice capture", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="workflows ui/ux",
        )
        events.clear()

        suggested = service.suggest_context_for_capture("user-1", capture.to_dict())

        self.assertIsNotNone(suggested)
        meta = self._meta(events, "thread_suggestion_shown")
        self.assertEqual(meta["candidate_count"], 2)
        self.assertIn("best_score", meta)
        self.assertIn("runner_up_score", meta)

    def test_decision_records_whether_the_suggestion_was_followed(self):
        service, events = self._service_with_log()
        context = service.create_context("user-1", title="Workflows UI/UX", summary="")
        service.create_context("user-1", title="Voice capture", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="workflows ui/ux",
        )
        events.clear()

        service.apply_context_decision(
            "user-1", capture.capture_id, action="confirmed", context_id=context["context_id"]
        )

        meta = self._meta(events, "thread_decision")
        self.assertEqual(meta["action"], "confirmed")
        self.assertTrue(meta["had_suggestion"])
        self.assertTrue(meta["followed_suggestion"])

    def test_kept_separate_records_a_declined_suggestion(self):
        service, events = self._service_with_log()
        service.create_context("user-1", title="Workflows UI/UX", summary="")
        service.create_context("user-1", title="Voice capture", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="workflows ui/ux",
        )
        events.clear()

        service.apply_context_decision("user-1", capture.capture_id, action="kept_separate")

        meta = self._meta(events, "thread_decision")
        self.assertEqual(meta["action"], "kept_separate")
        self.assertTrue(meta["had_suggestion"])
        self.assertFalse(meta["followed_suggestion"])

    def test_telemetry_never_breaks_the_request_that_produced_it(self):
        service, _events = self._service_with_log()

        def exploding_logger(*_args, **_kwargs):
            raise RuntimeError("usage_events write failed")

        service.usage_logger = exploding_logger
        service.create_context("user-1", title="Workflows UI/UX", summary="")
        capture = service.create_text_capture(
            uid="user-1",
            source_text="Met with Jordan about the workflows page. Action: revise the result card.",
            context_hint="workflows ui/ux",
        )

        # Must not raise.
        service.suggest_context_for_capture("user-1", capture.to_dict())

    def test_no_logger_injected_is_silent_and_safe(self):
        service = WorkflowService(
            repository=FakeRepository(),
            note_generator=lambda *_a, **_k: {"title": NOTE_TITLE, "framing_line": "", "key_point": "", "next_step": ""},
            now_provider=lambda: "2026-06-29T12:00:00Z",
            api_key_provider=lambda: "test-key",
        )
        capture = service.create_text_capture(
            uid="user-1", source_text=PARAPHRASE_CAPTURE, context_hint=""
        )

        self.assertIsNone(service.suggest_context_for_capture("user-1", capture.to_dict()))


if __name__ == "__main__":
    unittest.main()
