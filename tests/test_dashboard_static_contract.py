import unittest
from pathlib import Path
import subprocess


REPO_ROOT = Path("/Users/eitan/memnon")
DASHBOARD_PATH = REPO_ROOT / "public" / "dashboard.html"


class DashboardStaticContractTests(unittest.TestCase):
    def test_dashboard_capture_posts_to_workflows_capture_endpoint(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn("/api/workflows/captures", html)
        self.assertNotIn("/text-reflection", html)
        self.assertNotIn("/upload", html)

    def test_dashboard_success_flow_redirects_to_saved_result_route(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn("/workflows/result/", html)
        self.assertIn("window.location.assign", html)

    def test_dashboard_keeps_legacy_teaching_context_ui_without_new_lane_copy(self):
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn("Use teaching context", html)
        self.assertNotIn("Choose a workflow", html)
        self.assertNotIn("Choose a note type", html)
        self.assertNotIn("reflection or workflow", html)

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
