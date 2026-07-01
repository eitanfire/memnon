import sys
import unittest
import json
from pathlib import Path
from unittest.mock import patch


FUNCTIONS_DIR = Path(__file__).resolve().parents[1] / "functions"
if str(FUNCTIONS_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_DIR))

from workflows.service import WorkflowService
from workflows.ai import generate_professional_note
from workflows.local_app import _local_note_generator
from workflows.service import derive_artifact_next_step
from workflows.service import _source_supports_education_context
from workflows.service import _source_supports_next_step


MEETING_DEBRIEF = (
    "Met with Jordan about the workflows page for Memnon. "
    "The result still feels too generic and not enough like a saved object worth revisiting. "
    "Action: revise the result card before the next demo."
)

PRODUCT_IDEA = (
    "I want Memnon to feel competitive with capture-first apps without turning into a dashboard. "
    "The key is one useful saved result, not more workflow options."
)

FOLLOW_UP_NOTE = (
    "Send Jordan the revised result card draft by Thursday and ask whether the title feels specific enough."
)

VOICE_REFLECTION = (
    "I keep noticing that the strong part of Memnon is restraint. "
    "If the input is weak, it should save it honestly instead of pretending it knows what to do."
)

PRINCIPLE_NOTE = "Memnon should save weak input honestly instead of pretending it knows what to do."


class FakeRepository:
    def __init__(self):
        self.records = {}
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


def build_service():
    return WorkflowService(
        repository=FakeRepository(),
        note_generator=lambda *_args, **_kwargs: {
            "title": "Professional note",
            "framing_line": "Shaped from your note into one practical artifact.",
            "key_point": "The note already points toward one useful direction and is worth shaping into a concrete next step.",
            "next_step": "Revise the result card before the next demo.",
        },
        now_provider=lambda: "2026-06-27T16:00:00Z",
        api_key_provider=lambda: "test-key",
    )


class WorkflowResultQualityTests(unittest.TestCase):
    def test_non_teacher_capture_scrubs_teacher_coded_generated_output(self):
        service = WorkflowService(
            repository=FakeRepository(),
            note_generator=lambda *_args, **_kwargs: {
                "title": "Teacher Planning Note",
                "framing_line": "A teacher note worth keeping for future lesson planning.",
                "key_point": "This should help with classroom planning next week.",
                "next_step": "Revise the result card before the next demo.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture("user-1", MEETING_DEBRIEF, "")
        artifact = record.result["primary_artifact"]

        self.assertNotEqual(artifact["title"], "Teacher Planning Note")
        self.assertNotIn("teacher", artifact["framing_line"].lower())
        self.assertNotIn("lesson", artifact["framing_line"].lower())
        self.assertNotIn("classroom", artifact["sections"][0]["text"].lower())
        self.assertTrue("workflows" in artifact["sections"][0]["text"].lower() or "result" in artifact["sections"][0]["text"].lower())

    def test_generated_output_does_not_default_to_professional_labeling(self):
        expected_guard = "if the source is not teacher-specific, do not inject teacher framing from the saved profile"
        expected_framing_rule = (
            "framing_line should describe why this saved object is worth keeping, not just that it is professional"
        )
        expected_saved_object_rule = "framing_line should read like a saved object worth reopening, not an executive summary"
        expected_generic_phrase_rule = (
            'avoid phrases like "professional note worth shaping", "important for ensuring", "crucial to ensure", '
            '"competitive landscape", or "clear direction for positioning"'
        )

        class _FakeResponse:
            def __init__(self, content: str):
                self._content = content

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": self._content}}]}).encode("utf-8")

        def fake_urlopen(request, timeout=45):
            del timeout
            payload = json.loads(request.data.decode("utf-8"))
            prompt = payload["messages"][1]["content"]
            if (
                expected_guard in prompt
                and expected_framing_rule in prompt
                and expected_saved_object_rule in prompt
                and expected_generic_phrase_rule in prompt
            ):
                content = json.dumps(
                    {
                        "title": "Saved note",
                        "framing_line": "Shaped from your note into one saved result worth reopening.",
                        "key_point": "The key is one useful saved result, not more workflow options.",
                        "next_step": "",
                    }
                )
            else:
                content = json.dumps(
                    {
                        "title": "Saved note",
                        "framing_line": "A teacher note worth keeping for future lesson planning.",
                        "key_point": "The key is one useful saved result, not more workflow options.",
                        "next_step": "",
                    }
                )
            return _FakeResponse(content)

        with patch("workflows.ai.urllib.request.urlopen", side_effect=fake_urlopen):
            generated = generate_professional_note(
                PRODUCT_IDEA,
                "",
                {"lane": "professional", "profession": "teacher"},
                "test-key",
                allow_next_step=False,
            )

        self.assertNotIn("teacher", generated.get("framing_line", "").lower())
        self.assertIn("worth reopening", generated.get("framing_line", "").lower())

    def test_local_fallback_generator_omits_next_step_for_non_action_note(self):
        generated = _local_note_generator(
            "Jordan thinks the result should feel more like a saved object worth revisiting.",
            "",
            {"lane": "professional", "profession": "teacher"},
            "local-dev",
            allow_next_step=True,
        )

        self.assertEqual(generated["next_step"], "")

    def test_specific_model_next_step_survives_when_grounded_in_source(self):
        next_step = derive_artifact_next_step(
            MEETING_DEBRIEF,
            "",
            "Revise the workflows result card before the next demo.",
        )

        self.assertEqual(next_step, "Revise the workflows result card before the next demo")

    def test_meeting_debrief_produces_specific_title_grounded_excerpt_and_next_step(self):
        service = build_service()

        record = service.create_text_capture("user-1", MEETING_DEBRIEF, "")
        artifact = record.result["primary_artifact"]
        self.assertTrue("Jordan" in artifact["title"] or "Workflows" in artifact["title"])
        self.assertTrue(artifact["source_excerpt"].startswith("Met with Jordan"))
        self.assertEqual([section["label"] for section in artifact["sections"]], ["Key point", "Next step"])
        self.assertNotIn(artifact["title"].lower(), {"professional note", "suggested note", "saved note"})
        self.assertNotIn("professional note worth shaping", record.result["interpretation_line"].lower())
        self.assertNotIn("professional note worth shaping", artifact["framing_line"].lower())
        self.assertNotIn("crucial to ensure", artifact["framing_line"].lower())

    def test_product_idea_can_be_worth_reopening_without_forced_action(self):
        service = build_service()

        record = service.create_text_capture("user-1", PRODUCT_IDEA, "")
        artifact = record.result["primary_artifact"]
        self.assertNotEqual(artifact["title"], "Professional note")
        self.assertTrue(artifact["source_excerpt"])
        self.assertNotIn("teacher", artifact["framing_line"].lower())
        self.assertNotEqual(
            artifact["sections"][0]["text"].lower().rstrip("."),
            PRODUCT_IDEA.split(".")[0].lower().rstrip("."),
        )
        self.assertNotIn("competitive with capture-first apps", artifact["framing_line"].lower())

    def test_follow_up_note_prefers_action_shaped_next_step(self):
        service = build_service()

        record = service.create_text_capture("user-1", FOLLOW_UP_NOTE, "")
        artifact = record.result["primary_artifact"]
        self.assertEqual(artifact["sections"][-1]["label"], "Next step")
        self.assertTrue("Jordan" in artifact["sections"][-1]["text"] or "Thursday" in artifact["sections"][-1]["text"])

    def test_non_teacher_follow_up_scrubs_teacher_coded_generated_next_step(self):
        service = WorkflowService(
            repository=FakeRepository(),
            note_generator=lambda *_args, **_kwargs: {
                "title": "Saved note",
                "framing_line": "A saved note shaped around one concrete next step.",
                "key_point": "Jordan needs a clearer read on whether the result title is specific enough.",
                "next_step": "Send Jordan the revised lesson plan by Thursday.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture("user-1", FOLLOW_UP_NOTE, "")
        artifact = record.result["primary_artifact"]

        self.assertEqual(artifact["sections"][-1]["label"], "Next step")
        self.assertIn("result card", artifact["sections"][-1]["text"].lower())
        self.assertNotIn("lesson", artifact["sections"][-1]["text"].lower())

    def test_education_context_matching_ignores_embedded_class_substrings(self):
        self.assertFalse(
            _source_supports_education_context(
                "World-class polish matters here because the title classification still feels too generic.",
                "",
            )
        )

    def test_education_context_matching_ignores_principal_engineer_phrase(self):
        self.assertFalse(
            _source_supports_education_context(
                "Talked with the principal engineer about the workflows ranking change.",
                "",
            )
        )

    def test_voice_reflection_stays_grounded_without_inventing_external_action(self):
        service = build_service()

        record = service.create_text_capture("user-1", VOICE_REFLECTION, "", input_type="voice")
        artifact = record.result["primary_artifact"] or record.result["saved_note_artifact"]
        self.assertTrue(artifact["source_excerpt"])

    def test_non_action_capture_can_omit_next_step(self):
        service = build_service()

        record = service.create_text_capture("user-1", PRODUCT_IDEA, "")
        artifact = record.result["primary_artifact"]
        labels = [section["label"] for section in artifact["sections"]]
        self.assertEqual(labels, ["Key point"])

    def test_principle_style_should_statement_does_not_surface_next_step(self):
        service = WorkflowService(
            repository=FakeRepository(),
            note_generator=lambda *_args, **_kwargs: {
                "title": "Saved note",
                "framing_line": "A saved note shaped around one concrete next step.",
                "key_point": "Memnon should save weak input honestly instead of pretending it knows what to do.",
                "next_step": "Save weak input honestly instead of pretending it knows what to do.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture("user-1", PRINCIPLE_NOTE, "")
        artifact = record.result["primary_artifact"]

        self.assertEqual([section["label"] for section in artifact["sections"]], ["Key point"])
        self.assertNotIn("Next step", artifact["body"])
        self.assertNotIn("professional note worth shaping", record.result["interpretation_line"].lower())
        self.assertNotIn("crucial adjustment", artifact["framing_line"].lower())

    def test_review_of_noun_phrase_does_not_support_next_step(self):
        source_text = "Review of the workflows page with Jordan. The note still feels generic."
        self.assertFalse(_source_supports_next_step(source_text, "", input_type="text"))

        service = WorkflowService(
            repository=FakeRepository(),
            note_generator=lambda *_args, **_kwargs: {
                "title": "Workflows page review",
                "framing_line": "A saved conversation note with one concrete follow-up to carry forward.",
                "key_point": "The note still feels generic.",
                "next_step": "Review the workflows page with Jordan.",
            },
            now_provider=lambda: "2026-06-27T16:00:00Z",
            api_key_provider=lambda: "test-key",
        )

        record = service.create_text_capture("user-1", source_text, "")
        artifact = record.result["primary_artifact"]

        self.assertEqual([section["label"] for section in artifact["sections"]], ["Key point"])
        self.assertNotIn("Next step", artifact["body"])
