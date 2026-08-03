import json
import re
import subprocess
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path("/Users/eitan/memnon")
TODAY_PATH = REPO_ROOT / "public" / "today.html"


class TodayStaticContractTests(unittest.TestCase):
    # ── Core/Today Consolidation v1: routing and single-component structure ──

    def test_workflows_html_is_retired(self):
        self.assertFalse(
            (REPO_ROOT / "public" / "workflows.html").exists(),
            "workflows.html must be deleted, not left as an unreachable stub "
            "(docs/superpowers/specs/2026-08-01-memnon-core-today-consolidation-v1.md §9)",
        )

    def test_dashboard_html_is_retired(self):
        self.assertFalse(
            (REPO_ROOT / "public" / "dashboard.html").exists(),
            "dashboard.html is renamed/merged into today.html, not left behind as a second file",
        )

    def test_firebase_redirects_old_routes_to_today(self):
        payload = json.loads(Path("firebase.json").read_text(encoding="utf-8"))
        redirects = payload["hosting"]["redirects"]

        self.assertIn({"source": "/dashboard", "destination": "/today", "type": 301}, redirects)
        self.assertIn({"source": "/workflows", "destination": "/today", "type": 301}, redirects)
        self.assertIn(
            {"source": "/workflows/:path*", "destination": "/today/:path*", "type": 301},
            redirects,
        )

    def test_firebase_routes_today_paths_to_today_html(self):
        payload = json.loads(Path("firebase.json").read_text(encoding="utf-8"))
        rewrites = payload["hosting"]["rewrites"]

        self.assertIn(
            {"source": "today{,/**}", "destination": "/today.html"},
            rewrites,
        )
        # The old workflows{,/**} rewrite must be gone -- that route redirects now,
        # it doesn't serve content, so a stale rewrite rule would mean dual-serving.
        self.assertNotIn(
            {"source": "workflows{,/**}", "destination": "/workflows.html"},
            rewrites,
        )

    def test_hosting_cache_headers_force_revalidation_for_today_and_capture_assets(self):
        payload = json.loads(Path("firebase.json").read_text(encoding="utf-8"))
        header_rules = payload["hosting"].get("headers", [])

        actual = {
            rule["source"]: {
                header["key"]: header["value"]
                for header in rule.get("headers", [])
            }
            for rule in header_rules
        }

        expected = {
            "today{,/**}": "no-cache, max-age=0, must-revalidate",
            "today.html": "no-cache, max-age=0, must-revalidate",
            "workflows.js": "no-cache, max-age=0, must-revalidate",
            "workflows_url_helpers.js": "no-cache, max-age=0, must-revalidate",
            "workflows.css": "no-cache, max-age=0, must-revalidate",
            "manifest.json": "no-cache, no-store, must-revalidate",
            "sw.js": "no-cache, no-store, must-revalidate",
        }

        for source, cache_control in expected.items():
            self.assertIn(source, actual, f"missing hosting header rule for {source}")
            self.assertEqual(
                actual[source].get("Cache-Control"),
                cache_control,
                f"unexpected Cache-Control for {source}",
            )

    def test_today_page_contains_both_sections_structurally_separated(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        today_index = html.index('class="dashboard-capture-title">Today<')
        capture_index = html.index('id="workflows-app"')
        self.assertLess(today_index, capture_index, "Today section must precede the Capture section in document order")

        # The Capture section must be visually demoted, not a co-equal panel --
        # this is the class the section-break CSS hooks into.
        self.assertIn('id="workflows-app" class="workflows-shell workflows-section-secondary"', html)

    def test_capture_section_uses_the_same_literal_component_not_a_rebuild(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        # Every mount point workflows.js queries must be present verbatim --
        # this is the "same literal component, not a duplicated implementation"
        # constraint from the consolidation spec, §6 and §10.
        for element_id in (
            "capture-form",
            "capture-surface",
            "record-trigger",
            "upload-trigger",
            "show-paste",
            "capture-file",
            "file-selection-state",
            "clear-upload",
            "paste-panel",
            "capture-text",
            "capture-context",
            "capture-submit",
            "workflows-status",
            "workflows-auth-prompt",
            "workflows-signin",
            "result-view",
            "loading-card",
            "primary-artifact-card",
            "saved-note-card",
            "source-text-panel",
            "source-text-content",
        ):
            self.assertIn(f'id="{element_id}"', html, f"missing capture component mount point: {element_id}")

        # Exactly one capture form on the page -- not a second/rebuilt implementation.
        self.assertEqual(html.count('id="capture-form"'), 1)
        self.assertEqual(html.count('id="record-trigger"'), 1)

        self.assertIn('type="module" src="/workflows.js"', html)
        self.assertIn('href="/workflows.css"', html)

    def test_today_open_capture_button_opens_in_place_not_a_page_navigation(self):
        html = TODAY_PATH.read_text(encoding="utf-8")
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        # The single record button lives with Today, immediately -- and activating
        # it must not navigate to a different page (spec §5).
        self.assertIn('id="today-open-capture"', html)
        self.assertNotIn('id="today-open-capture" class="btn btn-primary capture-mode-btn" href="/workflows"', html)
        self.assertIn('href="#workflows-app"', html)
        self.assertIn("today-open-capture", js)
        self.assertIn("focusCaptureComponent", js)
        self.assertIn("event.preventDefault()", js)

    def test_deep_link_routes_scroll_to_capture_section_not_today_top(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        # §4a: /today/result/:id and /today/saved must land directly on that
        # content, not require a scroll past Today's orientation content.
        self.assertIn("landOnCaptureSection", js)
        self.assertIn("scrollIntoView", js)
        handler_start = js.index("async function handleCurrentRoute")
        handler_end = js.index("\n}", js.index("mountWorkflowsApp"))
        handler_body = js[handler_start:handler_end]
        self.assertIn("landOnCaptureSection();", handler_body)

    def test_route_paths_point_at_today_not_workflows(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn('"/today/saved"', js)
        self.assertIn("/today/result/", js)
        self.assertNotIn('"/workflows/saved"', js)
        self.assertNotIn("/workflows/result/", js)

        parse_route_start = js.index("function parseWorkflowsRoute")
        parse_route_end = js.index("\n}", parse_route_start)
        parse_route_body = js[parse_route_start:parse_route_end]
        self.assertIn('"/today.html"', parse_route_body)
        self.assertIn('|| "/today"', parse_route_body)
        self.assertIn(r"\/today\/result\/", parse_route_body)

    def test_firebase_init_guards_against_duplicate_app(self):
        # Today's own inline script and workflows.js both touch Firebase; on one
        # page they must not both call initializeApp() unconditionally.
        html = TODAY_PATH.read_text(encoding="utf-8")
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        for source, label in ((html, "today.html"), (js, "workflows.js")):
            self.assertIn("getApps().length ? getApp() : initializeApp(firebaseConfig)", source, f"{label} missing Firebase duplicate-app guard")

    def test_backend_next_route_points_at_today(self):
        blueprint = (REPO_ROOT / "functions" / "workflows" / "blueprint.py").read_text(encoding="utf-8")
        service = (REPO_ROOT / "functions" / "workflows" / "service.py").read_text(encoding="utf-8")
        main = (REPO_ROOT / "functions" / "main.py").read_text(encoding="utf-8")

        for source, label in ((blueprint, "blueprint.py"), (service, "service.py"), (main, "main.py")):
            self.assertNotIn("/workflows/result/", source, f"{label} still generates the old page route")
            self.assertIn("/today/result/", source, f"{label} should generate the new page route")

    # ── Service worker (unchanged by this milestone) ──

    def test_service_worker_only_intercepts_share_target_posts(self):
        sw = (REPO_ROOT / "public" / "sw.js").read_text(encoding="utf-8")

        self.assertIn('url.pathname !== "/share-target" || event.request.method !== "POST"', sw)
        self.assertIn("event.respondWith(handleShareTarget(event.request));", sw)
        self.assertNotIn("event.respondWith(fetch(event.request))", sw)

    # ── Today section framing (carried over from dashboard contract) ──

    def test_today_is_framed_as_today(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        self.assertIn("<title>Memnon Today</title>", html)
        self.assertIn(">Today<", html)
        self.assertIn("Daily Brief", html)

    def test_today_replaces_reflection_context_panel_with_context_settings_link(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

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

    def test_today_uses_latest_result_language_for_latest_return_surface(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

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

    def test_today_does_not_reacquire_management_dashboard_features(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        # Guarded explicitly in the consolidation spec §6 -- the merged page
        # must not read as a management dashboard/control center.
        self.assertNotIn("Manage", html)
        self.assertNotIn('id="analytics-panel"', html)
        self.assertNotIn('id="review-queue"', html)

    def test_share_target_audio_falls_back_to_upload_status_when_share_status_is_hidden(self):
        html = TODAY_PATH.read_text(encoding="utf-8")
        start = html.index("async function checkSharedFile()")
        end = html.index("function getSharedFileFromIDB()", start)
        snippet = html[start:end]

        script = f"""
const vm = require("vm");
const uploadStatus = {{ style: {{}}, textContent: "" }};
const context = {{
  window: {{ location: {{ search: "?shared=1" }} }},
  history: {{ replaceState: () => {{}} }},
  routes: {{ today: "/today" }},
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

    # ── Capture surface copy and mount points (carried over from workflows contract) ──

    def test_workflows_shell_has_required_copy_and_mount_points(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        self.assertIn(">Capture<", html)
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

    def test_saved_results_copy_uses_neutral_result_language(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        self.assertIn("Saved results", js)
        self.assertIn("Reopen a saved result.", js)
        self.assertNotIn("Saved workflow results", js)
        self.assertNotIn("saved workflow artifact", js)

    def test_workflows_js_contains_capture_and_result_api_paths(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        helper = Path("public/workflows_url_helpers.js").read_text(encoding="utf-8")
        html = TODAY_PATH.read_text(encoding="utf-8")

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
        self.assertIn("response.blob()", js)
        self.assertIn("URL.createObjectURL", js)
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
        html = TODAY_PATH.read_text(encoding="utf-8")

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
        self.assertIn("This note is worth keeping, but it needs clearer direction before acting on it.", js)
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
              "const SAVED_RESULTS_PATH = '/today/saved';",
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

            if (metadata !== "Uploaded file · Jun 27, 2026") {
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
              extractBetween("function renderFeedbackNoteForm", "function wireSavedResultsListFeedbackControls"),
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

            if (fileMetadata !== "Uploaded file · Jun 27, 2026") {
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
            if (reopenedFeedback.trim() === "") {
              throw new Error(`reopened feedback should render: ${reopenedFeedback}`);
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

    def test_result_route_renders_voice_audio_review_only_for_voice_captures(self):
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
              "const API_CAPTURES_PATH = '/api/workflows/captures';",
              extractBetween("function escapeHtml", "function setStatus"),
              extractBetween("function resolveResultSourceEvent", "function resolveResultMetadataLine"),
              extractBetween("function resolveResultMetadataLine", "function buildSourceExcerptLabel"),
            ].join("\\n");

            const context = {};
            vm.createContext(context);
            vm.runInContext(snippets, context);

            const voiceSourceEvent = context.resolveResultSourceEvent({
              capture_id: "cap-voice",
              input_type: "voice",
              source_event: {
                input_type: "voice",
                source_audio_storage_path: "workflow-voice-audio/user-1/cap-voice/voice-note.webm",
              },
            });
            const textSourceEvent = context.resolveResultSourceEvent({
              capture_id: "cap-text",
              input_type: "text",
              source_event: {
                input_type: "text",
              },
            });
            const fileSourceEvent = context.resolveResultSourceEvent({
              capture_id: "cap-file",
              input_type: "file",
              source_event: {
                input_type: "file",
              },
            });

            const voiceHtml = context.renderVoiceReview(voiceSourceEvent);
            const textHtml = context.renderVoiceReview(textSourceEvent);
            const fileHtml = context.renderVoiceReview(fileSourceEvent);

            if (!voiceHtml.includes("<audio") || !voiceHtml.includes("/api/workflows/captures/cap-voice/source-audio")) {
              throw new Error(`unexpected voice review html: ${voiceHtml}`);
            }
            if (!voiceHtml.includes("data-source-audio-endpoint=")) {
              throw new Error(`voice review should use data-source-audio-endpoint: ${voiceHtml}`);
            }
            if (voiceHtml.includes(" src=")) {
              throw new Error(`voice review should not embed an unauthenticated audio src: ${voiceHtml}`);
            }
            if (!voiceHtml.includes("Review captured audio")) {
              throw new Error(`missing voice review label: ${voiceHtml}`);
            }
            if (textHtml.trim() !== "") {
              throw new Error(`text capture should not render voice review: ${textHtml}`);
            }
            if (fileHtml.trim() !== "") {
              throw new Error(`file capture should not render voice review: ${fileHtml}`);
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

    def test_feedback_prompt_is_available_everywhere_and_binary(self):
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
              { capture_id: "cap-1", title: "Saved note", next_route: "/today/result/cap-1", metadata_line: "Pasted note", feedback_choice: "" },
            ]);

            if (!immediate.includes("How was this result?") || !immediate.includes("Useful") || !immediate.includes("Not useful")) {
              throw new Error(`immediate feedback missing: ${immediate}`);
            }
            if (!reopened.includes("How was this result?") || !reopened.includes("Useful") || !reopened.includes("Not useful")) {
              throw new Error(`reopened feedback missing: ${reopened}`);
            }
            if (!savedList.includes('data-feedback-choice="useful"') || !savedList.includes('data-feedback-choice="not_useful"')) {
              throw new Error(`saved results list should include feedback controls: ${savedList}`);
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

    def test_contextual_result_suggestions_are_immediate_only_and_quiet(self):
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        self.assertIn("Draft social post", js)
        self.assertIn("Analyze professionally", js)
        self.assertNotIn("Choose a workflow", js)
        self.assertNotIn("Generated from", js)
        self.assertNotIn("Derived result", js)

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
              extractBetween("function renderContextualSuggestions", "function renderResultFeedback"),
              extractBetween("function renderSavedResultsBody", "function renderSavedResultsList"),
            ].join("\\n");

            const context = {};
            vm.createContext(context);
            vm.runInContext(snippets, context);

            const none = context.renderContextualSuggestions(
              { result: {} },
              { isImmediateResult: true },
            );
            const one = context.renderContextualSuggestions(
              {
                result: {
                  contextual_suggestions: [
                    { type: "draft_social_post", copy: "This could become a social post.", action_label: "Draft social post" },
                  ],
                },
              },
              { isImmediateResult: true },
            );
            const two = context.renderContextualSuggestions(
              {
                result: {
                  contextual_suggestions: [
                    { type: "draft_social_post", copy: "This could become a social post.", action_label: "Draft social post" },
                    { type: "analyze_professionally", copy: "Analyze this through your professional lens.", action_label: "Analyze professionally" },
                  ],
                },
              },
              { isImmediateResult: true },
            );
            const reopened = context.renderContextualSuggestions(
              {
                result: {
                  contextual_suggestions: [
                    { type: "draft_social_post", copy: "This could become a social post.", action_label: "Draft social post" },
                  ],
                },
              },
              { isImmediateResult: false },
            );
            const savedList = context.renderSavedResultsBody([
              { capture_id: "cap-1", title: "Saved note", next_route: "/today/result/cap-1", metadata_line: "Pasted note" },
            ]);

            if (none.trim() !== "") {
              throw new Error(`zero suggestions should render nothing: ${none}`);
            }
            if (!one.includes("This could become a social post.") || !one.includes("Draft social post")) {
              throw new Error(`single suggestion missing quiet copy: ${one}`);
            }
            if (!two.includes("Optional next steps") || !two.includes("Analyze professionally")) {
              throw new Error(`two suggestions should render quietly: ${two}`);
            }
            if (reopened.trim() !== "") {
              throw new Error(`reopened result should not render suggestions: ${reopened}`);
            }
            if (savedList.includes("Draft social post") || savedList.includes("Analyze professionally")) {
              throw new Error(`saved results list should not include suggestions: ${savedList}`);
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
