"""The merged voice library: data integrity, spoken names, verbatim, same_as.

The staged library (seeds/library) was merged by
scripts/merge_voice_library.py. These tests hold the merged data to the
library's rules and pin how the prompts carry it.
"""
import json
import unittest
from datetime import date
from pathlib import Path

from tests.test_daily_feed_slice import FUNCTIONS_DIR, load_main_module

ROOT = FUNCTIONS_DIR.parent
COPIES = (ROOT / "data" / "sources.json", FUNCTIONS_DIR / "sources.json", ROOT / "public" / "sources.json")


def _passages(sources):
    return [p for g in sources["guides"].values() for p in g["passages"]]


class VoiceLibraryDataTests(unittest.TestCase):
    def setUp(self):
        self.sources = json.loads(COPIES[0].read_text())

    def test_copies_are_in_sync(self):
        for path in COPIES[1:]:
            self.assertEqual(json.loads(path.read_text()), self.sources, path)

    def test_every_passage_tag_exists_in_the_tag_vocabulary(self):
        vocabulary = set(self.sources["tag_vocabulary"])
        for p in _passages(self.sources):
            self.assertLessEqual(set(p["tags"]), vocabulary, p["id"])

    def test_no_passage_id_repeats(self):
        ids = [p["id"] for p in _passages(self.sources)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_four_removed_ids_are_gone(self):
        ids = {p["id"] for p in _passages(self.sources)}
        self.assertFalse(ids & {"mai_2", "jd_1", "jd_3", "bh_3"})

    def test_replaced_ids_are_gone_and_live_on_in_same_as(self):
        ids = {p["id"] for p in _passages(self.sources)}
        same_as = {s for p in _passages(self.sources) for s in p.get("same_as", [])}
        for old in ("mai_1", "pa_2_4", "mb_1", "pp_2", "pf_2", "jd_2", "bh_1"):
            self.assertNotIn(old, ids)
            self.assertIn(old, same_as)

    def test_same_as_points_at_real_passages_or_replaced_ids(self):
        ids = {p["id"] for p in _passages(self.sources)}
        by_id = {p["id"]: p for p in _passages(self.sources)}
        self.assertIn("pa_t29", by_id["mai_t10"]["same_as"])
        self.assertIn("mai_t10", by_id["pa_t29"]["same_as"])
        for p in _passages(self.sources):
            for other in p.get("same_as", []):
                if other in ids:
                    self.assertIn(p["id"], by_id[other]["same_as"], (p["id"], other))

    def test_new_jewish_guides_are_in_the_tradition(self):
        judaism = self.sources["traditions"]["judaism"]["guides"]
        for guide_id in ("hillel", "rebbe_nachman", "heschel", "jonathan_sacks"):
            self.assertIn(guide_id, judaism)
            self.assertIn(guide_id, self.sources["guides"])

    def test_every_tradition_guide_exists_or_was_already_missing(self):
        # howard_thurman is listed under education but has never had passages.
        known = set(self.sources["guides"]) | {"howard_thurman"}
        for tradition in self.sources["traditions"].values():
            self.assertLessEqual(set(tradition["guides"]), known)

    def test_staged_passages_carry_the_library_shape(self):
        for p in self.sources["guides"]["heschel"]["passages"]:
            self.assertIn(p["kind"], {"teaching", "story", "saying"})
            self.assertTrue(p["text"] and p["ref"] and p["verified_url"])
            self.assertIn("verbatim", p)


class VoiceLibraryPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main_module()
        import lanes
        cls.lanes = lanes

    def _passage(self, source_id):
        return next(p for g in self.lanes.SOURCES["guides"].values() for p in g["passages"] if p["id"] == source_id)

    def test_a_guide_with_a_spoken_name_speaks_it_in_the_reflect_prompt(self):
        prompt, _ = self.lanes.reflect_prompt("teaching was hard today", {"reflect_config": {"selected_guides": ["heschel"]}})
        self.assertIn("] Rabbi Heschel (Abraham Joshua Heschel; ", prompt)
        self.assertIn("Voices the user has chosen: Rabbi Heschel", prompt)

    def test_a_guide_with_a_spoken_name_speaks_it_in_the_daily_feed(self):
        selected = self.main.daily_feed_passages({"reflect_config": {"selected_guides": ["maimonides"]}}, "anger")
        self.assertTrue(selected)
        self.assertTrue(all(p["spoken_name"] == "the Rambam" for p in selected))

    def test_text_is_the_teaching_and_verbatim_is_offered_separately(self):
        user = {"reflect_config": {"selected_guides": ["maimonides"]}}
        selected = self.main.daily_feed_passages(user, "anger")
        with_verbatim = [p for p in selected if p["verbatim"]]
        self.assertTrue(with_verbatim)
        p = with_verbatim[0]
        self.assertEqual(p["text"], self._passage(p["source_id"])["text"])

        prompt, _ = self.lanes.reflect_prompt("anger", user)
        self.assertIn(f'The one phrase you may use in the source\'s own words: "{p["verbatim"]}"', prompt)
        self.assertNotIn("sefaria.org", prompt)

    def test_verified_url_and_note_never_reach_the_prompt(self):
        prompt, _ = self.lanes.reflect_prompt(
            "love courage perseverance teaching resilience",
            {"reflect_config": {"selected_guides": ["paulo_freire"]}},
        )
        self.assertNotIn("http", prompt)
        self.assertNotIn("Verbatim cleared", prompt)

    def test_same_as_counts_as_spoken_for_rotation(self):
        """mai_t10 and pa_t29 are one teaching (the Rambam restates Avot 4:18)."""
        today = date(2026, 11, 27)
        user = {"daily_feed_voice_history": [{"source_id": "pa_t29", "guide_id": "pirkei_avot", "date": "2026-11-26"}]}
        recent = self.main._daily_feed_recent_voice_ids(user, today)
        self.assertIn("mai_t10", recent)

        # A history entry under a replaced id still counts.
        user = {"daily_feed_voice_history": [{"source_id": "mai_1", "guide_id": "maimonides", "date": "2026-11-26"}]}
        self.assertIn("mai_t16", self.main._daily_feed_recent_voice_ids(user, today))

        selected = self.main.daily_feed_passages(
            {"reflect_config": {"selected_guides": ["maimonides", "pirkei_avot"]}},
            "difficult people patience listening empathy discernment",
            exclude_source_ids=self.main._daily_feed_recent_voice_ids(
                {"daily_feed_voice_history": [{"source_id": "pa_t29", "date": "2026-11-26"}]}, today),
        )
        self.assertNotIn("mai_t10", [p["source_id"] for p in selected])

    def test_one_selection_never_offers_both_halves_of_a_same_as_pair(self):
        selected = self.lanes.select_passages(
            ["maimonides", "pirkei_avot"],
            {"difficult_people", "discernment", "patience", "listening", "empathy", "anger"},
        )
        ids = {item["passage"]["id"] for item in selected}
        self.assertFalse({"mai_t10", "pa_t29"} <= ids, ids)


if __name__ == "__main__":
    unittest.main()
