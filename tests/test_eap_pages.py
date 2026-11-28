"""EAP directory: scripts/build_eap_pages.py renders data/eap into public/eap.

Builds into a temp folder and checks the rules the pages must keep: sources
and verified dates on every page, "ask HR" instead of a guess, no
passwords, and FAQPage JSON-LD built from the same fields.
"""
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_eap_pages", ROOT / "scripts" / "build_eap_pages.py")
build_eap_pages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_eap_pages)

DATA = ROOT / "data" / "eap" / "colorado-districts.json"


def _jsonld(page: str) -> dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    return json.loads(match.group(1))


class EapPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.public = Path(cls.tmp.name)
        build_eap_pages.build(DATA, cls.public)
        cls.entries = json.loads(DATA.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def page(self, slug):
        return (self.public / "eap" / f"{slug}.html").read_text()

    def test_a_page_for_every_district_with_has_eap_set(self):
        expected = {e["slug"] for e in self.entries if e["has_eap"] is not None} - build_eap_pages.REFERENCE_SLUGS
        built = {p.stem for p in (self.public / "eap").glob("*.html")} - {"index"}
        self.assertEqual(built, expected)
        self.assertNotIn("estes-park", built)  # has_eap is null

    def test_every_rendered_page_has_a_source_url_and_a_verified_date(self):
        for entry in self.entries:
            if not build_eap_pages.has_page(entry):
                continue
            page = self.page(entry["slug"])
            self.assertIn(f'href="{entry["official_source_urls"][0]}"', page, entry["slug"])
            self.assertIn(f'Last verified <time datetime="{entry["last_verified"]}">', page, entry["slug"])
        index = (self.public / "eap" / "index.html").read_text()
        self.assertIn("Last verified <time", index)

    def test_low_confidence_district_renders_ask_hr_and_no_guess(self):
        for slug in ("denver", "littleton"):
            page = self.page(slug)
            self.assertIn("We could not confirm this district&#x27;s EAP from a public source; ask HR.", page)
            self.assertNotIn("ComPsych", page)
            self.assertNotIn("Aetna", page)
        index = (self.public / "eap" / "index.html").read_text()
        self.assertRegex(index, r"Estes Park School District R-3 \(Estes Park\): We could not confirm")

    def test_null_confidence_is_treated_as_unconfirmed(self):
        entry = {**self.entries[0], "confidence": None}
        self.assertEqual(build_eap_pages.facts(entry)["answer"], build_eap_pages.ASK_HR)

    def test_the_script_fails_if_an_entry_lacks_official_source_urls(self):
        broken = [dict(e) for e in self.entries]
        broken[3]["official_source_urls"] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "districts.json"
            path.write_text(json.dumps(broken))
            with self.assertRaises(build_eap_pages.DataError):
                build_eap_pages.build(path, Path(tmp))

    def test_passwords_never_render_and_login_points_at_the_guide(self):
        for slug in ("westminster", "27j-brighton", "adams-14"):
            page = self.page(slug)
            self.assertNotRegex(page, r"(?i)password|username")
            self.assertNotIn("LFGSupport", page)
            self.assertIn("Login details are in your district", page)
        # Codes the districts publish for staff still show.
        self.assertIn("Company Web ID: SVVSD", self.page("st-vrain-valley"))
        self.assertIn("Group Code: dcsd", self.page("douglas-county"))

    def test_district_page_order(self):
        page = self.page("st-vrain-valley").split("<main", 1)[1]
        marks = [
            "In crisis? Call or text",
            "<h1>Does St. Vrain Valley School District RE-1J have an employee assistance program?</h1>",
            "Provider: ComPsych (GuidanceResources). Sessions: up to 6 free face-to-face visits per episode. "
            "Phone: 855-699-6908 (24/7). Log in: www.guidanceresources.com, Company Web ID: SVVSD.",
            "How sessions are counted is unclear.",
            "Last verified",
            "Making the most of your sessions",
            'href="/eap/guide"',
            'Memnon can carry what you learn in sessions into a short daily brief. <a href="/connect">',
        ]
        positions = [page.index(m) for m in marks]
        self.assertEqual(positions, sorted(positions))

    def test_faq_jsonld_is_built_from_the_same_fields(self):
        data = _jsonld(self.page("aurora"))
        self.assertEqual(data["@type"], "FAQPage")
        answers = [q["acceptedAnswer"]["text"] for q in data["mainEntity"]]
        self.assertIn("1-888-324-2631", answers[0])
        self.assertNotIn("888-628-4809", self.page("aurora"))  # the conflicting number stays in data
        self.assertEqual(answers[1], "5 counseling sessions per issue, per year.")
        low = _jsonld(self.page("denver"))
        self.assertEqual(len(low["mainEntity"]), 1)
        self.assertEqual(low["mainEntity"][0]["acceptedAnswer"]["text"], build_eap_pages.ASK_HR)

    def test_sitemap_lists_every_page(self):
        sitemap = (self.public / "sitemap.xml").read_text()
        self.assertIn("<loc>https://memnon.app/eap/st-vrain-valley</loc>", sitemap)
        self.assertIn("<loc>https://memnon.app/eap/guide</loc>", sitemap)
        self.assertNotIn("estes-park", sitemap)

    def test_committed_pages_match_the_data(self):
        for path in self.public.rglob("*"):
            if path.is_file():
                committed = ROOT / "public" / path.relative_to(self.public)
                self.assertEqual(committed.read_text(), path.read_text(), f"rerun scripts/build_eap_pages.py: {committed}")


class EapStaticFilesTests(unittest.TestCase):
    def test_robots_allows_search_bots_and_links_sitemap(self):
        robots = (ROOT / "public" / "robots.txt").read_text()
        for bot in ("OAI-SearchBot", "PerplexityBot", "Claude-SearchBot"):
            self.assertIn(f"User-agent: {bot}\nAllow: /", robots)
        self.assertNotIn("Disallow", robots)
        self.assertIn("Sitemap: https://memnon.app/sitemap.xml", robots)

    def test_guide_covers_consent_and_crisis(self):
        guide = (ROOT / "public" / "eap" / "guide.html").read_text()
        self.assertIn("988", guide)
        self.assertIn("Ask first. Always.", guide)
        self.assertIn("Colorado lets a person record a conversation they are part of", guide)
        self.assertIn("not legal or clinical advice", guide)


if __name__ == "__main__":
    unittest.main()
