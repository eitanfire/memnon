import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path("/Users/eitan/memnon")
FUNCTIONS_DIR = REPO_ROOT / "functions"
MAIN_PATH = FUNCTIONS_DIR / "main.py"
DASHBOARD_PATH = REPO_ROOT / "public" / "dashboard.html"


def load_main_module():
    sys.path.insert(0, str(FUNCTIONS_DIR))
    spec = importlib.util.spec_from_file_location("memnon_functions_main", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeDoc:
    def __init__(self, data=None, exists=True, doc_id="user123"):
        self._data = data or {}
        self.exists = exists
        self.id = doc_id

    def to_dict(self):
        return dict(self._data)


class FakeUsageCollection:
    def __init__(self):
        self.events = []

    def add(self, payload):
        self.events.append(payload)


class FakeUserRef:
    def __init__(self, data=None, doc_id="user123"):
        self.data = data or {}
        self.doc_id = doc_id
        self.set_calls = []
        self.usage = FakeUsageCollection()

    def get(self):
        return FakeDoc(self.data, True, self.doc_id)

    def set(self, payload, merge=False):
        self.set_calls.append((payload, merge))
        self.data.update(payload)

    def collection(self, name):
        if name != "usage_events":
            raise AssertionError(f"unexpected subcollection: {name}")
        return self.usage


class FakeUsersCollection:
    def __init__(self, ref):
        self.ref = ref

    def document(self, uid):
        return self.ref


class FakeDB:
    def __init__(self, ref):
        self.ref = ref

    def collection(self, name):
        if name != "users":
            raise AssertionError(f"unexpected collection: {name}")
        return FakeUsersCollection(self.ref)


class DailyFeedSliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()

    def test_build_daily_feed_status_transitions(self):
        user_data = {
            "daily_feed_enabled": True,
            "daily_feed_timezone": "America/Denver",
            "daily_feed_publish_hour_local": 4,
        }

        with patch.object(self.main, "_load_latest_daily_feed_episode", return_value=None):
            scheduled = self.main._build_daily_feed_status(
                "user123",
                user_data,
                datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc),
            )
            preparing = self.main._build_daily_feed_status(
                "user123",
                user_data,
                datetime(2026, 6, 24, 10, 30, tzinfo=timezone.utc),
            )
            missing = self.main._build_daily_feed_status(
                "user123",
                user_data,
                datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
            )

        ready_episode = {
            "id": "2026-06-24",
            "date_key": "2026-06-24",
            "title": "June 24 Test",
            "description": "desc",
            "episode_type": "standard",
            "published_at": datetime(2026, 6, 24, 10, 5, tzinfo=timezone.utc),
        }
        with patch.object(self.main, "_load_latest_daily_feed_episode", return_value=ready_episode):
            ready = self.main._build_daily_feed_status(
                "user123",
                user_data,
                datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(scheduled["state"], "scheduled")
        self.assertEqual(preparing["state"], "preparing")
        self.assertEqual(missing["state"], "missing")
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["latest_episode"]["title"], "June 24 Test")

    def test_setup_daily_feed_returns_status_and_founder_flag(self):
        user_ref = FakeUserRef({"email": "eitanfire@gmail.com", "daily_feed_enabled": True})
        client = self.main.flask_app.test_client()

        with (
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_verify_firebase_token", return_value="user123"),
            patch.object(
                self.main,
                "_ensure_daily_feed_config",
                return_value={
                    "email": "eitanfire@gmail.com",
                    "daily_feed_enabled": True,
                    "daily_feed_token": "tok123",
                    "daily_feed_timezone": "America/Denver",
                    "daily_feed_publish_hour_local": 4,
                },
            ),
            patch.object(self.main, "_daily_feed_url_for_token", return_value="https://example.test/feed/tok123.xml"),
            patch.object(
                self.main,
                "_build_daily_feed_status",
                return_value={
                    "state": "ready",
                    "last_generated_at": "ts1",
                    "last_attempted_at": "ts2",
                    "last_error": "",
                },
            ),
            patch.object(self.main, "_email_is_founder", return_value=True),
            patch.object(self.main, "_log_usage_event") as log_usage,
        ):
            response = client.post("/daily-feed/setup", json={"enabled": True})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["feed_url"], "https://example.test/feed/tok123.xml")
        self.assertTrue(payload["daily_feed_can_regenerate"])
        self.assertEqual(payload["daily_feed_status"]["state"], "ready")
        log_usage.assert_called_once()

    def test_generate_daily_feed_today_forbidden_for_non_founder(self):
        user_ref = FakeUserRef({"email": "teacher@example.com", "daily_feed_enabled": True})
        client = self.main.flask_app.test_client()

        with (
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_verify_firebase_token", return_value="user123"),
            patch.object(self.main, "_user_is_founder", return_value=False),
        ):
            response = client.post("/daily-feed/generate-today", json={})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "forbidden"})

    def test_generate_daily_feed_today_returns_episode_payload_for_founder(self):
        user_ref = FakeUserRef({"email": "eitanfire@gmail.com", "daily_feed_enabled": True})
        client = self.main.flask_app.test_client()

        with (
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_verify_firebase_token", return_value="user123"),
            patch.object(self.main, "_user_is_founder", return_value=True),
            patch.object(
                self.main,
                "_ensure_daily_feed_config",
                return_value={
                    "email": "eitanfire@gmail.com",
                    "daily_feed_enabled": True,
                    "daily_feed_token": "tok123",
                    "daily_feed_timezone": "America/Denver",
                    "daily_feed_publish_hour_local": 4,
                },
            ),
            patch.object(
                self.main,
                "_build_daily_feed_episode",
                return_value={
                    "id": "2026-06-24",
                    "title": "June 24",
                    "description": "desc",
                    "episode_type": "standard",
                    "duration_seconds": 120,
                },
            ),
            patch.object(self.main, "_daily_feed_url_for_token", return_value="https://example.test/feed/tok123.xml"),
            patch.object(
                self.main,
                "_build_daily_feed_status",
                return_value={
                    "state": "ready",
                    "last_generated_at": "ts1",
                    "last_attempted_at": "ts2",
                    "last_error": "",
                },
            ),
        ):
            response = client.post("/daily-feed/generate-today", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["feed_url"], "https://example.test/feed/tok123.xml")
        self.assertEqual(payload["episode"]["id"], "2026-06-24")
        self.assertEqual(payload["episode"]["audio_url"], "https://api-4hth6oktaa-uc.a.run.app/feed/tok123/2026-06-24.mp3")

    def test_save_setup_clears_weather_cache_when_school_anchor_changes(self):
        existing_user = {
            "school_name": "Jefferson Academy",
            "school_city": "BROOMFIELD",
            "school_state": "CO",
            "weather_location_label": "Jefferson Academy Colorado",
            "weather_latitude": 39.7392,
            "weather_longitude": -104.9903,
            "weather_timezone": "America/Denver",
            "weather_geocoded_from": "Jefferson Academy, CO",
        }
        user_ref = FakeUserRef(existing_user)
        client = self.main.flask_app.test_client()

        with (
            patch.object(self.main, "_verify_firebase_token", return_value="user123"),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
        ):
            response = client.post("/setup", json={
                "lane": "professional",
                "profession": "teacher",
                "reflection_style": "complete",
                "school_name": "Arapahoe Ridge",
                "school_state": "CO",
            })

        self.assertEqual(response.status_code, 200)
        saved_payload, merge_flag = user_ref.set_calls[-1]
        self.assertTrue(merge_flag)
        self.assertIsNone(saved_payload["weather_latitude"])
        self.assertIsNone(saved_payload["weather_longitude"])
        self.assertEqual(saved_payload["weather_geocoded_from"], "")

    def test_save_setup_clears_weather_cache_when_school_city_changes(self):
        existing_user = {
            "school_name": "Jefferson Academy",
            "school_city": "BROOMFIELD",
            "school_state": "CO",
            "weather_location_label": "Broomfield Colorado",
            "weather_latitude": 39.92054,
            "weather_longitude": -105.08665,
            "weather_timezone": "America/Denver",
            "weather_geocoded_from": "BROOMFIELD",
        }
        user_ref = FakeUserRef(existing_user)
        client = self.main.flask_app.test_client()

        with (
            patch.object(self.main, "_verify_firebase_token", return_value="user123"),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
        ):
            response = client.post("/setup", json={
                "lane": "professional",
                "profession": "teacher",
                "reflection_style": "complete",
                "school_name": "Jefferson Academy",
                "school_city": "LAFAYETTE",
                "school_state": "CO",
            })

        self.assertEqual(response.status_code, 200)
        saved_payload, merge_flag = user_ref.set_calls[-1]
        self.assertTrue(merge_flag)
        self.assertIsNone(saved_payload["weather_latitude"])
        self.assertIsNone(saved_payload["weather_longitude"])
        self.assertEqual(saved_payload["weather_geocoded_from"], "")

    def test_build_daily_feed_prompt_includes_weather_block_when_available(self):
        prompt = self.main._build_daily_feed_prompt(
            user_data={
                "preferred_name": "Jordan",
                "spoken_name": "Jordan",
                "reflection_style": "complete",
                "school_name": "Jefferson Academy",
                "school_state": "CO",
            },
            notes=[{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}],
            episode_type="standard",
            local_now=datetime(2026, 6, 24, 8, 0, tzinfo=timezone.utc),
            weather_context={
                "day_type": "stormy",
                "temperature_summary": "High 78F, low 52F",
                "precipitation_summary": "70% precipitation risk, 0.3 mm expected",
                "orientation_cue": "A stormy afternoon may make transitions and end-of-day energy heavier than usual.",
            },
        )
        self.assertIn("--- WEATHER CONTEXT ---", prompt)
        self.assertIn("stormy", prompt.lower())
        self.assertIn("Outside context", prompt)
        self.assertIn("Do not create a weather segment", prompt)

    def test_build_deterministic_daily_feed_result_puts_outside_context_in_opening(self):
        result = self.main._build_deterministic_daily_feed_result(
            user_data={"preferred_name": "Jordan"},
            notes=[{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}],
            local_now=datetime(2026, 6, 24, 8, 0, tzinfo=timezone.utc),
            weather_context={
                "should_surface": True,
                "micro_cue_text": "Outside context: A stormy afternoon may make transitions and end-of-day energy heavier than usual.",
                "orientation_cue": "A stormy afternoon may make transitions and end-of-day energy heavier than usual.",
            },
        )
        self.assertIn("Outside context:", result["segments"]["opening"])
        self.assertEqual(result["_weather_applied"], True)

    def test_build_deterministic_daily_feed_result_uses_restrained_practical_stance(self):
        result = self.main._build_deterministic_daily_feed_result(
            user_data={"preferred_name": "Jordan"},
            notes=[{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}],
            local_now=datetime(2026, 6, 24, 8, 0, tzinfo=timezone.utc),
            weather_context=None,
        )
        practical = result["segments"]["practical_briefing"]
        self.assertNotIn("One thread worth holding onto", practical)
        self.assertIn("restrained stance", practical.lower())
        self.assertEqual(result["segments"]["calendar_today"], "")

    def test_build_daily_feed_episode_attempts_weather_enrichment_and_continues_on_failure(self):
        user_ref = FakeUserRef({
            "email": "eitanfire@gmail.com",
            "preferred_name": "Jordan",
            "spoken_name": "Jordan",
            "reflection_style": "complete",
            "school_name": "Jefferson Academy",
            "school_state": "CO",
        })

        class EpisodeRef:
            def __init__(self):
                self.saved = {}

            def get(self):
                if self.saved:
                    return FakeDoc(self.saved, True, "2026-06-24")
                return FakeDoc({}, False, "2026-06-24")

            def set(self, payload, merge=False):
                self.saved.update(payload)

        episode_ref = EpisodeRef()

        with (
            patch.dict(self.main.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_daily_feed_episode_ref", return_value=episode_ref),
            patch.object(self.main, "_load_recent_feed_notes", return_value=[{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}]),
            patch.object(self.main, "_upload_daily_feed_audio", return_value="daily-feed/user123/2026-06-24.mp3"),
            patch.object(self.main, "synthesize_daily_brief_bytes", return_value=(b"audio", {"used_music_beds": False})),
            patch.object(self.main, "_summarize", return_value={
                "title": "June 24",
                "description": "desc",
                "time_anchor": "Today is Tuesday, June 24th.",
                "continuity_anchor": "Protect the morning",
                "segments": {
                    "opening": "Today is Tuesday, June 24th.",
                    "practical_briefing": "Protect the morning.",
                    "calendar_today": "",
                    "reflective_grounding": "This still matters today.",
                    "meditative_close": "One next step is enough.",
                },
            }),
            patch.object(self.main, "load_weather_context", side_effect=RuntimeError("timeout"), create=True) as load_weather,
            patch.object(self.main, "_log_usage_event"),
        ):
            result = self.main._build_daily_feed_episode(
                "user123",
                user_ref.data,
                now_utc=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
                force=True,
            )

        self.assertEqual(result["id"], "2026-06-24")
        load_weather.assert_called_once()
        self.assertEqual(result["generation_meta"]["weather"]["available"], False)
        self.assertEqual(result["generation_meta"]["weather"]["unavailable_reason"], "unexpected_weather_exception")

    def test_build_daily_feed_episode_inserts_outside_context_micro_cue_into_opening(self):
        user_ref = FakeUserRef({
            "email": "eitanfire@gmail.com",
            "preferred_name": "Jordan",
            "spoken_name": "Jordan",
            "reflection_style": "complete",
            "school_name": "Jefferson Academy",
            "school_city": "BROOMFIELD",
            "school_state": "CO",
        })

        class EpisodeRef:
            def __init__(self):
                self.saved = {}

            def get(self):
                if self.saved:
                    return FakeDoc(self.saved, True, "2026-06-24")
                return FakeDoc({}, False, "2026-06-24")

            def set(self, payload, merge=False):
                self.saved.update(payload)

        episode_ref = EpisodeRef()
        weather_context = {
            "day_type": "stormy",
            "temperature_summary": "High 85F, low 62F",
            "precipitation_summary": "30% precipitation risk, 0.0 mm expected",
            "orientation_cue": "A stormy afternoon may make transitions and end-of-day energy heavier than usual.",
            "micro_cue_label": "Outside context",
            "micro_cue_text": "Outside context: A stormy afternoon may make transitions and end-of-day energy heavier than usual.",
            "should_surface": True,
            "omission_reason": "",
        }
        weather_diagnostics = {
            "available": True,
            "source": "cached_coordinates",
            "unavailable_reason": "",
        }
        summarize_results = [
            {
                "title": "June 24",
                "description": "desc",
                "time_anchor": "Today is Tuesday, June 24th.",
                "continuity_anchor": "Protect the morning",
                "segments": {
                    "opening": "Today is Tuesday, June 24th.",
                    "practical_briefing": "Protect the morning and keep transitions simple.",
                    "calendar_today": "",
                    "reflective_grounding": "This still matters today.",
                    "meditative_close": "One next step is enough.",
                },
            },
            {
                "opening": "Today is Tuesday, June 24th. Outside context: A stormy afternoon may make transitions and end-of-day energy heavier than usual.",
            },
        ]

        with (
            patch.dict(self.main.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_daily_feed_episode_ref", return_value=episode_ref),
            patch.object(self.main, "_load_recent_feed_notes", return_value=[{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}]),
            patch.object(self.main, "_daily_feed_has_recent_reflection", return_value=True),
            patch.object(self.main, "_upload_daily_feed_audio", return_value="daily-feed/user123/2026-06-24.mp3"),
            patch.object(self.main, "synthesize_daily_brief_bytes", return_value=(b"audio", {"used_music_beds": False})),
            patch.object(self.main, "load_weather_context", return_value=(weather_context, {"weather_geocoded_from": "BROOMFIELD"}, weather_diagnostics), create=True),
            patch.object(self.main, "_summarize", side_effect=summarize_results) as summarize,
            patch.object(self.main, "_log_usage_event"),
        ):
            result = self.main._build_daily_feed_episode(
                "user123",
                user_ref.data,
                now_utc=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
                force=True,
            )

        self.assertEqual(
            result["script_segments"]["opening"],
            "Today is Tuesday, June 24th. Outside context: A stormy afternoon may make transitions and end-of-day energy heavier than usual.",
        )
        self.assertEqual(result["script_segments"]["reflective_grounding"], "This still matters today.")
        self.assertEqual(result["script_segments"]["meditative_close"], "One next step is enough.")
        self.assertIn("Outside context:", result["script_segments"]["opening"])
        self.assertEqual(result["script_segments"]["practical_briefing"], "Protect the morning and keep transitions simple.")
        self.assertEqual(result["generation_meta"]["weather"]["applied"], True)
        self.assertEqual(result["generation_meta"]["weather"]["placement"], "opening_micro_cue")
        self.assertEqual(summarize.call_count, 2)
        base_prompt = summarize.call_args_list[0].args[0]
        rewrite_prompt = summarize.call_args_list[1].args[0]
        self.assertIn("--- WEATHER CONTEXT ---", base_prompt)
        self.assertIn("Today is Tuesday, June 24th.", rewrite_prompt)
        self.assertIn("A stormy afternoon may make transitions and end-of-day energy heavier than usual.", rewrite_prompt)

    def test_build_daily_feed_episode_records_mild_weather_omission_metadata(self):
        user_ref = FakeUserRef({
            "email": "eitanfire@gmail.com",
            "preferred_name": "Jordan",
            "spoken_name": "Jordan",
            "reflection_style": "complete",
            "school_name": "Jefferson Academy",
            "school_state": "CO",
        })

        class EpisodeRef:
            def __init__(self):
                self.saved = {}

            def get(self):
                if self.saved:
                    return FakeDoc(self.saved, True, "2026-06-24")
                return FakeDoc({}, False, "2026-06-24")

            def set(self, payload, merge=False):
                self.saved.update(payload)

        episode_ref = EpisodeRef()
        weather_context = {
            "day_type": "clear",
            "orientation_cue": "The weather looks steady enough to support a more open, straightforward day.",
            "micro_cue_label": "Outside context",
            "micro_cue_text": "Outside context: The weather looks steady enough to support a more open, straightforward day.",
            "should_surface": False,
            "omission_reason": "mild_conditions",
        }
        weather_diagnostics = {
            "available": True,
            "source": "cached_coordinates",
            "unavailable_reason": "",
        }

        with (
            patch.dict(self.main.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_daily_feed_episode_ref", return_value=episode_ref),
            patch.object(self.main, "_load_recent_feed_notes", return_value=[{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}]),
            patch.object(self.main, "_daily_feed_has_recent_reflection", return_value=True),
            patch.object(self.main, "_upload_daily_feed_audio", return_value="daily-feed/user123/2026-06-24.mp3"),
            patch.object(self.main, "synthesize_daily_brief_bytes", return_value=(b"audio", {"used_music_beds": False})),
            patch.object(self.main, "load_weather_context", return_value=(weather_context, {}, weather_diagnostics), create=True),
            patch.object(self.main, "_summarize", return_value={
                "title": "June 24",
                "description": "desc",
                "time_anchor": "Today is Tuesday, June 24th.",
                "continuity_anchor": "Protect the morning",
                "segments": {
                    "opening": "Today is Tuesday, June 24th.",
                    "practical_briefing": "Protect the morning and keep transitions simple.",
                    "calendar_today": "",
                    "reflective_grounding": "This still matters today.",
                    "meditative_close": "One next step is enough.",
                },
            }) as summarize,
            patch.object(self.main, "_log_usage_event"),
        ):
            result = self.main._build_daily_feed_episode(
                "user123",
                user_ref.data,
                now_utc=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
                force=True,
            )

        self.assertEqual(summarize.call_count, 1)
        self.assertNotIn("Outside context:", result["script_segments"]["opening"])
        self.assertEqual(result["generation_meta"]["weather"]["available"], True)
        self.assertEqual(result["generation_meta"]["weather"]["applied"], False)
        self.assertEqual(result["generation_meta"]["weather"]["omission_reason"], "mild_conditions")

    def test_build_daily_feed_episode_records_weather_unavailable_reason(self):
        user_ref = FakeUserRef({
            "email": "eitanfire@gmail.com",
            "preferred_name": "Jordan",
            "spoken_name": "Jordan",
            "reflection_style": "complete",
            "school_name": "Jefferson Academy",
            "school_state": "CO",
        })

        class EpisodeRef:
            def __init__(self):
                self.saved = {}

            def get(self):
                if self.saved:
                    return FakeDoc(self.saved, True, "2026-06-24")
                return FakeDoc({}, False, "2026-06-24")

            def set(self, payload, merge=False):
                self.saved.update(payload)

        episode_ref = EpisodeRef()
        weather_diagnostics = {
            "available": False,
            "source": "",
            "unavailable_reason": "forecast_failed",
        }

        with (
            patch.dict(self.main.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False),
            patch.object(self.main, "_get_db", return_value=FakeDB(user_ref)),
            patch.object(self.main, "_daily_feed_episode_ref", return_value=episode_ref),
            patch.object(self.main, "_load_recent_feed_notes", return_value=[{"title": "Recent reflection", "summary": "Stay steady", "insight": "Protect the morning"}]),
            patch.object(self.main, "_daily_feed_has_recent_reflection", return_value=True),
            patch.object(self.main, "_upload_daily_feed_audio", return_value="daily-feed/user123/2026-06-24.mp3"),
            patch.object(self.main, "synthesize_daily_brief_bytes", return_value=(b"audio", {"used_music_beds": False})),
            patch.object(self.main, "load_weather_context", return_value=(None, {}, weather_diagnostics), create=True),
            patch.object(self.main, "_summarize", return_value={
                "title": "June 24",
                "description": "desc",
                "time_anchor": "Today is Tuesday, June 24th.",
                "continuity_anchor": "Protect the morning",
                "segments": {
                    "opening": "Today is Tuesday, June 24th.",
                    "practical_briefing": "Protect the morning and keep transitions simple.",
                    "calendar_today": "",
                    "reflective_grounding": "This still matters today.",
                    "meditative_close": "One next step is enough.",
                },
            }),
            patch.object(self.main, "_log_usage_event"),
        ):
            result = self.main._build_daily_feed_episode(
                "user123",
                user_ref.data,
                now_utc=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
                force=True,
            )

        self.assertEqual(result["generation_meta"]["weather"]["available"], False)
        self.assertEqual(result["generation_meta"]["weather"]["applied"], False)
        self.assertEqual(result["generation_meta"]["weather"]["unavailable_reason"], "forecast_failed")

    def test_dashboard_includes_admin_regenerate_controls(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        self.assertIn('id="daily-brief-regenerate-btn"', html)
        self.assertIn("daily_feed_can_regenerate", html)
        self.assertIn("Last generated", html)


if __name__ == "__main__":
    unittest.main()
