import json
import re
import subprocess
import textwrap
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
        self.assertIn('id="capture-file"', html)
        self.assertIn('id="file-selection-state"', html)
        self.assertIn('id="clear-upload"', html)
        self.assertIn('id="result-view"', html)
        self.assertIn('id="loading-card"', html)
        self.assertIn('id="workflows-build-marker"', html)
        self.assertIn('id="workflows-debug-state"', html)
        self.assertIn('type="module" src="/workflows.js"', html)

    def test_workflows_js_contains_capture_and_result_api_paths(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        helper = Path("public/workflows_url_helpers.js").read_text(encoding="utf-8")
        html = Path("public/workflows.html").read_text(encoding="utf-8")

        self.assertIn("/api/workflows/captures", js)
        self.assertIn("/api/workflows/contexts", js)
        self.assertIn("http://127.0.0.1:5051", js)
        self.assertIn("canonicalizeAuthReturnUrl", js)
        self.assertIn("shouldShowLocalDebugUi", js)
        self.assertIn("workflows.js loaded", js)
        self.assertIn("Blocked unexpected navigation", js)
        self.assertIn('"127.0.0.1"', helper)
        self.assertIn('"localhost"', helper)
        self.assertIn("getIdToken", js)
        self.assertIn("View source text", html)

    def test_workflows_js_includes_voice_capture_flow(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn("MediaRecorder", js)
        self.assertIn("FormData", js)
        self.assertIn("audio/webm", js)
        self.assertIn("audio/mp4", js)
        self.assertIn("video/mp4", js)
        self.assertIn("Requesting microphone access...", js)
        self.assertIn("Recording...", js)
        self.assertIn("Stopping recording...", js)
        self.assertIn("Uploading voice note...", js)
        self.assertIn("No audio was captured. Try again.", js)
        self.assertIn("Microphone access was denied.", js)
        self.assertIn("That recording is too long for inline capture. Try a shorter note.", js)

    def test_workflows_shell_exposes_signed_out_sign_in_path(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        html = Path("public/workflows.html").read_text(encoding="utf-8")

        self.assertIn("Sign in with Google", html)
        self.assertIn("Draft now, sign in to save.", html)
        self.assertIn("/auth/start", js)
        self.assertIn("return_to", js)

    def test_signed_out_continue_can_resume_after_auth(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn("sessionStorage", js)
        self.assertIn("memnon_workflows_pending_capture_v1", js)
        self.assertIn("Continue and sign in to save", js)
        self.assertIn("Redirecting to sign in so you can save this draft.", js)
        self.assertNotIn("Sign in to continue.", js)

    def test_localhost_fallback_copy_does_not_leak_developer_language(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertNotIn("Loaded a local demo result because the live workflows API is not available on localhost.", js)
        self.assertNotIn("Created locally because the live workflows API is not available on localhost.", js)

    def test_ui_copy_includes_product_states_and_not_backend_language(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn("Shaping this into a next step...", js)
        self.assertIn("Something went wrong. Try again.", js)
        self.assertIn("Saved for later", js)
        self.assertIn("Kept as a saved note", js)
        self.assertIn("This is a small note worth preserving.", js)
        self.assertIn("This seems worth keeping, but it may need a little direction before it becomes something stronger.", js)
        self.assertIn("Saved and shaped", js)
        self.assertIn("Saved result", js)
        self.assertIn("Next step", js)
        self.assertIn("Key point", js)
        self.assertIn("Why keep this", js)
        self.assertIn("From your note", js)
        self.assertIn("artifact.metadata_line", js)
        self.assertNotIn("${renderThemes(themes)}", js)

    def test_local_debug_mode_uses_explicit_sign_in_href_and_hides_prompt(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn("signInHref", js)
        self.assertIn('prompt.style.display = "none"', js)

    def test_screen_visibility_is_explicit_and_non_interactive(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn("captureForm.inert = !showCapture", js)
        self.assertIn('captureForm.style.display = showCapture ? "" : "none"', js)
        self.assertIn('resultView.style.display = showCapture ? "none" : "grid"', js)

    def test_start_another_capture_resets_the_form_surface(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn('input.value = ""', js)
        self.assertIn('context.value = ""', js)
        self.assertIn("setPastePanelVisible(false)", js)
        self.assertIn("resetCaptureForm();", js)

    def test_hidden_result_cards_are_forced_not_to_render(self):
        css = Path("public/workflows.css").read_text(encoding="utf-8")

        self.assertIn("[hidden]", css)
        self.assertIn("display: none !important;", css)

    def test_metadata_line_uses_local_date_formatting(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn("toLocaleDateString", js)
        self.assertNotIn("Just now", js)

    def test_context_hint_does_not_render_as_thread_like_metadata(self):
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");

            const source = fs.readFileSync("public/workflows.js", "utf8");

            function extractBetween(startMarker, endMarker) {
              const start = source.indexOf(startMarker);
              if (start === -1) {
                throw new Error(`missing start marker: ${startMarker}`);
              }
              const end = source.indexOf(endMarker, start);
              if (end === -1) {
                throw new Error(`missing end marker: ${endMarker}`);
              }
              return source.slice(start, end);
            }

            const snippets = [
              "const SAVED_RESULTS_PATH = '/workflows/saved';",
              extractBetween("function escapeHtml", "function setStatus"),
              extractBetween("function formatLocalCaptureDate", "function describeSourceType"),
              extractBetween("function describeSourceType", "function buildMetadataLine"),
              extractBetween("function buildMetadataLine", "function renderSourceExcerpt"),
              extractBetween("function renderConfirmedThreadDisplay", "function renderSections"),
            ].join("\\n");

            const context = {};
            vm.createContext(context);
            vm.runInContext(snippets, context);

            const metadataOnlyHint = context.buildMetadataLine({
              input_type: "text",
              created_at: "2026-06-27T16:00:00Z",
              context_hint: "workflows ui/ux",
            });
            const confirmedThread = context.renderConfirmedThreadDisplay({
              result: { related_thread: { confirmed_title: "Workflows UI/UX" } },
              threading: { confirmed_context_id: "ctx-1", context_decision: "confirmed" },
            });

            if (metadataOnlyHint !== "Pasted note · Jun 27, 2026") {
              throw new Error(`unexpected metadata line: ${metadataOnlyHint}`);
            }
            if (!confirmedThread.includes("Related to Workflows UI/UX")) {
              throw new Error(`missing confirmed thread copy: ${confirmedThread}`);
            }
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_uploaded_file_helpers_prefer_file_as_active_source_and_show_filename(self):
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");

            const source = fs.readFileSync("public/workflows.js", "utf8");

            function extractBetween(startMarker, endMarker) {
              const start = source.indexOf(startMarker);
              if (start === -1) {
                throw new Error(`missing start marker: ${startMarker}`);
              }
              const end = source.indexOf(endMarker, start);
              if (end === -1) {
                throw new Error(`missing end marker: ${endMarker}`);
              }
              return source.slice(start, end);
            }

            const snippets = [
              extractBetween("function formatLocalCaptureDate", "function describeSourceType"),
              extractBetween("function normalizedUploadExtension", "function syncAuthPrompt"),
              extractBetween("function describeSourceType", "function buildMetadataLine"),
              extractBetween("function buildMetadataLine", "function renderSourceExcerpt"),
              extractBetween("function renderSourceExcerpt", "function renderThreadChooser"),
            ].join("\\n");

            const context = {};
            vm.createContext(context);
            vm.runInContext(snippets, context);

            const metadata = context.buildMetadataLine({
              input_type: "file",
              created_at: "2026-06-27T16:00:00Z",
              source_filename: "plan.md",
            });
            const label = context.buildSourceExcerptLabel({
              input_type: "file",
              source_filename: "plan.md",
            });
            const fileActive = context.resolveCaptureSource({
              text: "This pasted text should stay quiet.",
              selectedFile: { name: "plan.md", size: 120 },
            });
            const textActive = context.resolveCaptureSource({
              text: "This pasted text should submit.",
              selectedFile: null,
            });

            if (metadata !== "Uploaded file · plan.md · Jun 27, 2026") {
              throw new Error(`unexpected file metadata: ${metadata}`);
            }
            if (label !== "From plan.md") {
              throw new Error(`unexpected source excerpt label: ${label}`);
            }
            if (fileActive.activeSource !== "file" || !fileActive.message.includes("won't be submitted")) {
              throw new Error(`unexpected file active state: ${JSON.stringify(fileActive)}`);
            }
            if (textActive.activeSource !== "text") {
              throw new Error(`unexpected text active state: ${JSON.stringify(textActive)}`);
            }
          """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_reopened_result_metadata_prefers_payload_source_context(self):
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");

            const source = fs.readFileSync("public/workflows.js", "utf8");

            function extractBetween(startMarker, endMarker) {
              const start = source.indexOf(startMarker);
              if (start === -1) {
                throw new Error(`missing start marker: ${startMarker}`);
              }
              const end = source.indexOf(endMarker, start);
              if (end === -1) {
                throw new Error(`missing end marker: ${endMarker}`);
              }
              return source.slice(start, end);
            }

            const snippets = [
              'const SOURCE_EXCERPT_LABEL = "From your note";',
              extractBetween("function formatLocalCaptureDate", "function describeSourceType"),
              extractBetween("function describeSourceType", "function buildMetadataLine"),
              extractBetween("function buildMetadataLine", "function buildSourceExcerptLabel"),
              extractBetween("function buildSourceExcerptLabel", "function renderSourceExcerpt"),
              extractBetween("function renderResultFeedback", "function renderResultCard"),
            ].join("\\n");

            const context = {};
            vm.createContext(context);
            vm.runInContext(snippets, context);

            const reopenedFilePayload = {
              input_type: "file",
              created_at: "2026-06-27T16:00:00Z",
              source_event: {
                created_at: "2026-06-27T16:00:00Z",
                source_text: "Draft the file-backed note.",
              },
              event_manifest: {
                source_event: {
                  input_type: "file",
                  source_filename: "plan.md",
                },
              },
            };
            const reopenedTextPayload = {
              input_type: "text",
              created_at: "2026-06-27T16:00:00Z",
              source_event: {
                created_at: "2026-06-27T16:00:00Z",
                source_text: "Draft the pasted note.",
              },
            };
            const reopenedVoicePayload = {
              input_type: "voice",
              created_at: "2026-06-27T16:00:00Z",
              source_event: {
                created_at: "2026-06-27T16:00:00Z",
                source_text: "Draft the voice note.",
              },
            };

            const fileMetadata = context.resolveResultMetadataLine(
              reopenedFilePayload,
              { metadata_line: "Saved note · Jun 27, 2026" },
            );
            const fileLabel = context.buildSourceExcerptLabel(
              context.resolveResultSourceEvent(reopenedFilePayload),
            );
            const textMetadata = context.resolveResultMetadataLine(
              reopenedTextPayload,
              { metadata_line: "Pasted note · Jun 27, 2026" },
            );
            const voiceMetadata = context.resolveResultMetadataLine(
              reopenedVoicePayload,
              { metadata_line: "Voice note · Jun 27, 2026" },
            );
            const reopenedFeedback = context.renderResultFeedback(
              { capture_id: "cap-1", feedback_choice: null },
              { isImmediateResult: false },
            );

            if (fileMetadata !== "Uploaded file · plan.md · Jun 27, 2026") {
              throw new Error(`unexpected reopened file metadata: ${fileMetadata}`);
            }
            if (fileLabel !== "From plan.md") {
              throw new Error(`unexpected reopened file label: ${fileLabel}`);
            }
            if (textMetadata !== "Pasted note · Jun 27, 2026") {
              throw new Error(`unexpected reopened text metadata: ${textMetadata}`);
            }
            if (voiceMetadata !== "Voice note · Jun 27, 2026") {
              throw new Error(`unexpected reopened voice metadata: ${voiceMetadata}`);
            }
            if (reopenedFeedback.trim() !== "") {
              throw new Error(`reopened feedback should stay hidden: ${reopenedFeedback}`);
            }
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_result_route_thread_controls_follow_immediate_result_rules(self):
        css = Path("public/workflows.css").read_text(encoding="utf-8")
        self.assertIn("workflows-thread-chooser", css)
        self.assertIn("workflows-thread-option", css)
        self.assertIn("workflows-thread-create-form", css)
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");

            const source = fs.readFileSync("public/workflows.js", "utf8");

            function extractBetween(startMarker, endMarker) {
              const start = source.indexOf(startMarker);
              if (start === -1) {
                throw new Error(`missing start marker: ${startMarker}`);
              }
              const end = source.indexOf(endMarker, start);
              if (end === -1) {
                throw new Error(`missing end marker: ${endMarker}`);
              }
              return source.slice(start, end);
            }

            const snippets = [
              extractBetween("function escapeHtml", "function setStatus"),
              extractBetween("function renderThreadChooser", "function isImmediateResultNavigation"),
              extractBetween("function isImmediateResultNavigation", "function renderRelatedThreadSuggestion"),
              extractBetween("function renderRelatedThreadSuggestion", "function renderConfirmedThreadDisplay"),
              extractBetween("function renderConfirmedThreadDisplay", "function renderSections"),
            ].join("\\n");

            const context = {};
            vm.createContext(context);
            vm.runInContext(snippets, context);

            const immediateEligible = context.renderRelatedThreadSuggestion(
              {
                result: {
                  related_thread: {
                    suggested_title: "Workflows UI/UX",
                    suggestion_active: true,
                  },
                },
                threading: { suggestion_active: true },
              },
              [{ context_id: "ctx-1", title: "Workflows UI/UX" }],
            );
            const chooserOnly = context.renderThreadChooser([{ context_id: "ctx-1", title: "Workflows UI/UX" }]);
            const reopenedNoControls = context.renderRelatedThreadSuggestion(
              { result: { related_thread: {} }, threading: {} },
              [{ context_id: "ctx-1", title: "Workflows UI/UX" }],
            );
            const staleRelatedThreadSignal = context.renderRelatedThreadSuggestion(
              {
                result: {
                  related_thread: {
                    suggested_title: "Workflows UI/UX",
                    suggestion_active: true,
                  },
                },
                threading: {},
              },
              [{ context_id: "ctx-1", title: "Workflows UI/UX" }],
            );
            const immediateNavigation = context.isImmediateResultNavigation({
              result: { related_thread: { suggested_title: "Workflows UI/UX", suggestion_active: true } },
              threading: { suggestion_active: true },
            });
            const reopenedNavigation = context.isImmediateResultNavigation({
              result: { related_thread: { confirmed_title: "Workflows UI/UX" } },
              threading: { confirmed_context_id: "ctx-1", context_decision: "confirmed" },
            });
            const reopenedConfirmed = context.renderConfirmedThreadDisplay(
              {
                result: { related_thread: { confirmed_title: "Workflows UI/UX" } },
                threading: { confirmed_context_id: "ctx-1", context_decision: "confirmed" },
              },
            );
            const decidedSeparate = context.renderRelatedThreadSuggestion(
              { result: { related_thread: {} }, threading: { context_decision: "kept_separate" } },
              [{ context_id: "ctx-1", title: "Workflows UI/UX" }],
            );

            const assertions = [
              immediateEligible.includes("This looks related to Workflows UI/UX.")
                && immediateEligible.includes("Continue there")
                && immediateEligible.includes("Not this")
                && immediateEligible.includes("workflows-related-thread-escape")
                && immediateEligible.includes("hidden")
                && !immediateEligible.includes("Choose another")
                && !immediateEligible.includes("Create new thread"),
              chooserOnly.includes("Create new thread")
                && chooserOnly.includes("Create")
                && chooserOnly.includes("data-create-context"),
              reopenedNoControls.trim() === "",
              staleRelatedThreadSignal.trim() === "",
              immediateNavigation === true,
              reopenedNavigation === false,
              reopenedConfirmed.includes("Workflows UI/UX")
                && reopenedConfirmed.includes("Related to Workflows UI/UX"),
              decidedSeparate.trim() === "",
            ];

            if (assertions.some((result) => !result)) {
              throw new Error(JSON.stringify({
                immediateEligible,
                reopenedNoControls,
                staleRelatedThreadSignal,
                reopenedConfirmed,
                decidedSeparate,
              }));
            }
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("workflows-related-thread-escape", css)

    def test_feedback_prompt_is_immediate_only_and_binary(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        self.assertIn("How was this result?", js)
        self.assertIn("Useful", js)
        self.assertIn("Not useful", js)

        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");

            const source = fs.readFileSync("public/workflows.js", "utf8");

            function extractBetween(startMarker, endMarker) {
              const start = source.indexOf(startMarker);
              if (start === -1) {
                throw new Error(`missing start marker: ${startMarker}`);
              }
              const end = source.indexOf(endMarker, start);
              if (end === -1) {
                throw new Error(`missing end marker: ${endMarker}`);
              }
              return source.slice(start, end);
            }

            const snippets = [
              extractBetween("function escapeHtml", "function setStatus"),
              extractBetween("function renderResultFeedback", "function renderResultCard"),
              extractBetween("function renderSavedResultsBody", "function renderSavedResultsList"),
            ].join("\\n");

            const context = {};
            vm.createContext(context);
            vm.runInContext(snippets, context);

            const immediate = context.renderResultFeedback(
              { capture_id: "cap-1", feedback_choice: null },
              { isImmediateResult: true },
            );
            const reopened = context.renderResultFeedback(
              { capture_id: "cap-1", feedback_choice: null },
              { isImmediateResult: false },
            );
            const savedList = context.renderSavedResultsBody([
              { capture_id: "cap-1", title: "Saved note", next_route: "/workflows/result/cap-1", metadata_line: "Pasted note" },
            ]);

            if (!immediate.includes("How was this result?") || !immediate.includes("Useful") || !immediate.includes("Not useful")) {
              throw new Error(`immediate feedback missing: ${immediate}`);
            }
            if (reopened.trim() !== "") {
              throw new Error(`reopened feedback should be hidden: ${reopened}`);
            }
            if (savedList.includes("How was this result?")) {
              throw new Error(`saved results list should not include feedback prompt: ${savedList}`);
            }
            """
        )
        completed = subprocess.run(
            ["node", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_immediate_thread_decision_rerenders_preserve_feedback_state(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        thread_controls_start = js.index("function wireResultThreadControls")
        feedback_controls_start = js.index("function wireResultFeedbackControls", thread_controls_start)
        thread_controls = js[thread_controls_start:feedback_controls_start]

        rerender_matches = re.findall(
            r"renderPayload\(updated,\s*\{\s*activeThreads:\s*\[\],\s*isImmediateResult:\s*true,\s*\}\s*\);",
            thread_controls,
        )

        self.assertGreaterEqual(
            len(rerender_matches),
            4,
            "Immediate thread-decision rerenders should preserve isImmediateResult for feedback UI.",
        )


if __name__ == "__main__":
    unittest.main()
