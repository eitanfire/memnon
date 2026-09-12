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


if __name__ == "__main__":
    unittest.main()
