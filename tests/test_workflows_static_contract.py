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

        self.assertIn("Capture a thought", html)
        self.assertIn("Speak, drop a file, or paste something messy.", html)
        self.assertIn("Turn it into something useful.", html)
        self.assertIn('id="workflows-app"', html)
        self.assertIn('id="capture-form"', html)
        self.assertIn('id="capture-surface"', html)
        self.assertIn('id="paste-panel"', html)
        self.assertIn('id="result-view"', html)
        self.assertIn('type="module" src="/workflows.js"', html)

    def test_workflows_js_contains_capture_and_result_api_paths(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        html = Path("public/workflows.html").read_text(encoding="utf-8")

        self.assertIn("/api/workflows/captures", js)
        self.assertIn("getIdToken", js)
        self.assertIn("View source text", html)

    def test_workflows_shell_exposes_signed_out_sign_in_path(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        html = Path("public/workflows.html").read_text(encoding="utf-8")

        self.assertIn("Sign in with Google", html)
        self.assertIn("Sign in required to save captures.", html)
        self.assertIn("/auth/start", js)
        self.assertIn("return_to", js)

    def test_signed_out_continue_can_resume_after_auth(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn("sessionStorage", js)
        self.assertIn("memnon_workflows_pending_capture_v1", js)
        self.assertIn("Sign in to continue.", js)


if __name__ == "__main__":
    unittest.main()
