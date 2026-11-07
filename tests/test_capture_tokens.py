"""Capture-token auth: a long-lived per-user token that can create captures and
nothing else, is stored only as a hash, and dies on regenerate or revoke."""
import io
import json
import re
import unittest
from unittest.mock import MagicMock, patch

from tests.test_daily_feed_slice import FakeWorkflowRecord, FakeWorkflowService, load_main_module


class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return json.loads(json.dumps(self._data, default=str)) if self._data is not None else None


class FakeQuery:
    def where(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def stream(self):
        return []

    def get(self):
        return []


class FakeCollection(FakeQuery):
    def __init__(self, store, path):
        self.store = store
        self.path = path

    def document(self, doc_id=None):
        return FakeDocRef(self.store, f"{self.path}/{doc_id or 'auto'}")

    def add(self, payload):
        self.store.setdefault(f"{self.path}/__added__", []).append(payload)
        return None, None


class FakeDocRef:
    def __init__(self, store, path):
        self.store = store
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    def get(self):
        return FakeSnapshot(self.id, self.store.get(self.path))

    def set(self, payload, merge=False):
        current = dict(self.store.get(self.path) or {}) if merge else {}
        for key, value in payload.items():
            if value is FakeFirestore.DELETE_FIELD:
                current.pop(key, None)
            elif merge and isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key] = {**current[key], **value}
            else:
                current[key] = value
        self.store[self.path] = current

    def update(self, payload):
        self.set(payload, merge=True)

    def delete(self):
        self.store.pop(self.path, None)

    def collection(self, name):
        return FakeCollection(self.store, f"{self.path}/{name}")


class FakeFirestore:
    DELETE_FIELD = object()

    def __init__(self):
        self.store = {}

    def collection(self, name):
        return FakeCollection(self.store, name)


def audio_form():
    return {
        "file": (io.BytesIO(b"fake-m4a-bytes"), "action-button.m4a", "audio/x-m4a"),
    }


class CaptureTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()
        import capture_tokens  # importable once load_main_module put functions/ on sys.path
        cls.hash = staticmethod(capture_tokens.hash_capture_token)

    def setUp(self):
        self.db = FakeFirestore()
        self.db.collection("users").document("user123").set({"email": "eitanfire@gmail.com"})
        self.service = FakeWorkflowService(FakeWorkflowRecord(capture_id="cap-token1", input_type="voice"))
        self.client = self.main.flask_app.test_client()
        self.patches = [
            patch.object(self.main, "_get_db", return_value=self.db),
            patch.object(self.main.firestore, "DELETE_FIELD", FakeFirestore.DELETE_FIELD),
            patch.object(self.main, "_workflow_service", return_value=self.service),
            patch.object(self.main, "transcribe_audio_bytes", return_value="Remember to call the bus company about Friday."),
            patch.object(self.main, "_upload_workflow_voice_audio", return_value="workflow-audio/user123/cap.m4a"),
            patch.object(self.main, "_log_usage_event"),
            patch.dict(self.main.os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def _issue(self, uid="user123"):
        """Mint a token through the real setup route, signed in as uid."""
        with patch.object(self.main.fb_auth, "verify_id_token", return_value={"uid": uid}):
            response = self.client.post("/capture-token", headers={"Authorization": "Bearer firebase-id-token"})
        self.assertEqual(response.status_code, 201)
        return response.get_json()["token"]

    def _capture(self, token):
        return self.client.post(
            "/workflows/captures",
            data=audio_form(),
            content_type="multipart/form-data",
            headers={"Authorization": f"Bearer {token}"},
        )

    # ── Accepted on the capture path ─────────────────────────────────────────

    def test_token_creates_an_audio_capture_through_the_pipeline(self):
        token = self._issue()
        self.assertTrue(token.startswith("mcap_"))
        self.assertGreaterEqual(len(token) - len("mcap_"), 43)  # 32 random bytes, urlsafe base64

        response = self._capture(token)

        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["capture_id"], "cap-token1")
        call = self.service.calls[0]
        self.assertEqual(call["uid"], "user123")
        self.assertEqual(call["input_type"], "voice")
        self.assertEqual(call["source_text"], "Remember to call the bus company about Friday.")
        user = self.db.store["users/user123"]
        self.assertIsNotNone(user["capture_token"]["last_used_at"])

    def test_token_keeps_existing_audio_type_and_size_checks(self):
        token = self._issue()
        bad_type = self.client.post(
            "/workflows/captures",
            data={"file": (io.BytesIO(b"x"), "clip.wav", "audio/wav")},
            content_type="multipart/form-data",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(bad_type.status_code, 400)
        too_big = self.client.post(
            "/workflows/captures",
            data={"file": (io.BytesIO(b"x" * (5 * 1024 * 1024 + 1)), "long.m4a", "audio/x-m4a")},
            content_type="multipart/form-data",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(too_big.status_code, 413)
        self.assertEqual(self.service.calls, [])

    def test_unknown_token_is_rejected_on_capture_path(self):
        self._issue()
        response = self._capture("mcap_" + "A" * 43)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.service.calls, [])

    # ── Revoke and regenerate ────────────────────────────────────────────────

    def test_regenerated_token_replaces_the_old_one_immediately(self):
        old = self._issue()
        new = self._issue()
        self.assertNotEqual(old, new)
        self.assertEqual(self._capture(old).status_code, 401)
        self.assertEqual(self._capture(new).status_code, 201)
        token_docs = [k for k in self.db.store if k.startswith("capture_tokens/")]
        self.assertEqual(len(token_docs), 1)

    def test_revoked_token_is_rejected(self):
        token = self._issue()
        with patch.object(self.main.fb_auth, "verify_id_token", return_value={"uid": "user123"}):
            revoked = self.client.delete("/capture-token", headers={"Authorization": "Bearer firebase-id-token"})
            status = self.client.get("/capture-token", headers={"Authorization": "Bearer firebase-id-token"})
        self.assertEqual(revoked.get_json(), {"active": False})
        self.assertEqual(status.get_json(), {"active": False})
        self.assertEqual(self._capture(token).status_code, 401)
        self.assertNotIn("capture_token", self.db.store["users/user123"])
        self.assertFalse([k for k in self.db.store if k.startswith("capture_tokens/")])

    def test_leftover_lookup_doc_does_not_revive_a_replaced_token(self):
        old = self._issue()
        leftover = dict(next(v for k, v in self.db.store.items() if k.startswith("capture_tokens/")))
        self._issue()
        # Simulate a lookup document that survived regeneration.
        self.db.store[f"capture_tokens/{self.hash(old)}"] = leftover
        self.assertEqual(self._capture(old).status_code, 401)

    # ── Never stored raw ─────────────────────────────────────────────────────

    def test_raw_token_is_never_stored(self):
        token = self._issue()
        self._capture(token)
        dumped = json.dumps(self.db.store, default=str)
        self.assertNotIn(token, dumped)
        self.assertNotIn(token[len("mcap_"):len("mcap_") + 20], dumped)
        digest = self.hash(token)
        self.assertIn(f"capture_tokens/{digest}", self.db.store)

    def test_setup_and_me_never_return_the_token_or_its_hash(self):
        token = self._issue()
        digest = self.hash(token)
        with (
            patch.object(self.main.fb_auth, "verify_id_token", return_value={"uid": "user123"}),
            patch.object(self.main, "_load_research_signals", return_value={}),
            patch.object(self.main, "_build_daily_feed_status", return_value={}),
        ):
            status = self.client.get("/capture-token", headers={"Authorization": "Bearer firebase-id-token"})
            me = self.client.get("/me", headers={"Authorization": "Bearer firebase-id-token"})
        for body in (status.get_data(as_text=True), me.get_data(as_text=True)):
            self.assertNotIn(token, body)
            self.assertNotIn(digest, body)
        self.assertEqual(status.get_json()["hint"], token[-4:])
        self.assertTrue(me.get_json()["capture_token"]["active"])

    # ── Rejected everywhere else ─────────────────────────────────────────────

    def test_firebase_check_refuses_capture_tokens_before_verifying(self):
        """Even a Firebase verifier that accepted anything must never see one."""
        token = self._issue()
        request = MagicMock(headers={"Authorization": f"Bearer {token}"})
        with patch.object(self.main.fb_auth, "verify_id_token", return_value={"uid": "user123"}) as verify:
            self.assertIsNone(self.main._verify_firebase_token(request))
        verify.assert_not_called()

    def test_token_grants_nothing_on_any_other_route(self):
        """For every route and method, a valid capture token must get exactly
        what no credentials get, except POST /workflows/captures."""
        token = self._issue()
        app = self.main.flask_app
        old_testing = app.testing
        app.testing = False  # unhandled errors become 500s, compared like any status
        results = []
        blocked = RuntimeError("network disabled in test")
        try:
            with (
                patch.object(self.main.fb_auth, "verify_id_token", side_effect=ValueError("not a Firebase ID token")),
                patch("urllib.request.urlopen", side_effect=blocked),
                patch("requests.Session.request", side_effect=blocked),
            ):
                for rule in app.url_map.iter_rules():
                    if rule.endpoint == "static":
                        continue
                    url = rule.rule
                    for arg in rule.arguments:
                        url = re.sub(rf"<(?:[^:<>]+:)?{arg}>", "x", url)
                    for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                        anon = self.client.open(url, method=method)
                        with_token = self.client.open(
                            url, method=method, headers={"Authorization": f"Bearer {token}"}
                        )
                        results.append((method, rule.rule, anon.status_code, with_token.status_code))
        finally:
            app.testing = old_testing

        differing = [r for r in results if r[2] != r[3]]
        self.assertEqual(differing, [("POST", "/workflows/captures", 401, 400)])
        # Every route that needs a signed-in user answers the token with 401.
        # The rest are public (OAuth, private feed URLs keyed by their own token).
        not_401 = sorted({(r[0], r[1]) for r in results if r[3] != 401})
        self.assertEqual(not_401, [
            ("GET", "/auth/callback"),
            ("GET", "/auth/start"),
            ("GET", "/feed/<token>.xml"),
            ("GET", "/feed/<token>/<episode_id>.mp3"),
            ("GET", "/feed/<token>/library.xml"),
            ("GET", "/feed/<token>/library/<episode_id>.mp3"),
            ("POST", "/workflows/captures"),
        ])
        self.assertGreaterEqual(len(results), 41)


if __name__ == "__main__":
    unittest.main()
