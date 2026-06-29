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

    def touch_context_activity(self, uid, context_id, now):
        context = self.contexts[(uid, context_id)]
        context["last_activity_at"] = now
        context["updated_at"] = now
        return context


class WorkflowServiceTests(unittest.TestCase):
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
                "key_point": "The result card still feels too generic.",
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

    def test_service_can_record_voice_capture_metadata(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "key_point": "The result needs to feel more like a saved object than a generated response.",
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

    def test_service_creates_one_professional_note_record(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "key_point": "The system feels too generic because the result does not yet feel like a saved object.",
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
            ["Key point", "Next step"],
        )
        self.assertIn("system feels too generic", record.result["primary_artifact"]["sections"][0]["text"])
        self.assertIn("Revise the result card", record.result["primary_artifact"]["sections"][1]["text"])
        self.assertIn("Met with Jordan today about the product direction.", record.result["primary_artifact"]["source_excerpt"])
        self.assertIn("product direction", record.result["primary_artifact"]["framing_line"].lower())
        self.assertEqual(record.result["secondary_artifacts"], [])
        self.assertEqual(record.source_event["profile_snapshot"]["lane"], "professional")

    def test_service_fetches_saved_capture(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Teacher workflow note",
                "framing_line": "Pulled from your product reflection.",
                "key_point": "Start by clarifying the single change that reduces user friction first.",
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
                "key_point": "Start by clarifying the single change that reduces user friction first.",
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

    def test_service_distinguishes_weak_saved_notes_from_ambiguous_ones(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Unused",
                "framing_line": "Unused",
                "key_point": "Unused",
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
                "key_point": "The note already points toward one useful direction and is worth shaping into a concrete next step.",
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
            artifact["sections"][0]["text"].lower(),
        )
        self.assertIn("saved artifact", artifact["sections"][1]["text"].lower())

    def test_service_preserves_actual_ambiguity_for_saved_notes(self):
        repo = FakeRepository()

        service = WorkflowService(
            repository=repo,
            note_generator=lambda *_args: {
                "title": "Unused",
                "framing_line": "Unused",
                "key_point": "Unused",
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

    def test_service_shapes_document_like_paste_into_primary_artifact(self):
        repo = FakeRepository()

        def generic_ai(source_text, context_hint, profile, api_key):
            return {
                "title": "Professional note",
                "framing_line": "Shaped from your note into one practical artifact to review.",
                "key_point": "The note already points toward one useful direction and is worth shaping into a concrete next step.",
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
        self.assertIn("fellowship thread", artifact["sections"][0]["text"].lower())
        self.assertIn("agendas", artifact["sections"][0]["text"].lower())
        self.assertIn("feedback", artifact["sections"][0]["text"].lower())
        self.assertNotIn("looks like source material", artifact["sections"][0]["text"].lower())
        self.assertIn("consolidate", artifact["sections"][1]["text"].lower())
        self.assertNotIn("toggle this calendar", artifact["sections"][1]["text"].lower())
        self.assertIn("fellowship", artifact["framing_line"].lower())
        self.assertIn("reference", artifact["framing_line"].lower())

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
                "key_point": "The average time Americans spend socializing in person has decreased.",
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
                "key_point": (
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
                "key_point": "Jordan thinks the result needs to feel more like a saved object.",
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
        self.assertEqual([section["label"] for section in artifact["sections"]], ["Key point"])
        self.assertIn("mixed audio", artifact["framing_line"].lower())
        self.assertIn("saved object", artifact["sections"][0]["text"].lower())

    def test_clean_voice_transcript_with_explicit_action_keeps_next_step(self):
        repo = FakeRepository()
        note_calls = []

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            note_calls.append(allow_next_step)
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "key_point": "Jordan wants the result card to feel more durable and grounded.",
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
        self.assertEqual([section["label"] for section in artifact["sections"]], ["Key point", "Next step"])
        self.assertIn("Revise the result card", artifact["sections"][1]["text"])

    def test_clean_voice_transcript_without_action_signal_does_not_invent_next_step(self):
        repo = FakeRepository()
        note_calls = []

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            note_calls.append(allow_next_step)
            return {
                "title": "Product direction conversation with Jordan",
                "framing_line": "Shaped from your note into one practical artifact.",
                "key_point": "Jordan wants the result card to feel more durable and grounded.",
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
        self.assertEqual([section["label"] for section in artifact["sections"]], ["Key point"])
        self.assertNotIn("Next step", artifact["body"])
        self.assertIn("grounded takeaway", artifact["framing_line"].lower())

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
                    "key_point": (
                        "Extended experiences can feel shorter than they are, indicating a high level "
                        "of engagement and enjoyment."
                    ),
                    "next_step": "Consider how to structure lessons that maximize student engagement.",
                }
            return {
                "title": "Restaurant review on tasting menu pacing",
                "framing_line": "A saved note shaped around one grounded takeaway.",
                "key_point": (
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
        self.assertNotIn("student", artifact["sections"][0]["text"].lower())

    def test_service_preserves_teacher_profile_for_teaching_note(self):
        repo = FakeRepository()
        captured_profiles = []

        def profile_echo_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            captured_profiles.append(profile.copy())
            return {
                "title": "AP Computer Science curriculum planning",
                "framing_line": "A saved teaching note with one concrete next step.",
                "key_point": "The first unit needs a tighter pacing plan for students who are new to programming.",
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
                "key_point": "The reviewer argues the tasting menu feels worth the time when each course stays memorable.",
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
                "key_point": "The result needs a stronger title and one clear next step.",
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
            "Met with Jordan about the workflows page for Memnon.",
        )

    def test_voice_note_rewrites_action_heavy_title_and_key_point(self):
        repo = FakeRepository()

        def fake_ai(source_text, context_hint, profile, api_key, allow_next_step=True):
            return {
                "title": "Revision Needed for Workflows Page Result Card Ahead of Next Demo",
                "framing_line": "This note captures key takeaways from a meeting regarding necessary updates to the workflows page.",
                "key_point": "Revise the result card before the next demo to ensure it aligns with expectations.",
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
            artifact["sections"][0]["text"],
            "The result needs a stronger title and one clear next step",
        )
        self.assertEqual(
            artifact["sections"][1]["text"],
            "Revise the result card before the next demo",
        )


if __name__ == "__main__":
    unittest.main()
