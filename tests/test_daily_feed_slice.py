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

    def test_dashboard_includes_admin_regenerate_controls(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        self.assertIn('id="daily-brief-regenerate-btn"', html)
        self.assertIn("daily_feed_can_regenerate", html)
        self.assertIn("Last generated", html)


if __name__ == "__main__":
    unittest.main()
