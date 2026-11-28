"""Daily brief: no repeats, no stale threads, spoken names, and the context pack.

Failures heard in November 2026: the same Rambam quote nearly every day, the
same old thread (and title) leading day after day, English names over the
names the traditions use, quotations set apart instead of woven, and nothing
from the local vault. These tests pin the fixes.
"""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_daily_feed_slice import FUNCTIONS_DIR, FakeDB, FakeDoc, FakeUserRef, load_main_module


NOW_UTC = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)  # Friday, 5 AM Denver

MODEL_RESULT = {
    "title": "November 27 — Room to breathe",
    "description": "desc",
    "time_anchor": "Today is Friday, November 27th.",
    "continuity_anchor": "The consent forms",
    "lead_note": "N1",
    "anchor_source": "recent_note",
    "context_pack_used": False,
    "voices_used": [],
    "segments": {
        "opening": "Today is Friday, November 27th.",
        "practical_briefing": "The consent forms are the thing.",
        "reflective_grounding": "You have been carrying this all week.",
        "meditative_close": "One next step is enough.",
    },
}

RECENT_NOTE = {
    "_id": "note-recent",
    "title": "Presidential AI Challenge forms",
    "summary": "Collect the consent forms.",
    "insight": "The consent forms",
    "created_at": "2026-11-24T22:38:40Z",
}


class EpisodeRef:
    def __init__(self):
        self.saved = {}

    def get(self):
        if self.saved:
            return FakeDoc(self.saved, True, "2026-11-27")
        return FakeDoc({}, False, "2026-11-27")

    def set(self, payload, merge=False):
        self.saved.update(payload)


USER = {
    "email": "eitanfire@gmail.com",
    "preferred_name": "Eitan",
    "daily_feed_timezone": "America/Denver",
    "reflect_config": {"selected_guides": ["maimonides"]},
}


class BriefContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()

    def _generate_unpatched_pack(self):
        """Like _generate, but leaves _load_context_pack to the caller's patch."""
        original = self._pack_patch_enabled
        self._pack_patch_enabled = False
        try:
            return self._generate()
        finally:
            self._pack_patch_enabled = original

    _pack_patch_enabled = True

    def _generate(
        self,
        user_data=None,
        notes=None,
        model_result=None,
        prior_episodes=None,
        context_pack=(None, "not_built"),
        summarize_error=None,
        patch_passages=None,
    ):
        user_ref = FakeUserRef(dict(user_data or USER))
        prompts = []

        def fake_summarize(prompt, *_args, **_kwargs):
            prompts.append(prompt)
            if summarize_error:
                raise summarize_error
            result = model_result if model_result is not None else MODEL_RESULT
            return {**result, "segments": dict(result["segments"])}

        patches = [
            patch.dict(self.main.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_daily_feed_episode_ref", return_value=EpisodeRef()),
            patch.object(self.main, "_load_recent_feed_notes", return_value=[RECENT_NOTE] if notes is None else notes),
            patch.object(self.main, "_load_prior_daily_feed_episodes", return_value=prior_episodes or []),
            patch.object(self.main, "_load_overview_tasks", return_value=([], "not_connected")),
            patch.object(self.main, "_load_calendar_events", return_value=([], "not_connected")),
            patch.object(self.main, "_upload_daily_feed_audio", return_value="daily-feed/user123/2026-11-27.mp3"),
            patch.object(self.main, "synthesize_daily_brief_bytes", return_value=(b"audio", {"used_music_beds": False})),
            patch.object(self.main, "_summarize", side_effect=fake_summarize),
            patch.object(self.main, "load_weather_context", return_value=(None, {}, {"available": False}), create=True),
            patch.object(self.main, "_log_usage_event"),
        ]
        if self._pack_patch_enabled:
            patches.append(patch.object(self.main, "_load_context_pack", return_value=context_pack))
        if patch_passages is not None:
            patches.append(patch.object(self.main, "daily_feed_passages", return_value=patch_passages))
        for p in patches:
            p.start()
        try:
            episode = self.main._build_daily_feed_episode("user123", user_ref.data, now_utc=NOW_UTC, force=True)
        finally:
            for p in reversed(patches):
                p.stop()
        return episode, prompts, user_ref

    # ── (a) Rotation memory ──────────────────────────────────────────────────

    def test_passage_used_yesterday_is_not_offered_today(self):
        """Fails on the code before this change: the primary-voice guarantee
        offered the Rambam's one passage every single day."""
        user = {
            **USER,
            "daily_feed_voice_history": [
                {"source_id": "mai_1", "guide_id": "maimonides", "date": "2026-11-26"},
            ],
        }
        episode, prompts, _ = self._generate(user_data=user)
        self.assertNotIn("[mai_1]", prompts[0])
        self.assertNotIn("mai_1", [p["source_id"] for p in episode["guide_passages_offered"]])
        # Its only voice is spent, so nothing is offered, and that is fine.
        self.assertNotIn("WISDOM VOICES", prompts[0])
        self.assertEqual(episode["episode_type"], "standard")

    def test_passage_is_offered_again_after_thirty_days(self):
        user = {
            **USER,
            "daily_feed_voice_history": [
                {"source_id": "mai_1", "guide_id": "maimonides", "date": "2026-10-27"},
            ],
        }
        _, prompts, _ = self._generate(user_data=user)
        self.assertIn("[mai_1]", prompts[0])

    def test_primary_voice_guarantee_yields_to_another_voice_when_spent(self):
        selected = self.main.daily_feed_passages(
            {"reflect_config": {"selected_guides": ["maimonides", "pirkei_avot"]}},
            "responsibility",
            exclude_source_ids={"mai_1"},
        )
        self.assertTrue(selected)
        self.assertNotIn("mai_1", [p["source_id"] for p in selected])
        self.assertTrue(all(p["guide_id"] == "pirkei_avot" for p in selected))

    def test_voices_used_are_written_to_history(self):
        user = {**USER, "daily_feed_voice_history": [
            {"source_id": "pa_2_4", "guide_id": "pirkei_avot", "date": "2026-11-20"},
            {"source_id": "lt_8", "guide_id": "lao_tzu", "date": "2026-08-01"},  # past 60 days: dropped
        ]}
        model = {**MODEL_RESULT, "voices_used": ["mai_1"]}
        episode, _, user_ref = self._generate(user_data=user, model_result=model)
        self.assertEqual(episode["guide_voices_used"][0]["source_ids"], ["mai_1"])
        history = user_ref.data["daily_feed_voice_history"]
        self.assertIn({"source_id": "mai_1", "guide_id": "maimonides", "date": "2026-11-27"}, history)
        self.assertIn({"source_id": "pa_2_4", "guide_id": "pirkei_avot", "date": "2026-11-20"}, history)
        self.assertNotIn("lt_8", [h["source_id"] for h in history])

    def test_a_voice_named_in_the_script_is_recorded_even_if_the_model_omits_it(self):
        model = {**MODEL_RESULT, "voices_used": [], "segments": {
            **MODEL_RESULT["segments"],
            "reflective_grounding": "As the Rambam teaches, one act can tip the balance.",
        }}
        episode, _, user_ref = self._generate(model_result=model)
        self.assertEqual(episode["guide_voices_used"][0]["source_ids"], ["mai_1"])
        self.assertEqual(episode["generation_meta"]["voices_detected_by_name"], ["mai_1"])
        self.assertIn("mai_1", [h["source_id"] for h in user_ref.data["daily_feed_voice_history"]])

    # ── (b) Spoken names, (c) weave, (f) library fixes ───────────────────────

    def test_spoken_name_appears_in_the_prompt(self):
        _, prompts, _ = self._generate()
        self.assertIn("[mai_1] the Rambam (Maimonides; Mishneh Torah, Hilchot Teshuvah 3:4)", prompts[0])
        self.assertIn('as the Rambam teaches', prompts[0])
        self.assertIn("No standalone quotations", prompts[0])
        self.assertNotIn("quote or closely paraphrase", prompts[0])

    def test_every_guide_has_a_spoken_name_and_the_misattributed_passages_are_gone(self):
        for path in (FUNCTIONS_DIR / "sources.json", FUNCTIONS_DIR.parent / "data" / "sources.json",
                     FUNCTIONS_DIR.parent / "public" / "sources.json"):
            guides = json.loads(Path(path).read_text())["guides"]
            self.assertTrue(all(g.get("spoken_name") for g in guides.values()), path)
            self.assertEqual(guides["maimonides"]["spoken_name"], "the Rambam")
            self.assertEqual(guides["rumi"]["spoken_name"], "Mawlana")
            self.assertEqual(guides["thich_nhat_hanh"]["spoken_name"], "Thầy")
            self.assertEqual(guides["lao_tzu"]["spoken_name"], "Laozi")
            ids = {p["id"] for g in guides.values() for p in g["passages"]}
            self.assertFalse(ids & {"mai_2", "jd_1", "jd_3", "bh_3"}, path)
            self.assertEqual(guides["maimonides"]["passages"][0]["ref"], "Hilchot Teshuvah 3:4")

    def test_fallback_weaves_the_voice_by_spoken_name_without_a_quotation(self):
        episode, _, _ = self._generate(summarize_error=RuntimeError("model down"))
        grounding = episode["script_segments"]["reflective_grounding"]
        self.assertEqual(episode["episode_type"], "fallback")
        self.assertIn("the Rambam", grounding)
        self.assertNotIn("Maimonides", grounding)
        self.assertNotIn("“", grounding)
        self.assertNotIn("offers a way to hold this", grounding)

    # ── (d) Staleness ────────────────────────────────────────────────────────

    def test_forty_day_old_note_is_not_offered(self):
        old = {"_id": "note-chicken", "title": "Banana the chicken", "summary": "The family chicken.",
               "insight": "Banana", "created_at": "2026-10-18T15:00:00Z"}
        episode, prompts, _ = self._generate(notes=[RECENT_NOTE, old])
        self.assertNotIn("Banana", prompts[0])
        self.assertEqual(episode["generation_meta"]["notes_dropped_stale"], 1)

    def test_only_stale_notes_means_no_reflection_history(self):
        old = {"_id": "note-chicken", "title": "Banana the chicken", "summary": "The family chicken.",
               "created_at": "2026-10-18T15:00:00Z"}
        episode, prompts, _ = self._generate(notes=[old])
        self.assertNotIn("Banana", prompts[0])
        self.assertNotIn("reflection_history", episode["context_sources_used"])

    def test_past_events_are_over(self):
        _, prompts, _ = self._generate()
        self.assertIn("A dated event in them that falls before today is over", prompts[0])

    def test_thread_that_led_twice_this_week_is_background_only(self):
        prior = [
            {"date_key": "2026-11-26", "title": "November 26 — Forms", "generation_meta": {"lead_thread_key": "doc:note-recent"}},
            {"date_key": "2026-11-24", "title": "November 24 — Forms again", "generation_meta": {"lead_thread_key": "doc:note-recent"}},
        ]
        episode, prompts, _ = self._generate(prior_episodes=prior)
        self.assertIn("Background only: this thread already led the brief 2 times", prompts[0])
        self.assertIn("Continuity anchor candidate: none", prompts[0])
        meta = episode["generation_meta"]
        self.assertEqual(meta["threads_capped"], ["doc:note-recent"])
        self.assertTrue(meta["lead_thread_over_cap"])  # the model ignored the rule: recorded, not hidden

    def test_thread_that_led_once_is_not_capped(self):
        prior = [{"date_key": "2026-11-26", "generation_meta": {"lead_thread_key": "doc:note-recent"}}]
        episode, prompts, _ = self._generate(prior_episodes=prior)
        self.assertNotIn("Background only", prompts[0])
        self.assertEqual(episode["generation_meta"]["lead_thread_key"], "doc:note-recent")

    # ── (e) Point of view ────────────────────────────────────────────────────

    def test_prompt_speaks_in_second_person_about_the_listeners_own_life(self):
        _, prompts, _ = self._generate()
        self.assertIn("Speak about them in the second person", prompts[0])
        self.assertIn("Never retell them as a third party's story", prompts[0])

    # ── Title and anchor repeats ─────────────────────────────────────────────

    def test_no_title_repeat_on_consecutive_days(self):
        prior = [{
            "date_key": "2026-11-26",
            "title": "November 26 — Embracing Change",
            "script_meta": {"continuity_anchor": "Banana the chicken"},
            "generation_meta": {},
        }]
        model = {**MODEL_RESULT, "title": "November 27 — Embracing change!"}
        episode, prompts, _ = self._generate(prior_episodes=prior, model_result=model)
        self.assertIn('The previous brief was titled "November 26 — Embracing Change"', prompts[0])
        self.assertIn('continuity anchor was "Banana the chicken"', prompts[0])
        self.assertEqual(episode["title"], "November 27 — The consent forms")
        meta = episode["generation_meta"]
        self.assertTrue(meta["title_repeat_prevented"])
        self.assertEqual(meta["prior_title"], "November 26 — Embracing Change")

    def test_fallback_title_does_not_repeat_either(self):
        prior = [{"date_key": "2026-11-26", "title": "November 26 — Presidential AI Challenge forms", "generation_meta": {}}]
        episode, _, _ = self._generate(prior_episodes=prior, summarize_error=RuntimeError("model down"))
        self.assertEqual(episode["episode_type"], "fallback")
        self.assertNotEqual(
            self.main._daily_feed_title_theme(episode["title"]),
            self.main._daily_feed_title_theme(prior[0]["title"]),
        )

    def test_a_new_title_is_left_alone(self):
        prior = [{"date_key": "2026-11-26", "title": "November 26 — Embracing Change", "generation_meta": {}}]
        episode, _, _ = self._generate(prior_episodes=prior)
        self.assertEqual(episode["title"], "November 27 — Room to breathe")
        self.assertFalse(episode["generation_meta"]["title_repeat_prevented"])

    # ── Context pack ─────────────────────────────────────────────────────────

    PACK = {
        "markdown": "# Context pack\n\n## People and household\n- Haley: the listener's wife\n\n## Therapy: cross-session analysis\nBelief vs. action.",
        "generated_at": "2026-11-27T10:00:00+00:00",
        "source_ids": ["people_household", "therapy_index"],
    }

    def test_context_pack_absent_brief_still_generates(self):
        episode, prompts, _ = self._generate(context_pack=(None, "not_built"))
        self.assertEqual(episode["episode_type"], "standard")
        self.assertNotIn("CONTEXT PACK", prompts[0])
        self.assertNotIn("context_pack", episode["context_sources_used"])
        self.assertEqual(
            episode["generation_meta"]["context_pack"],
            {"offered": False, "applied": False, "claimed_by_model": False, "terms_detected": [],
             "omission_reason": "not_built", "generated_at": "", "sources": []},
        )

    def test_context_pack_read_error_is_silent(self):
        with patch.object(self.main, "_load_context_pack", side_effect=RuntimeError("permission denied")):
            episode, _, _ = self._generate_unpatched_pack()
        self.assertEqual(episode["episode_type"], "standard")
        self.assertEqual(episode["generation_meta"]["context_pack"]["omission_reason"], "read_error")

    def test_context_pack_offered_with_its_rules(self):
        model = {**MODEL_RESULT, "context_pack_used": True, "anchor_source": "context_pack_therapy"}
        episode, prompts, _ = self._generate(context_pack=(self.PACK, ""), model_result=model)
        prompt = prompts[0]
        self.assertIn("CONTEXT PACK: THE LISTENER'S OWN SECOND BRAIN", prompt)
        self.assertIn("Haley: the listener's wife", prompt)
        self.assertIn("Never quote session notes or the therapist verbatim", prompt)
        self.assertIn("Recent reflections lead", prompt)
        self.assertNotIn("Therapy material anchored yesterday's brief", prompt)
        self.assertIn("context_pack", episode["context_sources_used"])
        meta = episode["generation_meta"]
        self.assertEqual(meta["context_pack"]["applied"], True)
        self.assertEqual(meta["context_pack"]["omission_reason"], "")
        self.assertEqual(meta["anchor_source"], "context_pack_therapy")

    def test_therapy_may_not_anchor_two_days_running(self):
        prior = [{"date_key": "2026-11-26", "title": "November 26 — Belief and action",
                  "generation_meta": {"anchor_source": "context_pack_therapy"}}]
        model = {**MODEL_RESULT, "context_pack_used": True, "anchor_source": "context_pack_therapy"}
        episode, prompts, _ = self._generate(context_pack=(self.PACK, ""), prior_episodes=prior, model_result=model)
        self.assertIn("Therapy material anchored yesterday's brief", prompts[0])
        self.assertTrue(episode["generation_meta"]["therapy_anchor_repeated"])

    def test_context_pack_offered_but_unused(self):
        episode, _, _ = self._generate(context_pack=(self.PACK, ""))
        self.assertNotIn("context_pack", episode["context_sources_used"])
        self.assertEqual(episode["generation_meta"]["context_pack"]["omission_reason"], "not_used")

    def test_context_pack_use_is_detected_when_the_model_under_reports(self):
        model = {**MODEL_RESULT, "context_pack_used": False, "segments": {
            **MODEL_RESULT["segments"],
            "practical_briefing": "The consent forms are the thing, and Haley has the evening.",
        }}
        episode, _, _ = self._generate(context_pack=(self.PACK, ""), model_result=model)
        meta = episode["generation_meta"]["context_pack"]
        self.assertTrue(meta["applied"])
        self.assertFalse(meta["claimed_by_model"])
        self.assertEqual(meta["terms_detected"], ["Haley"])
        self.assertIn("context_pack", episode["context_sources_used"])

    def test_names_also_in_the_reflections_do_not_count_as_pack_use(self):
        notes = [{**RECENT_NOTE, "summary": "Haley picks up Bayla."}]
        model = {**MODEL_RESULT, "segments": {**MODEL_RESULT["segments"], "practical_briefing": "Haley has pickup."}}
        episode, _, _ = self._generate(context_pack=(self.PACK, ""), model_result=model, notes=notes)
        self.assertFalse(episode["generation_meta"]["context_pack"]["applied"])

    def test_load_context_pack_rejects_a_stale_pack(self):
        class Coll:
            def __init__(self, payload):
                self.payload = payload

            def document(self, _name):
                return self

            def get(self):
                return FakeDoc(self.payload, self.payload is not None, "current")

        for payload, expected in (
            (None, (None, "not_built")),
            ({"markdown": ""}, (None, "empty")),
            ({"markdown": "x", "generated_at": datetime(2026, 11, 20, tzinfo=timezone.utc)}, (None, "stale")),
        ):
            user_ref = FakeUserRef({})
            user_ref.subcollections["context_pack"] = Coll(payload)
            with patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)):
                self.assertEqual(self.main._load_context_pack("user123", NOW_UTC), expected)

        user_ref = FakeUserRef({})
        user_ref.subcollections["context_pack"] = Coll({
            "markdown": "# pack", "generated_at": datetime(2026, 11, 27, 10, tzinfo=timezone.utc),
            "sources": [{"id": "recent_notes", "omitted": ""}, {"id": "people_household", "omitted": "empty"}],
        })
        with patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)):
            pack, reason = self.main._load_context_pack("user123", NOW_UTC)
        self.assertEqual(reason, "")
        self.assertEqual(pack["markdown"], "# pack")
        self.assertEqual(pack["source_ids"], ["recent_notes"])


if __name__ == "__main__":
    unittest.main()
