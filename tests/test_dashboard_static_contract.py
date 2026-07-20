import unittest
from pathlib import Path
import subprocess


REPO_ROOT = Path("/Users/eitan/memnon")
DASHBOARD_PATH = REPO_ROOT / "public" / "dashboard.html"


class DashboardStaticContractTests(unittest.TestCase):
    def test_service_worker_only_intercepts_share_target_posts(self):
        sw = (REPO_ROOT / "public" / "sw.js").read_text(encoding="utf-8")

        self.assertIn('url.pathname !== "/share-target" || event.request.method !== "POST"', sw)
        self.assertIn("event.respondWith(handleShareTarget(event.request));", sw)
        self.assertNotIn("event.respondWith(fetch(event.request))", sw)

    def test_dashboard_is_framed_as_today(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn("<title>Memnon Today</title>", html)
        self.assertIn(">Today<", html)
        self.assertIn("Daily Brief", html)

    def test_dashboard_links_to_core_capture_without_owning_capture_form(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn('href="/workflows"', html)
        self.assertIn("Open capture", html)
        self.assertNotIn('id="record-btn"', html)
        self.assertNotIn('id="write-btn"', html)
        self.assertNotIn('id="capture-text-panel"', html)
        self.assertNotIn('id="capture-text-save"', html)

    def test_dashboard_replaces_reflection_context_panel_with_context_settings_link(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn("Context settings", html)
        self.assertNotIn("Reflection Context", html)
        self.assertNotIn("next reflection", html)
        self.assertNotIn("Use teaching context", html)
        self.assertNotIn("Teaching context: On", html)
        self.assertNotIn("Teaching context:", html)
        self.assertNotIn("Voices:", html)
        self.assertNotIn("Frameworks:", html)
        self.assertNotIn("Mode: Complete reflection", html)
        self.assertNotIn("Tune Reflection", html)
        self.assertNotIn("Choose a workflow", html)
        self.assertNotIn("Choose a note type", html)
        self.assertNotIn("reflection or workflow", html)

    def test_dashboard_uses_latest_result_language_for_latest_return_surface(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn("Latest result", html)
        self.assertIn("Latest complete result", html)
        self.assertIn("Loading your latest result…", html)
        self.assertIn("Loading the latest result text…", html)
        self.assertIn("Your latest result text will appear here after processing.", html)
        self.assertNotIn("Latest reflection", html)
        self.assertNotIn("Latest complete reflection", html)
        self.assertNotIn("Loading your latest reflection…", html)
        self.assertNotIn("Loading the latest reflection text…", html)
        self.assertNotIn("Your latest reflection text will appear here after processing.", html)

    def test_share_target_audio_falls_back_to_upload_status_when_share_status_is_hidden(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        start = html.index("async function checkSharedFile()")
        end = html.index("function getSharedFileFromIDB()", start)
        snippet = html[start:end]

        script = f"""
const vm = require("vm");
const uploadStatus = {{ style: {{}}, textContent: "" }};
const context = {{
  window: {{ location: {{ search: "?shared=1" }} }},
  history: {{ replaceState: () => {{}} }},
  routes: {{ dashboard: "/dashboard" }},
  document: {{
    getElementById(id) {{
      if (id === "share-status") return null;
      if (id === "upload-status") return uploadStatus;
      return null;
    }},
  }},
  getSharedFileFromIDB: async () => null,
  uploadAudio: async () => {{}},
}};
vm.createContext(context);
vm.runInContext({snippet!r}, context);
(async () => {{
  await context.checkSharedFile();
  if (uploadStatus.textContent !== "⚠️ No shared file found.") {{
    throw new Error(`unexpected status text: ${{uploadStatus.textContent}}`);
  }}
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
        completed = subprocess.run(
            ["node", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
