"""Spoken task overview (today_overview) at the top of the daily brief.

The overview is read-only and must disappear silently, never erroring and
never apologising aloud, when tasks are disconnected, no list is chosen, or
the Google Tasks API fails.
"""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from tests.test_daily_feed_slice import FakeDB, FakeDoc, FakeUserRef, load_main_module


NOW_UTC = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)  # Monday
TODAY = date(2026, 11, 9)

OPEN_TASKS = [
    {"id": "a", "title": "Email Dana the grant budget", "due": "2026-11-09T00:00:00.000Z", "status": "needsAction", "updated": "2026-11-01T10:00:00.000Z"},
    {"id": "b", "title": "Renew car registration", "due": "2026-11-05T00:00:00.000Z", "status": "needsAction", "updated": "2026-10-20T10:00:00.000Z"},
    {"id": "c", "title": "Draft Willow case study", "due": "2026-11-12T00:00:00.000Z", "status": "needsAction", "updated": "2026-11-06T10:00:00.000Z"},
    {"id": "d", "title": "Reorganise garage shelves", "status": "needsAction", "updated": "2026-11-07T10:00:00.000Z"},
    {"id": "e", "title": "Book dentist", "status": "needsAction", "updated": "2026-10-01T10:00:00.000Z"},
    {"id": "f", "title": "   ", "status": "needsAction", "updated": "2026-11-08T10:00:00.000Z"},
    {"id": "g", "title": "--", "status": "needsAction", "updated": "2026-11-08T10:00:00.000Z"},
    {"id": "h", "title": "Old finished thing", "status": "completed", "updated": "2026-11-08T10:00:00.000Z"},
]

MODEL_RESULT_WITH_OVERVIEW = {
    "title": "November 9 — First things",
    "description": "desc",
    "time_anchor": "Today is Monday, November 9th.",
    "continuity_anchor": "Protect the morning",
    "tasks_used": ["T1", "T2", "T3", "T9"],
    "task_drop_suggested": "T4",
    "segments": {
        "opening": "Today is Monday, November 9th.",
        "today_overview": "Two things are already pressing: the registration is overdue and Dana needs the budget today.",
        "practical_briefing": "Protect the morning.",
        "calendar_today": "",
        "reflective_grounding": "This still matters today.",
        "meditative_close": "One next step is enough.",
    },
}


class FakeTasksService:
    def __init__(self, items=None, error=None):
        self.items = items if items is not None else OPEN_TASKS
        self.error = error
        self.list_calls = []
        self.write_calls = []

    def tasks(self):
        return self

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return self

    def execute(self):
        if self.error:
            raise self.error
        return {"items": list(self.items)}

    def insert(self, **kwargs):
        self.write_calls.append(("insert", kwargs))
        return self

    def patch(self, **kwargs):
        self.write_calls.append(("patch", kwargs))
        return self

    def delete(self, **kwargs):
        self.write_calls.append(("delete", kwargs))
        return self


class EpisodeRef:
    def __init__(self):
        self.saved = {}

    def get(self):
        if self.saved:
            return FakeDoc(self.saved, True, "2026-11-09")
        return FakeDoc({}, False, "2026-11-09")

    def set(self, payload, merge=False):
        self.saved.update(payload)


CONNECTED_USER = {
    "email": "eitanfire@gmail.com",
    "preferred_name": "Eitan",
    "spoken_name": "Eitan",
    "reflection_style": "complete",
    "timezone": "America/Denver",
    "google_tasks_connected": True,
    "google_tasks_list_id": "list-personal",
}


class TodayOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()

    def _generate(self, user_data, service, model_result=None, summarize_error=None, now_utc=NOW_UTC):
        """Run the real episode builder with storage, audio and the model faked.
        Returns (episode, prompts sent to the model)."""
        user_ref = FakeUserRef(dict(user_data))
        prompts = []

        def fake_summarize(prompt, *_args, **_kwargs):
            prompts.append(prompt)
            if summarize_error:
                raise summarize_error
            result = model_result if model_result is not None else MODEL_RESULT_WITH_OVERVIEW
            return {**result, "segments": dict(result["segments"])}

        with (
            patch.dict(self.main.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_daily_feed_episode_ref", return_value=EpisodeRef()),
            patch.object(self.main, "_load_recent_feed_notes", return_value=[{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}]),
            patch.object(self.main, "_daily_feed_has_recent_reflection", return_value=True),
            patch.object(self.main, "daily_feed_passages", return_value=[]),
            patch.object(self.main, "_upload_daily_feed_audio", return_value="daily-feed/user123/2026-11-09.mp3"),
            patch.object(self.main, "synthesize_daily_brief_bytes", return_value=(b"audio", {"used_music_beds": False})) as synth,
            patch.object(self.main, "_summarize", side_effect=fake_summarize),
            patch.object(self.main, "load_weather_context", return_value=(None, {}, {"available": False}), create=True),
            patch.object(self.main, "_tasks_service_for_user", return_value=service),
            patch.object(self.main, "_log_usage_event"),
        ):
            episode = self.main._build_daily_feed_episode("user123", user_ref.data, now_utc=now_utc, force=True)
        episode["_professional_text"] = synth.call_args.kwargs["professional_text"]
        return episode, prompts

    def assertOverviewOmitted(self, episode, prompts, reason):
        self.assertNotIn("today_overview", episode["segments_used"])
        self.assertNotIn("today_overview", episode["script_segments"])
        self.assertNotIn("OPEN PERSONAL TASKS", prompts[0])
        self.assertNotIn("today_overview", prompts[0])
        self.assertNotIn("registration", episode["script_text"])
        self.assertNotIn("tasks", episode["context_sources_used"])
        meta = episode["generation_meta"]
        self.assertEqual(meta["tasks_used"], [])
        self.assertIsNone(meta["task_drop_suggested"])
        self.assertEqual(meta["tasks_overview"], {"offered": 0, "applied": False, "omission_reason": reason})
        # Still a full, normal episode.
        self.assertEqual(episode["episode_type"], "standard")
        self.assertEqual(episode["segments_used"][0], "opening")
        self.assertIn("practical_briefing", episode["segments_used"])

    # ── Omit paths ───────────────────────────────────────────────────────────

    def test_overview_omitted_when_tasks_disconnected(self):
        service = FakeTasksService()
        user = {**CONNECTED_USER, "google_tasks_connected": False}
        episode, prompts = self._generate(user, service)
        self.assertOverviewOmitted(episode, prompts, "not_connected")
        self.assertEqual(service.list_calls, [])

    def test_overview_omitted_when_no_list_chosen(self):
        service = FakeTasksService()
        user = {**CONNECTED_USER, "google_tasks_list_id": "  "}
        episode, prompts = self._generate(user, service)
        self.assertOverviewOmitted(episode, prompts, "no_list")
        self.assertEqual(service.list_calls, [])

    def test_overview_omitted_when_tasks_api_errors(self):
        service = FakeTasksService(error=RuntimeError("HttpError 503 backendError"))
        episode, prompts = self._generate(CONNECTED_USER, service)
        self.assertOverviewOmitted(episode, prompts, "api_error")
        self.assertEqual(len(service.list_calls), 1)
        self.assertNotIn("couldn", episode["script_text"].lower())

    def test_overview_omitted_when_list_has_no_open_tasks(self):
        service = FakeTasksService(items=[OPEN_TASKS[5], OPEN_TASKS[7]])
        episode, prompts = self._generate(CONNECTED_USER, service)
        self.assertOverviewOmitted(episode, prompts, "no_open_tasks")

    def test_fallback_omits_overview_silently_when_tasks_api_errors(self):
        service = FakeTasksService(error=RuntimeError("HttpError 503"))
        episode, _ = self._generate(CONNECTED_USER, service, summarize_error=RuntimeError("model down"))
        self.assertEqual(episode["episode_type"], "fallback")
        self.assertNotIn("today_overview", episode["segments_used"])
        self.assertEqual(episode["generation_meta"]["tasks_overview"]["omission_reason"], "api_error")
        self.assertNotIn("task", episode["script_text"].lower())

    # ── Weekends: no to-do reminders on days off ─────────────────────────────

    def assertWeekendEpisodeWithoutOverview(self, now_utc, expected_date_key):
        service = FakeTasksService()
        episode, prompts = self._generate(CONNECTED_USER, service, now_utc=now_utc)
        self.assertEqual(episode["date_key"], expected_date_key)
        self.assertOverviewOmitted(episode, prompts, "weekend")
        # Not fetched at all, so nothing can be recorded as used.
        self.assertEqual(service.list_calls, [])
        # The rest of the episode is unchanged.
        self.assertEqual(
            episode["segments_used"],
            ["opening", "practical_briefing", "reflective_grounding", "meditative_close"],
        )

    def test_overview_omitted_on_saturday_without_fetching_tasks(self):
        # 18:00 UTC is 11:00 Saturday in Denver.
        self.assertWeekendEpisodeWithoutOverview(datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc), "2026-11-07")

    def test_overview_omitted_on_sunday_without_fetching_tasks(self):
        # 03:00 UTC Monday is still 20:00 Sunday in Denver: the local date decides.
        self.assertWeekendEpisodeWithoutOverview(datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc), "2026-11-08")

    def test_overview_spoken_on_friday_evening_even_when_utc_is_saturday(self):
        # 03:00 UTC Saturday is 20:00 Friday in Denver.
        service = FakeTasksService()
        episode, _ = self._generate(
            CONNECTED_USER, service, now_utc=datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(episode["date_key"], "2026-11-06")
        self.assertEqual(episode["segments_used"][:2], ["opening", "today_overview"])
        self.assertEqual(len(service.list_calls), 1)
        self.assertTrue(episode["generation_meta"]["tasks_overview"]["applied"])
        self.assertEqual(len(episode["generation_meta"]["tasks_used"]), 3)

    # ── Overview present ─────────────────────────────────────────────────────

    def test_overview_follows_opening_and_records_tasks_used(self):
        service = FakeTasksService()
        episode, prompts = self._generate(CONNECTED_USER, service)

        self.assertEqual(episode["segments_used"][:3], ["opening", "today_overview", "practical_briefing"])
        self.assertTrue(episode["_professional_text"].startswith("Today is Monday, November 9th.\n\nTwo things"))
        self.assertIn("tasks", episode["context_sources_used"])
        prompt = prompts[0]
        self.assertIn("[T1] Renew car registration (it is overdue)", prompt)
        self.assertIn("[T2] Email Dana the grant budget (it is due today)", prompt)
        self.assertIn("exactly one task to drop or delegate", prompt)
        self.assertIn("Never read the task list out", prompt)

        meta = episode["generation_meta"]
        # T9 was never offered, so it is not recorded.
        self.assertEqual(meta["tasks_used"], [
            {"id": "b", "title": "Renew car registration"},
            {"id": "a", "title": "Email Dana the grant budget"},
            {"id": "c", "title": "Draft Willow case study"},
        ])
        self.assertEqual(meta["task_drop_suggested"], {"id": "d", "title": "Reorganise garage shelves"})
        self.assertEqual(meta["tasks_overview"], {"offered": 5, "applied": True, "omission_reason": ""})
        self.assertEqual(service.list_calls[0]["tasklist"], "list-personal")
        self.assertFalse(service.list_calls[0]["showCompleted"])
        self.assertEqual(service.write_calls, [])

    def test_model_leaving_overview_empty_records_nothing(self):
        result = {**MODEL_RESULT_WITH_OVERVIEW, "segments": {**MODEL_RESULT_WITH_OVERVIEW["segments"], "today_overview": ""}}
        episode, _ = self._generate(CONNECTED_USER, FakeTasksService(), model_result=result)
        self.assertNotIn("today_overview", episode["segments_used"])
        self.assertEqual(episode["generation_meta"]["tasks_used"], [])
        self.assertIsNone(episode["generation_meta"]["task_drop_suggested"])
        self.assertEqual(episode["generation_meta"]["tasks_overview"]["omission_reason"], "not_spoken")

    def test_fallback_speaks_overview_with_one_drop(self):
        episode, _ = self._generate(CONNECTED_USER, FakeTasksService(), summarize_error=RuntimeError("model down"))
        self.assertEqual(episode["episode_type"], "fallback")
        self.assertEqual(episode["segments_used"][:2], ["opening", "today_overview"])
        overview = episode["script_segments"]["today_overview"]
        self.assertIn("Start with Renew car registration, because it is overdue.", overview)
        self.assertEqual(overview.count("set down or hand off"), 1)
        meta = episode["generation_meta"]
        self.assertEqual([t["id"] for t in meta["tasks_used"]], ["b", "a", "c"])
        self.assertEqual(meta["task_drop_suggested"], {"id": "e", "title": "Book dentist"})

    # ── Ranking ──────────────────────────────────────────────────────────────

    def test_rank_puts_due_and_overdue_first_then_week_then_recently_updated(self):
        ranked = self.main._rank_overview_tasks(OPEN_TASKS, TODAY)
        self.assertEqual([t["id"] for t in ranked], ["b", "a", "c", "d", "e"])

    def test_rank_caps_candidates_at_eight(self):
        items = [
            {"id": str(i), "title": f"Task {i}", "status": "needsAction", "updated": f"2026-11-0{i % 9 + 1}T00:00:00Z"}
            for i in range(20)
        ]
        self.assertEqual(len(self.main._rank_overview_tasks(items, TODAY)), 8)

    def test_prompt_has_no_overview_without_tasks(self):
        prompt = self.main._build_daily_feed_prompt(
            CONNECTED_USER, [{"title": "t", "summary": "s"}], "standard", NOW_UTC, overview_tasks=[]
        )
        self.assertNotIn("today_overview", prompt)
        self.assertNotIn("tasks_used", prompt)


if __name__ == "__main__":
    unittest.main()
