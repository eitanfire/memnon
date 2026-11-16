"""Spoken calendar walk-through (calendar_today) in the daily brief.

Same contract as the task overview: read-only, and the segment disappears
silently, never erroring and never apologising aloud, when the calendar is
disconnected, the API fails, or there is nothing on today.
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.test_daily_feed_slice import FakeDB, FakeDoc, FakeUserRef, load_main_module


NOW_UTC = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)  # Monday, 5 AM Denver
DENVER = ZoneInfo("America/Denver")

CALENDARS = [
    {"id": "primary-id", "summary": "Eitan", "primary": True, "selected": True},
    {"id": "school-id", "summary": "School", "selected": True},
    {"id": "hidden-id", "summary": "Holidays", "selected": False},
]

EVENTS_BY_CALENDAR = {
    "primary-id": [
        {"id": "e1", "summary": "Staff meeting", "status": "confirmed",
         "start": {"dateTime": "2026-11-09T09:00:00-07:00"}, "end": {"dateTime": "2026-11-09T09:45:00-07:00"},
         "location": "Room 214"},
        {"id": "e2", "summary": "Dentist", "status": "confirmed",
         "start": {"dateTime": "2026-11-09T13:30:00-07:00"}, "end": {"dateTime": "2026-11-09T14:30:00-07:00"}},
        {"id": "e3", "summary": "Declined thing", "status": "confirmed",
         "start": {"dateTime": "2026-11-09T15:00:00-07:00"}, "end": {"dateTime": "2026-11-09T16:00:00-07:00"},
         "attendees": [{"self": True, "responseStatus": "declined"}]},
        {"id": "e4", "summary": "Cancelled thing", "status": "cancelled",
         "start": {"dateTime": "2026-11-09T16:00:00-07:00"}, "end": {"dateTime": "2026-11-09T17:00:00-07:00"}},
        {"id": "e5", "summary": "Conference week", "status": "confirmed",
         "start": {"date": "2026-11-08"}, "end": {"date": "2026-11-11"}},
        {"id": "e6", "summary": "Tomorrow only", "status": "confirmed",
         "start": {"dateTime": "2026-11-10T09:00:00-07:00"}, "end": {"dateTime": "2026-11-10T10:00:00-07:00"}},
    ],
    "school-id": [
        # Same meeting shared on a second calendar: spoken once.
        {"id": "e7", "summary": "Staff meeting", "status": "confirmed",
         "start": {"dateTime": "2026-11-09T09:00:00-07:00"}, "end": {"dateTime": "2026-11-09T09:45:00-07:00"}},
        {"id": "e8", "summary": "Parent call", "status": "confirmed",
         "start": {"dateTime": "2026-11-09T11:00:00-07:00"}, "end": {"dateTime": "2026-11-09T11:15:00-07:00"}},
    ],
    "hidden-id": [
        {"id": "e9", "summary": "Hidden calendar event", "status": "confirmed",
         "start": {"dateTime": "2026-11-09T10:00:00-07:00"}, "end": {"dateTime": "2026-11-09T10:30:00-07:00"}},
    ],
}

MODEL_RESULT = {
    "title": "November 9 — A full morning",
    "description": "desc",
    "time_anchor": "Today is Monday, November 9th.",
    "continuity_anchor": "Protect the morning",
    "segments": {
        "opening": "Today is Monday, November 9th.",
        "practical_briefing": "Protect the morning.",
        "calendar_today": "The staff meeting at 9 AM comes first, then a parent call at 11, then the dentist at 1:30.",
        "reflective_grounding": "This still matters today.",
        "meditative_close": "One next step is enough.",
    },
}


class FakeCalendarService:
    def __init__(self, calendars=None, events=None, error=None):
        self.calendars = calendars if calendars is not None else CALENDARS
        self.events_by_calendar = events if events is not None else EVENTS_BY_CALENDAR
        self.error = error
        self.event_calls = []
        self._pending = None

    def calendarList(self):
        self._pending = ("calendars", {})
        return self

    def events(self):
        return self

    def list(self, **kwargs):
        if self._pending and self._pending[0] == "calendars":
            self._pending = ("calendars", kwargs)
        else:
            self._pending = ("events", kwargs)
            self.event_calls.append(kwargs)
        return self

    def execute(self):
        if self.error:
            raise self.error
        kind, kwargs = self._pending
        self._pending = None
        if kind == "calendars":
            return {"items": list(self.calendars)}
        return {"items": list(self.events_by_calendar.get(kwargs["calendarId"], []))}


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
    "daily_feed_timezone": "America/Denver",
    "google_calendar_connected": True,
}


class CalendarTodayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()

    def _generate(self, user_data, service, model_result=None, summarize_error=None, now_utc=NOW_UTC, notes=None):
        user_ref = FakeUserRef(dict(user_data))
        prompts = []

        def fake_summarize(prompt, *_args, **_kwargs):
            prompts.append(prompt)
            if summarize_error:
                raise summarize_error
            result = model_result if model_result is not None else MODEL_RESULT
            return {**result, "segments": dict(result["segments"])}

        default_notes = [{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}]
        with (
            patch.dict(self.main.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_daily_feed_episode_ref", return_value=EpisodeRef()),
            patch.object(self.main, "_load_recent_feed_notes", return_value=default_notes if notes is None else notes),
            patch.object(self.main, "_daily_feed_has_recent_reflection", return_value=notes is None),
            patch.object(self.main, "daily_feed_passages", return_value=[]),
            patch.object(self.main, "_load_overview_tasks", return_value=([], "not_connected")),
            patch.object(self.main, "_upload_daily_feed_audio", return_value="daily-feed/user123/2026-11-09.mp3"),
            patch.object(self.main, "synthesize_daily_brief_bytes", return_value=(b"audio", {"used_music_beds": False})) as synth,
            patch.object(self.main, "_summarize", side_effect=fake_summarize),
            patch.object(self.main, "load_weather_context", return_value=(None, {}, {"available": False}), create=True),
            patch.object(self.main, "_calendar_service_for_user", return_value=service),
            patch.object(self.main, "_log_usage_event"),
        ):
            episode = self.main._build_daily_feed_episode("user123", user_ref.data, now_utc=now_utc, force=True)
        episode["_professional_text"] = synth.call_args.kwargs["professional_text"]
        return episode, prompts

    def assertCalendarOmitted(self, episode, prompts, reason):
        self.assertNotIn("calendar_today", episode["segments_used"])
        self.assertNotIn("calendar_today", episode["script_segments"])
        self.assertNotIn("TODAY'S CALENDAR", prompts[0])
        self.assertNotIn("staff meeting", episode["script_text"].lower())
        self.assertNotIn("calendar", episode["context_sources_used"])
        meta = episode["generation_meta"]
        self.assertEqual(meta["calendar_context_available"], False)
        self.assertEqual(meta["calendar_today"], {"offered": 0, "applied": False, "omission_reason": reason})
        self.assertEqual(episode["episode_type"], "standard")
        self.assertIn("practical_briefing", episode["segments_used"])

    # ── Omit paths ───────────────────────────────────────────────────────────

    def test_calendar_omitted_when_disconnected(self):
        service = FakeCalendarService()
        user = {**CONNECTED_USER, "google_calendar_connected": False}
        episode, prompts = self._generate(user, service)
        self.assertCalendarOmitted(episode, prompts, "not_connected")
        self.assertEqual(service.event_calls, [])

    def test_calendar_omitted_when_api_errors(self):
        service = FakeCalendarService(error=RuntimeError("HttpError 503 backendError"))
        episode, prompts = self._generate(CONNECTED_USER, service)
        self.assertCalendarOmitted(episode, prompts, "api_error")

    def test_calendar_omitted_when_nothing_is_on_today(self):
        service = FakeCalendarService(events={"primary-id": [EVENTS_BY_CALENDAR["primary-id"][5]], "school-id": []})
        episode, prompts = self._generate(CONNECTED_USER, service)
        self.assertCalendarOmitted(episode, prompts, "no_events_today")

    def test_model_calendar_text_is_dropped_when_nothing_was_offered(self):
        """The model is handed no calendar, so anything it writes there is invented."""
        service = FakeCalendarService(events={"primary-id": [], "school-id": []})
        episode, prompts = self._generate(CONNECTED_USER, service)
        self.assertCalendarOmitted(episode, prompts, "no_events_today")
        self.assertIn("leave as an empty string", prompts[0])

    # ── Loader ───────────────────────────────────────────────────────────────

    def test_loader_reads_shown_calendars_and_filters_declined_cancelled_and_other_days(self):
        service = FakeCalendarService()
        local_now = NOW_UTC.astimezone(DENVER)
        with patch.object(self.main, "_calendar_service_for_user", return_value=service):
            events, reason = self.main._load_calendar_events("user123", CONNECTED_USER, local_now)
        self.assertEqual(reason, "")
        self.assertEqual(
            [e["title"] for e in events],
            ["Conference week", "Staff meeting", "Parent call", "Dentist"],
        )
        self.assertTrue(events[0]["all_day"])
        self.assertEqual(events[1]["location"], "Room 214")
        self.assertEqual(sorted(c["calendarId"] for c in service.event_calls), ["primary-id", "school-id"])
        window = service.event_calls[0]
        self.assertEqual(window["timeMin"], "2026-11-09T00:00:00-07:00")
        self.assertEqual(window["timeMax"], "2026-11-10T00:00:00-07:00")
        self.assertTrue(window["singleEvents"])

    def test_loader_falls_back_to_primary_when_no_calendar_is_selected(self):
        service = FakeCalendarService(
            calendars=[{"id": "x", "summary": "X", "selected": False}],
            events={"primary": [EVENTS_BY_CALENDAR["primary-id"][0]]},
        )
        local_now = NOW_UTC.astimezone(DENVER)
        with patch.object(self.main, "_calendar_service_for_user", return_value=service):
            events, reason = self.main._load_calendar_events("user123", CONNECTED_USER, local_now)
        self.assertEqual(reason, "")
        self.assertEqual([e["title"] for e in events], ["Staff meeting"])

    def test_spoken_times(self):
        self.assertEqual(self.main._spoken_clock(datetime(2026, 11, 9, 9, 0, tzinfo=DENVER)), "9 AM")
        self.assertEqual(self.main._spoken_clock(datetime(2026, 11, 9, 13, 30, tzinfo=DENVER)), "1:30 PM")
        self.assertEqual(self.main._spoken_clock(datetime(2026, 11, 9, 0, 5, tzinfo=DENVER)), "12:05 AM")
        self.assertEqual(self.main._spoken_clock(datetime(2026, 11, 9, 12, 0, tzinfo=DENVER)), "12 PM")
        event = {"all_day": False, "start": "2026-11-09T09:00:00-07:00", "end": "2026-11-09T09:45:00-07:00"}
        self.assertEqual(self.main._spoken_event_time(event), "9 AM to 9:45 AM")
        self.assertEqual(self.main._spoken_event_time({"all_day": True, "start": "2026-11-09T00:00:00-07:00"}), "all day")

    # ── Spoken segment ───────────────────────────────────────────────────────

    def test_calendar_is_offered_to_the_model_and_spoken(self):
        service = FakeCalendarService()
        episode, prompts = self._generate(CONNECTED_USER, service)
        prompt = prompts[0]
        self.assertIn("TODAY'S CALENDAR", prompt)
        self.assertIn("[C1] all day — Conference week", prompt)
        self.assertIn("[C2] 9 AM to 9:45 AM — Staff meeting (at Room 214)", prompt)
        self.assertIn("[C4] 1:30 PM to 2:30 PM — Dentist", prompt)
        self.assertNotIn("Declined thing", prompt)
        self.assertNotIn("Hidden calendar event", prompt)
        self.assertIn("Say each time exactly as given", prompt)
        self.assertIn("calendar_today", episode["segments_used"])
        self.assertIn("calendar", episode["context_sources_used"])
        self.assertIn("staff meeting at 9 AM", episode["_professional_text"])
        meta = episode["generation_meta"]
        self.assertEqual(meta["calendar_context_available"], True)
        self.assertEqual(meta["calendar_today"], {"offered": 4, "applied": True, "omission_reason": ""})

    def test_calendar_not_spoken_is_recorded_as_not_spoken(self):
        service = FakeCalendarService()
        result = {**MODEL_RESULT, "segments": {**MODEL_RESULT["segments"], "calendar_today": ""}}
        episode, _ = self._generate(CONNECTED_USER, service, model_result=result)
        self.assertNotIn("calendar_today", episode["segments_used"])
        self.assertEqual(episode["generation_meta"]["calendar_today"], {"offered": 4, "applied": False, "omission_reason": "not_spoken"})

    def test_fallback_speaks_the_calendar_without_the_model(self):
        service = FakeCalendarService()
        episode, _ = self._generate(CONNECTED_USER, service, summarize_error=RuntimeError("model down"))
        self.assertEqual(episode["episode_type"], "fallback")
        text = episode["script_segments"]["calendar_today"]
        self.assertIn("Running through the whole day: Conference week.", text)
        self.assertIn("You have 3 appointments on the calendar today.", text)
        self.assertIn("At 9 AM to 9:45 AM, Staff meeting, at Room 214.", text)
        self.assertIn("At 1:30 PM to 2:30 PM, Dentist.", text)
        self.assertIn("calendar", episode["context_sources_used"])

    def test_no_reflections_fallback_anchors_on_the_first_appointment(self):
        service = FakeCalendarService()
        episode, _ = self._generate(CONNECTED_USER, service, summarize_error=RuntimeError("model down"), notes=[])
        self.assertEqual(episode["episode_type"], "fallback")
        self.assertIn("Conference week", episode["title"])
        self.assertIn("Arrive at Conference week with a clear head", episode["script_segments"]["practical_briefing"])
        self.assertEqual(episode["script_meta"]["has_continuity_anchor"], True)

    # ── OAuth plumbing ───────────────────────────────────────────────────────

    def test_calendar_scope_is_requested_only_when_asked(self):
        self.assertNotIn(self.main.CALENDAR_READONLY_SCOPE, self.main._requested_google_scopes())
        self.assertIn(self.main.CALENDAR_READONLY_SCOPE, self.main._requested_google_scopes(include_calendar=True))
        self.assertNotIn(self.main.TASKS_READONLY_SCOPE, self.main._requested_google_scopes(include_calendar=True))

    def test_narrower_reauth_keeps_calendar_grant(self):
        existing = {"token": "old", "refresh_token": "r1", "scopes": [self.main.CALENDAR_READONLY_SCOPE, "openid"]}
        new = {"token": "new", "scopes": ["openid"], "client_id": "c"}
        merged = self.main._merge_google_token(existing, new)
        self.assertIn(self.main.CALENDAR_READONLY_SCOPE, merged["scopes"])
        self.assertEqual(merged["refresh_token"], "r1")
        self.assertEqual(merged["client_id"], "c")

    def test_user_calendar_connected_reads_token_scopes_or_flag(self):
        self.assertTrue(self.main._user_calendar_connected({"google_drive_token": {"scopes": [self.main.CALENDAR_READONLY_SCOPE]}}))
        self.assertTrue(self.main._user_calendar_connected({"google_calendar_connected": True}))
        self.assertFalse(self.main._user_calendar_connected({"google_drive_token": {"scopes": ["openid"]}}))
        self.assertFalse(self.main._user_calendar_connected({}))


if __name__ == "__main__":
    unittest.main()
