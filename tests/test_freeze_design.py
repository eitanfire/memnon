import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "freeze_design", Path(__file__).resolve().parents[1] / "scripts" / "freeze_design.py"
)
fd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fd)


class FreezeDesignRewriteTests(unittest.TestCase):
    def test_strip_scripts_removes_scripts_handlers_and_manifest(self):
        html = (
            '<head><link rel="manifest" href="/manifest.json"><script type="module">x()</script>'
            '<script src="/a.js"></script></head><body><button onclick="go()">Go</button></body>'
        )
        out = fd.strip_scripts(html)
        self.assertNotIn("<script", out)
        self.assertNotIn("onclick", out)
        self.assertNotIn("manifest", out)
        self.assertIn("<button>Go</button>", out)

    def test_neutralize_links_makes_page_links_inert(self):
        out = fd.neutralize_links('<a class="x" href="/setup#a">Setup</a><a href="https://x.org">X</a>')
        self.assertIn('href="#" data-live-href="/setup#a"', out)
        self.assertIn('href="https://x.org"', out)

    def test_copy_assets_uses_shared_hashed_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "assets"
            out_dir = Path(tmp) / "projects" / "p" / "v"
            out_dir.mkdir(parents=True)
            original = fd.ASSET_STORE
            fd.ASSET_STORE = store
            try:
                html = '<img src="/favicon.ico"><div style="background:url(\'/favicon.ico\')"></div>'
                out = fd.copy_assets(html, out_dir)
            finally:
                fd.ASSET_STORE = original
            copied = list(store.iterdir())
            self.assertEqual(len(copied), 1)
            self.assertRegex(copied[0].name, r"^favicon\.[0-9a-f]{10}\.ico$")
            self.assertEqual(out.count(f"../../../assets/{copied[0].name}"), 2)

    def test_local_file_refuses_paths_outside_public(self):
        self.assertIsNone(fd.local_file("/../README.md"))
        self.assertIsNone(fd.local_file("//cdn.example.com/x.png"))
        self.assertIsNotNone(fd.local_file("/favicon.ico?v=3"))

    def test_manifest_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = fd.MANIFEST
            fd.MANIFEST = Path(tmp) / "manifest.js"
            try:
                data = {"projects": [{"id": "x", "name": "X", "description": "", "versions": []}]}
                fd.save_manifest(data)
                self.assertIn("window.GALLERY = {", fd.MANIFEST.read_text())
                self.assertEqual(fd.load_manifest(), data)
            finally:
                fd.MANIFEST = original


if __name__ == "__main__":
    unittest.main()
