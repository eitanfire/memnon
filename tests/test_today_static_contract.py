import json
import re
import subprocess
import textwrap
import unittest
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY_PATH = REPO_ROOT / "public" / "today.html"

_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _AncestryHTMLParser(HTMLParser):
    """Records, for each element with an id="...", the (tag, class) of every
    real DOM ancestor at the point that element opens. Lets a test assert
    actual nesting instead of document-order string position, which a flat
    substring index can't distinguish from "comes later but is a sibling."
    Caller must strip <script>/<style> contents first -- this is not a full
    HTML5 tokenizer and inline JS/CSS text will otherwise desync the tag stack.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._stack = []
        self.ancestors_by_id = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if "id" in attrs_dict:
            self.ancestors_by_id[attrs_dict["id"]] = list(self._stack)
        if tag not in _VOID_ELEMENTS:
            self._stack.append((tag, attrs_dict.get("class", "")))

    def handle_startendtag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if "id" in attrs_dict:
            self.ancestors_by_id[attrs_dict["id"]] = list(self._stack)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return


def _strip_script_and_style(html):
    html = re.sub(r"<script\b[^>]*>.*?</script>", "<script></script>", html, flags=re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "<style></style>", html, flags=re.S)
    return html


class TodayStaticContractTests(unittest.TestCase):
    """Static/string-level contract for public/today.html.

    Standing practice for this file specifically (2026-08-07, UX Burden review):
    structural or CSS changes to this page require confirmation via a real
    browser render -- a CDP screenshot at realistic (1x DPI, ~1280px) scale,
    not just this suite passing. This page has produced three real
    regressions invisible to static/string inspection alone: a CLI
    `--screenshot` false negative from async-timing (looked broken, wasn't),
    an orphaned `width: 100%` rule that made a lone button render oversized
    after its sibling was removed, and a real gap in this file's own coverage:
    document-order string checks (`a_index < b_index`) can't tell "appears
    later in the file" apart from "is actually nested inside" -- a one-line
    slip that put Capture after, rather than inside, Today's card boundary
    would have passed every prior assertion here. Closed with a small
    `html.parser`-based ancestry check
    (`test_capture_is_nested_inside_todays_card_boundary_not_a_second_card`),
    confirmed to fail against a simulated version of exactly that slip before
    being trusted. See docs/product-notes/today-capture-first-restore-2026-08-07.md.
    """

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

    def test_capture_is_nested_inside_todays_card_boundary_not_a_second_card(self):
        # Capture Restore (2026-08-07): the old separately-bordered "section
        # break" is gone -- Capture now shares Today's one card boundary.
        # "Clearly secondary, not co-equal weight" (spec §5) is carried by
        # internal hierarchy instead; see
        # test_record_control_is_the_dominant_element_in_the_merged_card and
        # docs/product-notes/today-capture-first-restore-2026-08-07.md.
        html = TODAY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("workflows-section-secondary", html)

        parser = _AncestryHTMLParser()
        parser.feed(_strip_script_and_style(html))

        self.assertIn("workflows-app", parser.ancestors_by_id, "id=\"workflows-app\" not found")
        self.assertTrue(
            any(cls.split() and "dashboard-capture-card" in cls.split() for _, cls in parser.ancestors_by_id["workflows-app"]),
            "Capture (#workflows-app) must be a DOM descendant of .dashboard-capture-card, not a sibling section after it",
        )

    def test_record_control_is_the_dominant_element_in_the_merged_card(self):
        # One shared card boundary is only safe if it doesn't flatten internal
        # hierarchy into five equal-weight peers (header, intro, continuity,
        # brief, capture). The record control's own card treatment must stay
        # intact; everything else added around it must read as visibly
        # quieter -- not just be asserted quieter in prose.
        html = TODAY_PATH.read_text(encoding="utf-8")
        workflows_css = Path("public/workflows.css").read_text(encoding="utf-8")

        def rule_body(source, selector):
            start = source.index(selector + " {")
            end = source.index("}", start)
            return source[start:end]

        # The record button's own card -- untouched, still the visually
        # heaviest element (bordered, backgrounded, shadowed).
        record_card = rule_body(workflows_css, ".workflows-card")
        for expected in ("border:", "background:", "box-shadow:"):
            self.assertIn(expected, record_card)

        # Simplified landing layout (2026-08-08): the merged-in "Capture"
        # heading/subhead were retired outright rather than kept as a demoted
        # kicker -- Today is the page's one heading now. Nothing to check
        # here anymore; superseded by test_capture_heading_and_subhead_are_retired.

        # The Daily Brief row must NOT be boxed -- no pill background/border
        # competing visually with the record card. (Continuity line itself
        # was retired in the same pass -- see test_continuity_line_is_retired.)
        daily_brief_body = rule_body(html, ".daily-brief-line")
        self.assertNotIn("background:", daily_brief_body, ".daily-brief-line must not be pill-boxed")
        self.assertNotIn("border-radius: 999px", daily_brief_body, ".daily-brief-line must not be pill-boxed")

        # The redundant inner memnon logo lockup (page nav already has one)
        # must not reappear mid-card once Capture is merged in.
        self.assertEqual(html.count('class="memnon-lockup'), 1)

    # ── Daily Brief / continuity relocation into Capture (2026-08-07) ──
    # Both were derived output from captures -- the same category as saved
    # results and history, which already belonged to Capture, not Today's
    # orientation role. A prior full run of this suite stayed green through
    # that exact move without a single failure, which proved nothing: no
    # existing test checked *where* these elements lived, only that they
    # existed and were unboxed.

    def test_today_intro_no_longer_claims_brief_and_continuity_live_there(self):
        html = TODAY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Daily Brief and continuity from recent captures live here", html)

    # ── Simplified landing layout (2026-08-08) ──
    # Same lesson as above, one round later: round five's relocation tests
    # (test_daily_brief_and_continuity_live_inside_capture_not_today,
    # test_capture_subhead_is_a_single_merged_line_not_two_tiers,
    # test_continuity_line_is_a_single_line_with_topic_and_continue_action)
    # asserted a layout this round deliberately superseded -- replaced below,
    # not left passing against copy/placement that no longer exists.

    def test_capture_heading_and_subhead_are_retired(self):
        # Today is the page's one heading now; Capture's own "Capture"
        # h1/subhead are gone, not demoted -- the record control is the
        # first thing a user sees after the Today heading and intro line.
        html = TODAY_PATH.read_text(encoding="utf-8")
        self.assertNotIn(">Capture<", html)
        self.assertNotIn("Capture a thought", html)
        self.assertNotIn("Turn it into something useful.", html)
        self.assertNotIn("workflows-subhead-secondary", html)
        # <main> keeps an accessible name even without a visible heading.
        self.assertIn('id="workflows-app" class="workflows-shell" aria-label="Capture"', html)

    def test_continuity_line_is_retired(self):
        # Removed from the page entirely (not relocated) -- confirmed by the
        # user rather than assumed, since two other readings were plausible
        # (keep it off-sketch, or move it behind a link). Thread detection
        # still runs at save time, so this doesn't lose safety; it loses a
        # standing reminder that safety never depended on.
        html = TODAY_PATH.read_text(encoding="utf-8")
        js = Path("public/workflows.js").read_text(encoding="utf-8")
        for needle in (
            "reflection-continuation", "continuity-topic", "reflection-continue-btn",
            "continuity-line", "renderReflectionContinuation", "continueReflectionThread",
        ):
            self.assertNotIn(needle, html, f"{needle} should be fully retired, not just hidden")
        # Its only reason to exist was this feature -- retired alongside it,
        # not left as an unreachable export.
        self.assertNotIn("focusCaptureComponent", js)
        self.assertNotIn("memnonFocusCapture", js)

    def test_latest_result_audio_and_view_toggle_are_retired(self):
        # Capture Data Model migration (2026-08-15): "Latest result" became a
        # single text-artifact panel sourced from workflow_captures, not a
        # Listen/Read toggle over Drive-hosted audio -- the current capture
        # pipeline never generates per-capture audio, so keeping the toggle
        # would offer a mode with nothing behind it. Retired outright (markup,
        # CSS, and JS), not just hidden -- same standard as the continuity
        # line above.
        html = TODAY_PATH.read_text(encoding="utf-8")
        for needle in (
            "reflection-view-toggle", "reflection-view-listen", "reflection-view-read",
            "reflection-audio-panel", "reflection-read-panel", "reflection-player-container",
            "reflection-player-label", "setReflectionView", "loadLatestAudio",
            "latestResultStyleDescriptor", "updateReflectionArchiveLabels",
            "archive-summary-copy", "REFLECTION_STYLE_ARCHIVE_COPY",
        ):
            self.assertNotIn(needle, html, f"{needle} should be fully retired, not just hidden")
        self.assertIn('id="reflection-read-content"', html)

    def test_daily_brief_and_latest_result_moved_to_page_footer(self):
        # Unlike continuity, these two were kept -- just relocated again, out
        # of Capture's own landmark into the shared page footer below the
        # divider, alongside saved-results/settings links.
        html = TODAY_PATH.read_text(encoding="utf-8")
        parser = _AncestryHTMLParser()
        parser.feed(_strip_script_and_style(html))

        for element_id in ("daily-brief-card",):
            ancestors = parser.ancestors_by_id.get(element_id)
            self.assertIsNotNone(ancestors, f'id="{element_id}" not found')
            ancestor_classes = [cls for _, cls in ancestors]
            self.assertTrue(
                any("capture-quiet-rows" in cls.split() for cls in ancestor_classes),
                f"#{element_id} must live in the page footer (.capture-quiet-rows)",
            )
            self.assertFalse(
                any("workflows-shell" in cls.split() for cls in ancestor_classes),
                f"#{element_id} must no longer be inside Capture's own landmark",
            )

        # Wireframe order: saved results, settings, latest result, brief.
        saved_index = html.index('id="workflows-saved-link-row"')
        settings_index = html.index('id="today-context-settings"')
        latest_result_index = html.index('class="latest-result-details"')
        daily_brief_index = html.index('id="daily-brief-card"')
        self.assertLess(saved_index, settings_index)
        self.assertLess(settings_index, latest_result_index)
        self.assertLess(latest_result_index, daily_brief_index)

    # ── Capture-First Hierarchy Restore (2026-08-07) ──
    # Targeted regressions for the four asked-for changes, added because a
    # broad suite staying green after this pass didn't prove any of these
    # specific behaviors -- only that nothing it was already checking broke.
    # See docs/product-notes/today-capture-first-restore-2026-08-07.md.

    def test_capture_no_longer_sits_below_todays_own_secondary_content(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        capture_index = html.index('id="workflows-app"')
        recent_notes_index = html.index('id="recent-notes-card"')

        self.assertLess(
            capture_index, recent_notes_index,
            "Capture must precede Recent Notes -- it was previously the last element on the page",
        )

    def test_daily_brief_collapses_to_four_states_and_absence_is_not_an_answer(self):
        # Burden thread, 2026-10-04: eight on-states collapsed to "nothing to play
        # yet" + "ready"; a null status renders as an error, never as a settled
        # state (the 7171747 failure mode).
        html = TODAY_PATH.read_text(encoding="utf-8")
        for retired in (
            "Today's brief is scheduled",
            "Today's brief is being prepared",
            "No brief yet today",
            "Your private daily brief feed is on",
        ):
            self.assertNotIn(retired, html)
        self.assertIn("daily_feed_status && typeof profile.daily_feed_status === \"object\"", html)
        self.assertIn("Today's brief status couldn't be checked just now.", html)
        self.assertIn('copyEl.setAttribute("role", "alert")', html)
        self.assertIn("Today's brief arrives after ${hourLabel}.", html)
        self.assertIn("Today's brief isn't in yet.", html)

    def test_dead_tasks_card_and_orphaned_capture_handlers_are_gone(self):
        # Polish pass 2026-10-04: the tasks card never rendered (its loader was
        # never called) and five handlers pointed at elements that no longer exist.
        html = TODAY_PATH.read_text(encoding="utf-8")
        for needle in (
            'id="tasks-card"',
            "loadCurrentTasks",
            '"record-btn"',
            '"write-btn"',
            '"capture-text-panel"',
            '"capture-text-input"',
            '"first-name"',
        ):
            self.assertNotIn(needle, html, f"{needle} should be removed from Today")

    def test_latest_result_and_daily_brief_detail_default_collapsed(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        # A bare `<details ...>` tag with no `open` attribute renders collapsed
        # by default -- this is what makes the one-line-by-default behavior
        # real rather than asserted only in prose.
        self.assertIn('<details class="latest-result-details">', html)
        self.assertIn('<details class="daily-brief-disclosure">', html)
        self.assertNotIn('<details class="latest-result-details" open', html)
        self.assertNotIn('<details class="daily-brief-disclosure" open', html)

    def test_daily_brief_mechanics_are_nested_inside_the_disclosure(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        disclosure_start = html.index('<details class="daily-brief-disclosure">')
        disclosure_end = html.index("</details>", disclosure_start)
        disclosure_body = html[disclosure_start:disclosure_end]

        # The feed URL, Overcast/Share actions, and Apple Podcasts steps are
        # the "mechanics" the ask wanted off the page by default -- they must
        # live inside the disclosure, not exposed at the top level next to it.
        for element_id in ("daily-brief-url-wrap", "daily-brief-overcast-btn", "daily-brief-apple-note"):
            self.assertIn(f'id="{element_id}"', disclosure_body, f"{element_id} must be nested inside the How-to-listen disclosure")

        # The always-visible one-liner (icon, state headline, primary action)
        # must stay outside the disclosure, not require a click to see at all.
        pre_disclosure = html[:disclosure_start]
        daily_brief_line_start = pre_disclosure.rindex('<div class="daily-brief-line"')
        always_visible = html[daily_brief_line_start:disclosure_start]
        self.assertIn('id="daily-brief-title"', always_visible)
        self.assertIn('id="daily-brief-enable"', always_visible)

    def test_recent_notes_do_not_render_a_reflection_style_category_tag(self):
        html = TODAY_PATH.read_text(encoding="utf-8")

        load_notes_start = html.index("async function loadRecentNotes(token)")
        load_notes_end = html.index("\n    function formatNoteDate", load_notes_start)
        load_notes_body = html[load_notes_start:load_notes_end]

        # The per-note reflection-style pill ("Complete reflection" /
        # "Practical guidance" / "Grounded reflection") predates the Latest
        # Reflection Naming Cleanup and was never a genuine category the note
        # content needed -- retired outright, not just hidden.
        self.assertNotIn("styleLabel", load_notes_body)
        self.assertNotIn("REFLECTION_STYLE_LABELS", load_notes_body)
        self.assertNotIn("n.reflection_style", load_notes_body)

        # The date pill is not a category tag and must survive.
        self.assertIn('<span class="note-pill">${formatNoteDate(n.date)}</span>', load_notes_body)

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

    def test_open_capture_jump_link_is_removed_as_a_hollow_cta(self):
        html = TODAY_PATH.read_text(encoding="utf-8")
        js = Path("public/workflows.js").read_text(encoding="utf-8")

        # Capture Restore (2026-08-07): once Capture moved to sit immediately
        # under Today's header, "Open capture" only ever scrolled a few pixels
        # to a section already in view -- a second, hollow CTA stacked on the
        # real one. Removed outright (UX Burden review) rather than kept
        # because a test happened to require it; see
        # docs/product-notes/today-capture-first-restore-2026-08-07.md.
        self.assertNotIn('id="today-open-capture"', html)
        self.assertNotIn("today-open-capture", js)

        # focusCaptureComponent's last caller ("continue the thread") was
        # itself retired in the simplified-landing pass (2026-08-08) -- see
        # test_continuity_line_is_retired. It's gone too now, not kept
        # unreachable on the theory a future caller might show up.

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

        # "Latest result" is now a single text-artifact panel (Capture Data
        # Model migration, 2026-08-15) -- no separate "listen or read" modes,
        # no reflection_style-flavored label ("Latest complete result" was
        # that style label; it's retired along with the toggle it lived on).
        self.assertIn("Latest result", html)
        self.assertIn("Loading your latest result…", html)
        self.assertIn("Your latest result text will appear here after processing.", html)
        self.assertNotIn("Latest reflection", html)
        self.assertNotIn("Latest complete reflection", html)
        self.assertNotIn("Latest complete result", html)
        self.assertNotIn("listen or read", html)
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

    # ── Standing context on Today (controller brief, 2026-09-12) ──

    def _run_standing_context(self, profile_literal):
        """Run renderStandingContext against a stub DOM, return the two lines."""
        html = TODAY_PATH.read_text(encoding="utf-8")
        start = html.index("    const LANE_LABELS = {")
        end = html.index("    function renderLatestReflectionText(", start)
        snippet = html[start:end]

        script = f"""
const vm = require("vm");
const activeEl = {{ textContent: "", hidden: false }};
const storedEl = {{ textContent: "", hidden: false }};
const context = {{
  REFLECTION_STYLE_LABELS: {{
    practical: "Practical guidance",
    grounded: "Grounded reflection",
    complete: "Complete reflection",
  }},
  document: {{
    getElementById(id) {{
      if (id === "standing-context-active") return activeEl;
      if (id === "standing-context-stored") return storedEl;
      return null;
    }},
  }},
}};
vm.createContext(context);
vm.runInContext({snippet!r}, context);
context.renderStandingContext({profile_literal});
console.log(JSON.stringify({{
  active: activeEl.textContent,
  stored: storedEl.textContent,
  storedHidden: storedEl.hidden,
}}));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        return json.loads(completed.stdout)

    def test_standing_context_states_what_shapes_a_result_and_what_only_sits_stored(self):
        # The whole point of the brief: a user could not tell what standing
        # context the app held. Only lane + reflection_style reach a Today
        # capture, so the two groups are stated separately rather than merged
        # into one list that would imply subjects/standards shape the note.
        result = self._run_standing_context(
            '{ lane: "professional", reflection_style: "practical", '
            'subjects: "Biology", grade_levels: [9, 10], state_standards: ["SC.9"], '
            'narration_voice: "sage" }'
        )

        self.assertIn("professional lane", result["active"])
        self.assertIn("practical guidance", result["active"])
        self.assertFalse(result["storedHidden"])
        self.assertIn("subjects", result["stored"])
        self.assertIn("grade levels", result["stored"])
        self.assertIn("state standards", result["stored"])
        self.assertIn("narration voice", result["stored"])
        # The stored line must not claim to shape the result.
        self.assertNotIn("subjects", result["active"])

    def test_standing_context_empty_state_says_so_plainly(self):
        result = self._run_standing_context("{}")

        self.assertEqual(result["active"], "No standing context set yet.")
        self.assertTrue(result["storedHidden"])

    def test_standing_context_logged_out_does_not_claim_to_hold_anything(self):
        result = self._run_standing_context("null")

        self.assertEqual(result["active"], "Sign in to see the context Memnon is holding.")
        self.assertTrue(result["storedHidden"])

    def test_standing_context_renders_stored_only_profile_without_claiming_influence(self):
        # A profile carrying teaching context but no lane/style: the active
        # line must not invent influence that isn't there.
        result = self._run_standing_context('{ subjects: "Chemistry" }')

        # Field categories are named, never the values themselves -- the line
        # says what is held, not what it contains.
        self.assertIn("subjects", result["stored"])
        self.assertNotIn("Chemistry", result["stored"])
        self.assertFalse(result["storedHidden"])
        self.assertNotIn("lane", result["active"])
        self.assertEqual(result["active"], "Nothing you have saved shapes a result here yet.")

    def test_standing_context_markup_defaults_to_signed_out_copy(self):
        # Static default is the signed-out line, not a loading string -- the
        # latest-result slot's "Loading…" never resolves for a logged-out
        # visitor, and this block must not repeat that.
        html = TODAY_PATH.read_text(encoding="utf-8")

        self.assertIn('id="standing-context-active"', html)
        self.assertIn('id="standing-context-stored"', html)
        self.assertIn("Sign in to see the context Memnon is holding.", html)
        # Edit affordance stays the existing /setup link, no new editing UI.
        self.assertIn('id="today-context-settings"', html)
        self.assertIn('href="/setup"', html)

    # ── Slots must never sit on an unresolvable loading state ──

    def _run_latest_result(self, note_literal, state_literal):
        html = TODAY_PATH.read_text(encoding="utf-8")
        start = html.index("    function renderLatestReflectionText(")
        end = html.index("    function summarizeGuidingVoices(", start)
        snippet = html[start:end]

        script = f"""
const vm = require("vm");
const container = {{ innerHTML: "" }};
const context = {{ document: {{ getElementById: (id) => id === "reflection-read-content" ? container : null }} }};
vm.createContext(context);
vm.runInContext({snippet!r}, context);
context.renderLatestReflectionText({note_literal}, {state_literal});
console.log(JSON.stringify({{ html: container.innerHTML }}));
"""
        completed = subprocess.run(["node", "-e", script], check=False, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        return json.loads(completed.stdout)["html"]

    def test_latest_result_never_ships_a_loading_string_that_cannot_resolve(self):
        # renderLatestReflectionText is only reachable from loadRecentNotes,
        # which runs inside an auth callback that returns early with no user --
        # so a static "Loading…" default was permanent for logged-out visitors.
        html = TODAY_PATH.read_text(encoding="utf-8")

        # Assert on the element's default content, not the whole file -- the
        # comment explaining this fix necessarily names the old string.
        start = html.index('<div id="reflection-read-content"')
        default_markup = html[start:html.index("</div>", start)]

        self.assertNotIn("Loading", default_markup)
        self.assertIn("Sign in to see your latest result.", default_markup)

    def test_latest_result_signed_out_state_is_distinct_from_empty(self):
        signed_out = self._run_latest_result("null", '"signed-out"')
        empty = self._run_latest_result("null", '"ready"')

        self.assertIn("Sign in to see your latest result.", signed_out)
        self.assertIn("will appear here after processing", empty)
        self.assertNotEqual(signed_out, empty)

    def test_latest_result_failed_fetch_reports_instead_of_hanging(self):
        unavailable = self._run_latest_result("null", '"unavailable"')

        self.assertIn("could not be loaded", unavailable)

    def test_load_recent_notes_renders_a_state_when_the_fetch_throws(self):
        html = TODAY_PATH.read_text(encoding="utf-8")
        start = html.index("async function loadRecentNotes(token)")
        end = html.index("\n    function formatNoteDate", start)
        body = html[start:end]

        # The catch previously only console.error'd, leaving the slot as-is.
        self.assertIn('renderLatestReflectionText(null, "unavailable")', body)

    def test_daily_brief_does_not_treat_unresolved_auth_as_signed_out(self):
        # The module-level render runs before onAuthStateChanged fires, so
        # reading auth.currentUser alone showed "Sign in to subscribe" beneath
        # a signed-in header. Pending is now its own state.
        html = TODAY_PATH.read_text(encoding="utf-8")

        self.assertIn("let authResolved = false;", html)
        self.assertIn("authResolved && !auth.currentUser", html)
        self.assertIn("const authPending =", html)
        self.assertIn("if (authPending) {", html)

    def test_signed_out_auth_branch_renders_states_instead_of_returning_early(self):
        html = TODAY_PATH.read_text(encoding="utf-8")
        start = html.index("onAuthStateChanged(auth, async (user) => {")
        branch = html[start:start + 900]

        self.assertIn("authResolved = true;", branch)
        self.assertIn('renderLatestReflectionText(null, "signed-out")', branch)
        self.assertIn("renderStandingContext(null)", branch)

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
        # "Capture a thought..." copy was retired in the simplified-landing
        # pass (2026-08-08), see test_capture_heading_and_subhead_are_retired
        # -- this test now only covers the mount points, which are unchanged.
        html = TODAY_PATH.read_text(encoding="utf-8")

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
