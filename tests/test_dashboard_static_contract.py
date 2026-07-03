import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
