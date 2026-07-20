import unittest
from pathlib import Path


REPO_ROOT = Path("/Users/eitan/memnon")
SETUP_PATH = REPO_ROOT / "public" / "setup.html"


class SetupStaticContractTests(unittest.TestCase):
    def test_setup_uses_context_settings_language(self):
        html = SETUP_PATH.read_text(encoding="utf-8")

        self.assertIn("<title>Context Settings - Memnon</title>", html)
        self.assertIn(">Context Settings<", html)
        self.assertIn(
            "Adjust the context Memnon can draw on when shaping your captures.",
            html,
        )
        self.assertNotIn("Tune Reflection", html)
        self.assertNotIn(
            "Adjust how Memnon helps you reflect and make sense of your experience.",
            html,
        )

    def test_setup_scopes_reflective_response_style_without_picker_language(self):
        html = SETUP_PATH.read_text(encoding="utf-8")

        self.assertIn("Reflective Response Style", html)
        self.assertIn(
            "Choose the default shape Memnon uses when a result calls for reflection or synthesis.",
            html,
        )
        self.assertNotIn("Choose what comes back after you record.", html)
        self.assertNotIn("Choose a workflow", html)
        self.assertNotIn("Select output type", html)
        self.assertNotIn("social post", html)
        self.assertNotIn("meeting notes", html)
        self.assertNotIn("mind maps", html)

    def test_setup_keeps_teaching_context_as_standing_relevant_context(self):
        html = SETUP_PATH.read_text(encoding="utf-8")

        self.assertIn("Teaching Context", html)
        self.assertIn(
            "Classroom, school, subject, and standards Memnon can use when relevant.",
            html,
        )

    def test_setup_privacy_copy_uses_capture_and_result_language(self):
        html = SETUP_PATH.read_text(encoding="utf-8")

        self.assertIn("Contribute anonymized product signals", html)
        self.assertIn(
            "Leave this off if you want your captures and results used only for your own return.",
            html,
        )
        self.assertIn(
            "If you turn it on, Memnon may use high-level product metadata in aggregate.",
            html,
        )
        self.assertIn(
            "Your capture text does not appear verbatim in founder research views.",
            html,
        )
        self.assertNotIn("reflection signals", html)
        self.assertNotIn("reflection metadata", html)

    def test_setup_updates_today_surface_labels(self):
        html = SETUP_PATH.read_text(encoding="utf-8")

        self.assertIn("Today Portrait", html)
        self.assertNotIn("Dashboard Portrait", html)
        self.assertIn("Save and go to Today", html)
        self.assertNotIn("Save and go to dashboard", html)


if __name__ == "__main__":
    unittest.main()
