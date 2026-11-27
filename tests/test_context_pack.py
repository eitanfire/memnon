"""Local context pack builder (src/context_pack.py)."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import context_pack as cp  # noqa: E402

NOW = datetime(2026, 11, 27, 11, 0, tzinfo=timezone.utc)


def note(created, title, body, **meta):
    lines = ["---", f"title: \"{title}\"", f"created: {created}"] + [f"{k}: {v}" for k, v in meta.items()] + ["---", "", body]
    return "\n".join(lines)


class ContextPackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        vault = self.root / "vault"
        (vault / "Inbox" / "Voice").mkdir(parents=True)
        (vault / "Growls").mkdir()
        (vault / ".obsidian").mkdir()
        write = lambda rel, text: (vault / rel).write_text(text, encoding="utf-8")
        write("Inbox/Voice/2026-11-24 a.md", note(
            "2026-11-24T15:38:40-07:00", "Consent forms",
            "## Summary\nCollect the consent forms. More detail here.\n\n## Action Items\n- [ ] Collect consent forms\n- [x] Done thing\n",
            type="voice-note", routing_reason="fallback"))
        # Ambient, multi-speaker: another speaker's wife. Must never reach people/household.
        write("Inbox/Voice/2026-11-20 b.md", note(
            "2026-11-20T09:00:00-07:00", "Staff room chatter",
            "## Summary\nA colleague said his wife Dana is in the hospital.\n",
            type="voice-note", routing_reason="keywords(3)"))
        write("Inbox/Voice/2026-10-01 c.md", note(
            "2026-10-01T09:00:00-07:00", "Banana the chicken",
            "## Summary\nThe family chicken.\n\n## Action Items\n- [ ] Old item\n", type="voice-note"))
        write("Inbox/Voice/2026-11-09 therapy-session-3.md", note(
            "2026-11-09T18:53:00-07:00", "Therapy session 3",
            "## Summary\nlong\n", type="therapy-session", summary="\"Flat affect after the Willow news; Haley and Bayla came up.\""))
        write("Inbox/Voice/therapy-sessions-index.md", note(
            "2026-10-05T00:00:00-07:00", "Therapy index",
            "# Index\n\n## Cross-session analysis (as of Nov 9)\n\n### Arc\nAvoidance, then action.\n\n## Appendix\nNot this.\n",
            type="therapy-index"))
        write("Growls/growl-log.md", note("2026-11-25T08:00:00-07:00", "Growl log", "Frustrated about grading."))
        write(".obsidian/hidden.md", "secret")
        self.config = {
            "_config_dir": str(self.root),
            "runtime_dir": str(self.root / "runtime"),
            "ai": {"api_key": "k", "model": "m"},
            "context_pack": {"enabled": True, "uid": "u1", "vault_dir": "./vault"},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_pack_sections_windows_and_order(self):
        pack = cp.build_pack(self.config, now=NOW, people_lines=["- Haley: the listener's wife"])
        md = pack["markdown"]
        self.assertIn("Consent forms: Collect the consent forms.", md)
        self.assertNotIn("Banana", md)  # older than 14 days, and its open item is older than 30
        self.assertNotIn("Old item", md)
        self.assertIn("- Collect consent forms (2026-11-24, Consent forms)", md)
        self.assertNotIn("Done thing", md)
        self.assertIn("Avoidance, then action.", md)
        self.assertNotIn("Not this.", md)
        self.assertIn("Flat affect after the Willow news", md)
        self.assertNotIn("secret", md)
        order = [md.index(h) for h in ("## Recent notes", "## Open action items", "## People and household",
                                       "## Therapy: cross-session", "## Therapy: per-session")]
        self.assertEqual(order, sorted(order))
        self.assertEqual([s["id"] for s in pack["sources"]], cp.SECTION_ORDER)

    def test_total_size_is_capped(self):
        self.config["context_pack"]["max_chars"] = 600
        pack = cp.build_pack(self.config, now=NOW, people_lines=[])
        self.assertLessEqual(pack["total_chars"], 600)
        self.assertIn("size_cap", [s["omitted"] for s in pack["sources"]])

    def test_people_come_only_from_sources_where_the_listener_speaks(self):
        cfg = cp.settings(self.config)
        notes = cp.load_vault(self.root / "vault")
        text = cp.speaker_source_text(notes, cfg)
        self.assertIn("Haley and Bayla", text)
        self.assertIn("Frustrated about grading", text)
        self.assertNotIn("Dana", text)
        self.assertNotIn("consent", text.lower())

    def test_people_are_derived_once_per_source_change_and_uploaded(self):
        calls, uploads = [], []

        def fake_post(url, payload, headers):
            calls.append(payload)
            content = json.dumps({"people": [
                {"name": "Haley", "relationship": "wife", "note": ""},
                {"name": "Someone", "relationship": ""},
                {"name": "Eitan's daughter", "relationship": "daughter", "note": "Was giddy at dinner"},
                {"name": "Richard", "relationship": "therapist", "note": "Sessions focus on emotional avoidance"},
            ]})
            return {"choices": [{"message": {"content": content}}]}

        state = {}
        pack = cp.build_and_upload(self.config, state, now=NOW, post=fake_post,
                                   upload=lambda p, project, uid: uploads.append((project, uid)))
        self.assertIn("- Haley: the listener's wife", pack["markdown"])
        self.assertNotIn("Someone", pack["markdown"])
        self.assertIn("- The listener's daughter (name not stated in the notes); Was giddy at dinner", pack["markdown"])
        self.assertIn("- Richard: the listener's therapist\n", pack["markdown"] + "\n")
        self.assertNotIn("avoidance", pack["markdown"].split("## People and household")[1].split("##")[0])
        self.assertEqual(calls[0]["model"], "gpt-4o")
        self.assertNotIn("Dana", calls[0]["messages"][0]["content"])
        self.assertEqual(uploads, [("memnon-app", "u1")])
        cp.build_and_upload(self.config, state, now=NOW, post=fake_post, upload=lambda *a: None)
        self.assertEqual(len(calls), 1)  # unchanged sources: cached

    def test_people_failure_still_ships_the_rest(self):
        def broken_post(*_a, **_k):
            raise RuntimeError("openai down")

        pack = cp.build_and_upload(self.config, {}, now=NOW, post=broken_post, upload=lambda *a: None)
        self.assertIn("## Recent notes", pack["markdown"])
        self.assertNotIn("## People and household", pack["markdown"])

    def test_runs_once_a_day_after_the_build_hour_and_never_raises(self):
        local = datetime(2026, 11, 27, 2, 0).astimezone()
        cfg = cp.settings(self.config)
        self.assertFalse(cp.is_due({}, cfg, local.replace(hour=2)))
        self.assertTrue(cp.is_due({}, cfg, local.replace(hour=4)))
        self.assertFalse(cp.is_due({"last_built_date": local.date().isoformat()}, cfg, local.replace(hour=4)))

        from unittest.mock import patch
        with patch.object(cp, "build_and_upload", side_effect=RuntimeError("no credentials")):
            self.assertIsNone(cp.run_if_due(self.config, now_local=local.replace(hour=4)))
        state = json.loads((self.root / "runtime" / "context_pack_state.json").read_text())
        self.assertIn("no credentials", state["last_error"])
        # Retries wait, rather than hammering every minute.
        self.assertFalse(cp.is_due(state, cfg, local.replace(hour=4, minute=10)))

    def test_disabled_without_uid(self):
        self.config["context_pack"].pop("uid")
        self.assertIsNone(cp.run_if_due(self.config))

    def test_firestore_document_shape(self):
        doc = cp.firestore_document({"markdown": "x", "generated_at": NOW, "total_chars": 1,
                                     "sources": [{"id": "a", "omitted": ""}]})
        fields = doc["fields"]
        self.assertEqual(fields["markdown"], {"stringValue": "x"})
        self.assertEqual(fields["generated_at"], {"timestampValue": "2026-11-27T11:00:00Z"})
        self.assertEqual(fields["total_chars"], {"integerValue": "1"})
        self.assertEqual(fields["sources"]["arrayValue"]["values"][0]["mapValue"]["fields"]["id"], {"stringValue": "a"})


if __name__ == "__main__":
    unittest.main()
