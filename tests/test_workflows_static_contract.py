import json
import unittest
from pathlib import Path


class WorkflowsStaticContractTests(unittest.TestCase):
    def test_firebase_routes_workflows_paths_to_workflows_html(self):
        payload = json.loads(Path("firebase.json").read_text(encoding="utf-8"))
        rewrites = payload["hosting"]["rewrites"]

        self.assertIn(
            {
                "source": "workflows{,/**}",
                "destination": "/workflows.html",
            },
            rewrites,
        )

    def test_workflows_shell_has_required_copy_and_mount_points(self):
        html = Path("public/workflows.html").read_text(encoding="utf-8")

        self.assertIn("Memnon Workflows", html)
        self.assertIn("Capture a thought. Turn it into a useful next step.", html)
        self.assertIn('id="workflows-app"', html)
        self.assertIn('id="capture-form"', html)
        self.assertIn('id="result-view"', html)
        self.assertIn('type="module" src="/workflows.js"', html)

    def test_workflows_js_contains_capture_and_result_api_paths(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        html = Path("public/workflows.html").read_text(encoding="utf-8")

        self.assertIn("/api/workflows/captures", js)
        self.assertIn("getIdToken", js)
        self.assertIn("View source text", html)


if __name__ == "__main__":
    unittest.main()
