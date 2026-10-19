import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS_DIR = REPO_ROOT / "functions"
MAIN_PATH = FUNCTIONS_DIR / "main.py"


def load_main_module():
    sys.path.insert(0, str(FUNCTIONS_DIR))
    spec = importlib.util.spec_from_file_location("memnon_functions_main_library", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeUserDoc:
    def __init__(self, data=None, doc_id="user123"):
        self._data = data or {}
        self.id = doc_id

    def to_dict(self):
        return dict(self._data)


class LibraryFeedTests(unittest.TestCase):
    def setUp(self):
        self.main = load_main_module()
        self.client = self.main.flask_app.test_client()
        self.user = FakeUserDoc({"preferred_name": "Eitan", "daily_feed_token": "tok"})
        self.episodes = [
            {"id": "living-product", "title": "The Living Product", "description": "Essay",
             "published_at": datetime(2026, 10, 18, tzinfo=timezone.utc), "audio_size_bytes": 1234,
             "audio_storage_path": "library-feed/user123/living-product.mp3"},
            {"id": "seven-bets", "title": "Seven Bets", "description": "Essay",
             "published_at": datetime(2026, 10, 17, tzinfo=timezone.utc), "audio_size_bytes": 99,
             "audio_storage_path": "library-feed/user123/seven-bets.mp3"},
        ]

    def test_library_rss_lists_every_episode_with_library_audio_urls(self):
        with patch.object(self.main, "_user_for_feed_token", return_value=self.user), \
             patch.object(self.main, "_load_library_feed_episodes", return_value=self.episodes):
            resp = self.client.get("/feed/tok/library.xml")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Memnon Library", body)
        self.assertEqual(body.count("<item>"), 2)
        self.assertIn("/feed/tok/library/living-product.mp3", body)
        self.assertIn("/feed/tok/library/seven-bets.mp3", body)
        self.assertIn('length="1234"', body)

    def test_library_rss_unknown_token_is_404(self):
        with patch.object(self.main, "_user_for_feed_token", return_value=None):
            resp = self.client.get("/feed/nope/library.xml")
        self.assertEqual(resp.status_code, 404)

    def _db_with_episode(self, episode):
        snap = MagicMock()
        snap.exists = episode is not None
        snap.to_dict.return_value = episode or {}
        db = MagicMock()
        db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap
        return db

    def test_library_audio_streams_saved_episode(self):
        with patch.object(self.main, "_user_for_feed_token", return_value=self.user), \
             patch.object(self.main, "_get_db", return_value=self._db_with_episode(self.episodes[0])), \
             patch.object(self.main, "_download_daily_feed_audio", return_value=b"mp3bytes") as download:
            resp = self.client.get("/feed/tok/library/living-product.mp3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"mp3bytes")
        self.assertEqual(resp.headers["Content-Type"], "audio/mpeg")
        download.assert_called_once_with("library-feed/user123/living-product.mp3")

    def test_library_audio_missing_episode_is_404(self):
        with patch.object(self.main, "_user_for_feed_token", return_value=self.user), \
             patch.object(self.main, "_get_db", return_value=self._db_with_episode(None)):
            resp = self.client.get("/feed/tok/library/missing.mp3")
        self.assertEqual(resp.status_code, 404)

    def test_daily_feed_audio_uses_the_persistent_feed_bucket(self):
        bucket = MagicMock()
        with patch.object(self.main, "_get_db"), \
             patch.object(self.main.storage, "bucket", return_value=bucket) as get_bucket:
            self.main._upload_daily_feed_audio("user123", "2026-10-18", b"x")
        get_bucket.assert_called_once_with("memnon-app-feed-audio")
        bucket.blob.assert_called_once_with("daily-feed/user123/2026-10-18.mp3")


if __name__ == "__main__":
    unittest.main()
