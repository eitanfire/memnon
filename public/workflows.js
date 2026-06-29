import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import {
  getAuth,
  onAuthStateChanged,
  signInWithCustomToken,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {
  WORKFLOWS_DEBUG_BUILD,
  canonicalizeAuthReturnUrl,
  getCaptureValidationError,
  isLocalStaticHost,
  shouldBlockUnexpectedNavigation,
  shouldBypassRemoteAuth,
  shouldShowLocalDebugUi,
} from "/workflows_url_helpers.js";

const firebaseConfig = {
  apiKey: "AIzaSyAfnbEJrqWZPgxh_k61dl2D-DAEO4AbMoY",
  authDomain: "memnon-app.web.app",
  projectId: "memnon-app",
  storageBucket: "memnon-app.firebasestorage.app",
  messagingSenderId: "714155490867",
  appId: "1:714155490867:web:f382fddb58a596bede6d46",
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const params = new URLSearchParams(window.location.search);
const callbackToken = params.get("token");
const authError = params.get("error");
const isStaticLocalhost = isLocalStaticHost(
  window.location.hostname,
  window.location.port,
);
const showLocalDebugUi = shouldShowLocalDebugUi(
  window.location.search,
  window.location.hostname,
  window.location.port,
);
const bypassRemoteAuth = shouldBypassRemoteAuth(
  window.location.hostname,
  window.location.port,
);
const LOCAL_API_ORIGIN = "http://127.0.0.1:5051";
const API_ORIGIN = isStaticLocalhost ? LOCAL_API_ORIGIN : "";
const API_CAPTURES_PATH = "/api/workflows/captures";
const API_CONTEXTS_PATH = "/api/workflows/contexts";
const AUTH_START_URL = "https://api-4hth6oktaa-uc.a.run.app/auth/start";
const SAVED_RESULTS_PATH = "/workflows/saved";
const PENDING_CAPTURE_KEY = "memnon_workflows_pending_capture_v1";
const LOCAL_DEV_ID_TOKEN = "local-dev-token";
const SCRIPT_LOADED_AT = new Date().toISOString();
const SOURCE_EXCERPT_LABEL = "From your note";
const KEY_POINT_LABEL = "Key point";
const NEXT_STEP_LABEL = "Next step";
const WHY_KEEP_THIS_LABEL = "Why keep this";
const VOICE_MIME_CANDIDATES = ["audio/webm", "audio/mp4", "video/mp4"];
const MIME_EXTENSION_MAP = {
  "audio/webm": "webm",
  "audio/mp4": "mp4",
  "audio/x-m4a": "m4a",
  "video/mp4": "mp4",
  "audio/ogg": "ogg",
};
const MIN_AUDIO_BYTES = 128;

let currentUser = null;
let initialRouteHandled = false;
let submitInFlight = false;
let voiceCaptureState = "idle";
let mediaRecorder = null;
let mediaStream = null;
let recordingChunks = [];
let activeRecordingMimeType = "";

function buildDebugPayload(event, extra = {}) {
  return {
    event,
    build: WORKFLOWS_DEBUG_BUILD,
    loadedAt: SCRIPT_LOADED_AT,
    href: window.location.href,
    hostname: window.location.hostname,
    port: window.location.port,
    usingLocalDevBypass: bypassRemoteAuth,
    apiOrigin: API_ORIGIN || "(same-origin)",
    currentUserPresent: Boolean(currentUser),
    ...extra,
  };
}

function renderDebugState(event, extra = {}) {
  if (!showLocalDebugUi) {
    return;
  }
  const marker = document.getElementById("workflows-build-marker");
  const panel = document.getElementById("workflows-debug-state");
  if (marker) {
    marker.hidden = false;
    marker.textContent = `workflows local dev build: ${WORKFLOWS_DEBUG_BUILD}`;
  }
  if (panel) {
    panel.hidden = false;
    panel.textContent = JSON.stringify(buildDebugPayload(event, extra), null, 2);
  }
}

function logDebug(event, extra = {}) {
  const payload = buildDebugPayload(event, extra);
  console.log(`[workflows] ${event}`, payload);
  renderDebugState(event, extra);
}

function safeNavigate(targetUrl, reason) {
  const resolvedUrl = new URL(targetUrl, window.location.href).toString();
  logDebug("navigation_attempt", {
    reason,
    targetUrl: resolvedUrl,
  });
  if (shouldBlockUnexpectedNavigation(window.location.href, resolvedUrl)) {
    const message = `Blocked unexpected navigation: ${resolvedUrl}`;
    console.error("[workflows] blocked navigation", buildDebugPayload("blocked_navigation", {
      reason,
      targetUrl: resolvedUrl,
    }));
    setStatusTone("Blocked unexpected navigation in local debug mode.", "error");
    renderDebugState("blocked_navigation", {
      reason,
      targetUrl: resolvedUrl,
      blockedMessage: message,
    });
    throw new Error(message);
  }
  window.location.assign(resolvedUrl);
}

console.log("workflows.js loaded", {
  build: WORKFLOWS_DEBUG_BUILD,
  loadedAt: SCRIPT_LOADED_AT,
  href: window.location.href,
  usingLocalDevBypass: bypassRemoteAuth,
  apiOrigin: API_ORIGIN || "(same-origin)",
});

function parseWorkflowsRoute(pathname) {
  const normalized =
    pathname === "/workflows.html" ? "/workflows" : pathname.replace(/\/+$/, "") || "/workflows";
  if (normalized === SAVED_RESULTS_PATH) {
    return { screen: "saved" };
  }
  const match = normalized.match(/^\/workflows\/result\/([^/]+)$/);
  if (match) {
    return { screen: "result", captureId: decodeURIComponent(match[1]) };
  }
  return { screen: "capture" };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setStatus(message) {
  const status = document.getElementById("workflows-status");
  if (status) {
    status.textContent = message || "";
    status.classList.remove("is-error", "is-working");
  }
}

function setStatusTone(message, tone = "neutral") {
  setStatus(message);
  const status = document.getElementById("workflows-status");
  if (!status || !message) {
    return;
  }
  if (tone === "error") {
    status.classList.add("is-error");
  }
  if (tone === "working") {
    status.classList.add("is-working");
  }
}

function currentReturnUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("token");
  url.searchParams.delete("error");
  return canonicalizeAuthReturnUrl(url.toString());
}

function buildAuthStartUrl() {
  const authUrl = new URL(AUTH_START_URL);
  authUrl.searchParams.set("return_to", currentReturnUrl());
  return authUrl.toString();
}

function readPendingCapture() {
  try {
    const raw = sessionStorage.getItem(PENDING_CAPTURE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writePendingCapture(payload) {
  sessionStorage.setItem(PENDING_CAPTURE_KEY, JSON.stringify(payload));
}

function clearPendingCapture() {
  sessionStorage.removeItem(PENDING_CAPTURE_KEY);
}

function setPastePanelVisible(visible, options = {}) {
  const panel = document.getElementById("paste-panel");
  const input = document.getElementById("capture-text");
  if (!panel) {
    return;
  }
  panel.hidden = !visible;
  if (visible && options.focus && input) {
    input.focus();
  }
}

function syncAuthPrompt() {
  const prompt = document.getElementById("workflows-auth-prompt");
  const link = document.getElementById("workflows-signin");
  if (!prompt || !link) {
    return;
  }

  link.href = buildAuthStartUrl();
  syncSavedResultsLink();
  if (bypassRemoteAuth) {
    prompt.hidden = true;
    prompt.style.display = "none";
    prompt.setAttribute("aria-hidden", "true");
    link.tabIndex = -1;
    return;
  }
  prompt.style.display = "";
  prompt.removeAttribute("aria-hidden");
  link.removeAttribute("tabindex");
  prompt.hidden = Boolean(currentUser);
}

function syncSavedResultsLink() {
  const row = document.getElementById("workflows-saved-link-row");
  const link = document.getElementById("workflows-saved-link");
  if (!row || !link) {
    return;
  }
  const visible = Boolean(currentUser) || bypassRemoteAuth;
  link.href = SAVED_RESULTS_PATH;
  row.hidden = !visible;
  row.style.display = visible ? "" : "none";
}

function syncSubmitState() {
  const input = document.getElementById("capture-text");
  const context = document.getElementById("capture-context");
  const submit = document.getElementById("capture-submit");
  const form = document.getElementById("capture-form");
  const recordTrigger = document.getElementById("record-trigger");
  const recordLabel = recordTrigger?.querySelector(".workflows-record-label");
  const showPaste = document.getElementById("show-paste");
  const uploadTrigger = document.getElementById("upload-trigger");
  const voiceBusy = voiceCaptureState !== "idle";
  const blockingVoiceState = ["requesting", "stopping", "uploading", "processing"].includes(voiceCaptureState);
  if (!submit) {
    return;
  }
  submit.disabled = submitInFlight || voiceBusy || !input || !input.value.trim();
  const signedOutCaptureCta = !currentUser && !bypassRemoteAuth;
  submit.textContent = submitInFlight
    ? "Working..."
    : signedOutCaptureCta
      ? "Continue and sign in to save"
      : "Continue";
  if (recordTrigger && recordLabel) {
    recordTrigger.disabled = submitInFlight || blockingVoiceState;
    recordTrigger.setAttribute("aria-pressed", voiceCaptureState === "recording" ? "true" : "false");
    if (voiceCaptureState === "requesting") {
      recordLabel.textContent = "Allow microphone…";
    } else if (voiceCaptureState === "recording") {
      recordLabel.textContent = "Stop recording";
    } else if (voiceCaptureState === "stopping") {
      recordLabel.textContent = "Stopping…";
    } else if (voiceCaptureState === "uploading") {
      recordLabel.textContent = "Uploading…";
    } else if (voiceCaptureState === "processing") {
      recordLabel.textContent = "Processing…";
    } else {
      recordLabel.textContent = "Record now";
    }
  }
  if (showPaste) {
    showPaste.disabled = submitInFlight || voiceBusy;
  }
  if (uploadTrigger) {
    uploadTrigger.disabled = submitInFlight || voiceBusy;
  }
  if (context) {
    context.disabled = submitInFlight || blockingVoiceState;
  }
  if (form) {
    form.setAttribute("aria-busy", submitInFlight || voiceBusy ? "true" : "false");
  }
  syncAuthPrompt();
}

function setVoiceCaptureState(nextState) {
  voiceCaptureState = nextState;
  syncSubmitState();
}

function selectVoiceMimeType() {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") {
    return "";
  }
  return VOICE_MIME_CANDIDATES.find((mimeType) => MediaRecorder.isTypeSupported(mimeType)) || "";
}

function extensionForMimeType(mimeType) {
  return MIME_EXTENSION_MAP[mimeType] || MIME_EXTENSION_MAP[mimeType?.split(";")[0]?.trim()] || "webm";
}

function buildVoiceFilename(mimeType) {
  return `voice-note-${Date.now()}.${extensionForMimeType(mimeType)}`;
}

function releaseRecordingResources() {
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
  }
  mediaStream = null;
  mediaRecorder = null;
  recordingChunks = [];
  activeRecordingMimeType = "";
}

function nextPaint() {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => resolve());
  });
}

async function getToken() {
  const user = auth.currentUser;
  if (!user && bypassRemoteAuth) {
    logDebug("local_dev_token_selected", {
      tokenMode: "local-dev-token",
    });
    return LOCAL_DEV_ID_TOKEN;
  }
  if (!user) {
    throw new Error("Sign in required");
  }
  return user.getIdToken();
}

async function apiFetch(path, init = {}) {
  const token = await getToken();
  const headers = new Headers(init.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  logDebug("api_fetch", {
    method: init.method || "GET",
    target: `${API_ORIGIN}${path}`,
  });

  const response = await fetch(`${API_ORIGIN}${path}`, {
    ...init,
    headers,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function resetResultCards() {
  for (const cardId of ["loading-card", "primary-artifact-card", "saved-note-card"]) {
    const card = document.getElementById(cardId);
    if (!card) {
      continue;
    }
    card.hidden = true;
    card.innerHTML = "";
  }
  const sourcePanel = document.getElementById("source-text-panel");
  if (sourcePanel) {
    sourcePanel.hidden = true;
    sourcePanel.open = false;
  }
  const sourceText = document.getElementById("source-text-content");
  if (sourceText) {
    sourceText.textContent = "";
  }
}

function resetCaptureForm() {
  const input = document.getElementById("capture-text");
  const context = document.getElementById("capture-context");
  if (input) {
    input.value = "";
  }
  if (context) {
    context.value = "";
  }
  setPastePanelVisible(false);
  clearPendingCapture();
}

function setScreenVisibility(showCapture) {
  const captureForm = document.getElementById("capture-form");
  const resultView = document.getElementById("result-view");
  if (!captureForm || !resultView) {
    return;
  }

  captureForm.hidden = !showCapture;
  captureForm.style.display = showCapture ? "" : "none";
  captureForm.inert = !showCapture;
  captureForm.setAttribute("aria-hidden", showCapture ? "false" : "true");

  resultView.hidden = showCapture;
  resultView.style.display = showCapture ? "none" : "grid";
  resultView.inert = showCapture;
  resultView.setAttribute("aria-hidden", showCapture ? "true" : "false");
}

function showCaptureScreen() {
  setScreenVisibility(true);
}

function showResultScreen() {
  setScreenVisibility(false);
}

function renderCardActions(actions) {
  if (!actions?.length) {
    return "";
  }
  return `
    <div class="workflows-inline-actions">
      ${actions.map((action) => action.html).join("")}
    </div>
  `;
}

function renderThemes(themes) {
  if (!themes?.length) {
    return "";
  }
  return `
    <div class="workflows-theme-list">
      ${themes.map((theme) => `<span class="workflows-theme-chip">${escapeHtml(theme)}</span>`).join("")}
    </div>
  `;
}

function formatLocalCaptureDate(timestamp) {
  if (!timestamp) {
    return "";
  }
  const calendarMatch = String(timestamp).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (calendarMatch) {
    const [, year, month, day] = calendarMatch;
    return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), 12)).toLocaleDateString(
      undefined,
      {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      },
    );
  }
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return parsed.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function describeSourceType(inputType) {
  if (inputType === "text") {
    return "Pasted note";
  }
  if (inputType === "voice") {
    return "Voice note";
  }
  return "Saved note";
}

function buildMetadataLine(sourceEvent) {
  const parts = [];
  const sourceType = describeSourceType(sourceEvent?.input_type);
  if (sourceType) {
    parts.push(sourceType);
  }
  const captureDate = formatLocalCaptureDate(sourceEvent?.created_at);
  if (captureDate) {
    parts.push(captureDate);
  }
  if (sourceEvent?.context_hint) {
    parts.push(
      sourceEvent.context_hint[0].toUpperCase() + sourceEvent.context_hint.slice(1),
    );
  }
  return parts.join(" · ");
}

function renderSourceExcerpt(text) {
  if (!text) {
    return "";
  }
  return `
    <div class="workflows-source-excerpt">
      <p class="workflows-source-label">${escapeHtml(SOURCE_EXCERPT_LABEL)}</p>
      <p>${escapeHtml(text)}</p>
    </div>
  `;
}

function renderThreadChooser(threads) {
  return `
    <div class="workflows-thread-chooser" hidden>
      ${threads.map((thread) => `
        <button type="button" class="workflows-thread-option" data-context-id="${escapeHtml(thread.context_id)}">
          ${escapeHtml(thread.title)}
        </button>
      `).join("")}
    </div>
  `;
}

function renderRelatedThreadBlock(payload, options = {}) {
  const relatedThread = payload?.result?.related_thread || {};
  const confirmedTitle = relatedThread.confirmed_title || "";
  const activeThreads = options.activeThreads || [];
  const threadDecision = payload?.threading?.context_decision || "";
  const canShowManualControls =
    Boolean(options.isImmediateResult)
    && !confirmedTitle
    && !threadDecision
    && activeThreads.length > 0;

  if (!confirmedTitle && !canShowManualControls) {
    return "";
  }

  return `
    <section class="workflows-related-thread-block">
      ${confirmedTitle ? `
        <div class="workflows-related-thread-confirmed">
          <p class="workflows-related-thread-label">Ongoing thread</p>
          <p class="workflows-related-thread-title">${escapeHtml(confirmedTitle)}</p>
        </div>
      ` : ""}
      ${canShowManualControls ? `
        <div class="workflows-related-thread-prompt">
          <p class="workflows-related-thread-copy">This belongs with an ongoing thread.</p>
          <div class="workflows-inline-actions">
            <button type="button" class="btn btn-outline" id="keep-with-thread">Keep with a thread</button>
            <button type="button" class="btn btn-outline" id="keep-separate">Keep separate</button>
          </div>
          ${renderThreadChooser(activeThreads)}
        </div>
      ` : ""}
    </section>
  `;
}

function renderSections(sections) {
  if (!sections?.length) {
    return "";
  }
  return sections
    .filter((section) => section?.label && section?.text)
    .map(
      (section) => {
        const normalizedLabel =
          section.label === "Key point"
            ? KEY_POINT_LABEL
            : section.label === "Next step"
              ? NEXT_STEP_LABEL
              : section.label === "Why keep this"
                ? WHY_KEEP_THIS_LABEL
              : section.label;
        return `
        <section class="workflows-section-block">
          <h3>${escapeHtml(normalizedLabel)}</h3>
          <p>${escapeHtml(section.text)}</p>
        </section>
      `;
      },
    )
    .join("");
}

function renderResultCard(card, options) {
  const {
    statusLabel,
    statusTone,
    kicker,
    title,
    metadataLine = "",
    interpretationLine = "",
    framingLine = "",
    bodyHtml = "",
    actions = [],
  } = options;

  card.hidden = false;
  card.innerHTML = `
    <div class="workflows-card-header">
      <span class="workflows-status-pill ${statusTone ? `is-${statusTone}` : ""}">${escapeHtml(statusLabel)}</span>
      ${kicker ? `<p class="workflows-kicker">${escapeHtml(kicker)}</p>` : ""}
    </div>
    <h2>${escapeHtml(title)}</h2>
    ${metadataLine ? `<p class="workflows-metadata-line">${escapeHtml(metadataLine)}</p>` : ""}
    ${interpretationLine ? `<p class="workflows-interpretation">${escapeHtml(interpretationLine)}</p>` : ""}
    ${framingLine ? `<p class="workflows-framing">${escapeHtml(framingLine)}</p>` : ""}
    ${bodyHtml ? `<div class="workflows-body">${bodyHtml}</div>` : ""}
    ${renderCardActions(actions)}
  `;
}

function renderLoadingState() {
  resetResultCards();
  showResultScreen();
  const card = document.getElementById("loading-card");
  renderResultCard(card, {
    statusLabel: "Working",
    statusTone: "working",
    kicker: "Next step",
    title: "Shaping this into a next step...",
    framingLine: "This usually takes a moment.",
    bodyHtml: `
      <div class="workflows-loading-lines">
        <span></span>
        <span></span>
      </div>
    `,
  });
}

function renderLoadError() {
  resetResultCards();
  showResultScreen();
  const card = document.getElementById("saved-note-card");
  renderResultCard(card, {
    statusLabel: "Try again",
    statusTone: "attention",
    kicker: "Result",
    title: "Could not load this note",
    framingLine: "Try again in a moment or start a fresh capture.",
    actions: [
      { html: '<button type="button" class="btn btn-primary" id="retry-load-capture">Try again</button>' },
      { html: '<button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>' },
    ],
  });
  document.getElementById("retry-load-capture")?.addEventListener("click", () => {
    handleCurrentRoute();
  });
  wireReturnToCapture();
}

function renderSavedResultsError() {
  resetResultCards();
  showResultScreen();
  const card = document.getElementById("saved-note-card");
  renderResultCard(card, {
    statusLabel: "Try again",
    statusTone: "attention",
    kicker: "Saved results",
    title: "Could not load saved results",
    framingLine: "Try again in a moment or start a fresh capture.",
    actions: [
      { html: '<button type="button" class="btn btn-primary" id="retry-load-saved-results">Try again</button>' },
      { html: '<button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>' },
    ],
  });
  document.getElementById("retry-load-saved-results")?.addEventListener("click", () => {
    handleCurrentRoute();
  });
  wireReturnToCapture();
}

function renderSavedResultsBody(items) {
  if (!items?.length) {
    return '<p class="workflows-empty-state">No saved results yet. Finish one capture and it will show up here.</p>';
  }
  return `
    <div class="workflows-saved-results-list">
      ${items
        .map(
          (item) => `
            <article class="workflows-saved-results-item">
              <div class="workflows-saved-results-item-header">
                <h3>${escapeHtml(item.title || "Saved note")}</h3>
                <a class="workflows-saved-results-link" href="${escapeHtml(item.next_route || "/workflows")}">Open</a>
              </div>
              <p class="workflows-saved-results-meta">${escapeHtml(item.metadata_line || item.status || "")}</p>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderSavedResultsList(items) {
  resetResultCards();
  showResultScreen();
  const card = document.getElementById("primary-artifact-card");
  renderResultCard(card, {
    statusLabel: "Saved results",
    statusTone: "saved",
    kicker: "History",
    title: "Saved workflow results",
    framingLine: "Reopen any saved workflow artifact.",
    bodyHtml: renderSavedResultsBody(items),
    actions: [
      { html: '<button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>' },
    ],
  });
  wireReturnToCapture();
}

function renderSavedNote(payload, options = {}) {
  const activeThreads = options.activeThreads || [];
  resetResultCards();
  showResultScreen();

  const card = document.getElementById("saved-note-card");
  const sourcePanel = document.getElementById("source-text-panel");
  const sourceText = document.getElementById("source-text-content");
  const savedArtifact = payload.result.saved_note_artifact || {};
  const defaultFramingLine =
    savedArtifact.state === "weak_signal"
      ? "This is a small note worth preserving."
      : "This seems worth keeping, but it may need a little direction before it becomes something stronger.";
  const savedStatusLabel =
    savedArtifact.state === "needs_direction" ? "Needs light direction" : "Saved for later";
  const savedKicker =
    savedArtifact.state === "needs_direction" ? "Worth keeping" : "Kept as a saved note";
  renderResultCard(card, {
    statusLabel: savedArtifact.status || savedStatusLabel,
    statusTone: "saved",
    kicker: savedKicker,
    title: savedArtifact.title || "Saved note",
    metadataLine: buildMetadataLine(payload.source_event) || savedArtifact.metadata_line,
    interpretationLine: payload.result.interpretation_line,
    framingLine: savedArtifact.framing_line || defaultFramingLine,
    bodyHtml: `
      ${renderRelatedThreadBlock(payload, { activeThreads, isImmediateResult: options.isImmediateResult })}
      ${renderSourceExcerpt(savedArtifact.source_excerpt || payload.result.source_preview || payload.source_event.source_preview || "")}
      ${renderSections(savedArtifact.sections || [])}
    `,
    actions: [
      { html: '<a class="btn btn-outline" href="/workflows/saved">View saved results</a>' },
      { html: '<button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>' },
    ],
  });

  sourcePanel.hidden = false;
  sourceText.textContent = payload.source_event.source_text;
  wireResultThreadControls(card, payload, {
    activeThreads,
    isImmediateResult: options.isImmediateResult,
  });
  wireReturnToCapture();
}

function renderPrimaryArtifact(payload, options = {}) {
  resetResultCards();
  showResultScreen();

  const card = document.getElementById("primary-artifact-card");
  const sourcePanel = document.getElementById("source-text-panel");
  const sourceText = document.getElementById("source-text-content");
  const artifact = payload.result.primary_artifact;
  const activeThreads = options.activeThreads || [];
  const bodyHtml = `
    ${renderRelatedThreadBlock(payload, { activeThreads, isImmediateResult: options.isImmediateResult })}
    ${renderSourceExcerpt(artifact.source_excerpt)}
    ${renderSections(artifact.sections || [])}
  `;

  renderResultCard(card, {
    statusLabel: artifact.status || "Saved and shaped",
    statusTone: "ready",
    kicker: "Saved result",
    title: artifact.title,
    metadataLine: buildMetadataLine(payload.source_event) || artifact.metadata_line,
    interpretationLine: payload.result.interpretation_line,
    framingLine: artifact.framing_line,
    bodyHtml,
    actions: [
      { html: '<button type="button" class="btn btn-primary" id="copy-artifact-body">Copy note</button>' },
      { html: '<a class="btn btn-outline" href="/workflows/saved">View saved results</a>' },
      { html: '<button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>' },
    ],
  });

  sourcePanel.hidden = false;
  sourceText.textContent = payload.source_event.source_text;

  document.getElementById("copy-artifact-body")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(artifact.copy_text || artifact.body || "");
    setStatusTone("Copied note.");
  });
  wireResultThreadControls(card, payload, {
    activeThreads,
    isImmediateResult: options.isImmediateResult,
  });
  wireReturnToCapture();
}

function wireReturnToCapture() {
  document.getElementById("start-another-capture")?.addEventListener("click", () => {
    history.pushState({}, "", "/workflows");
    resetResultCards();
    resetCaptureForm();
    showCaptureScreen();
    setStatus("");
    document.getElementById("capture-text")?.focus();
    syncSubmitState();
  });
}

function renderPayload(payload, options = {}) {
  setStatus("");
  if (payload.result.route_kind === "saved_note" || !payload.result.primary_artifact) {
    renderSavedNote(payload, options);
    return;
  }
  renderPrimaryArtifact(payload, options);
}

async function createCapture(text, contextHint) {
  return apiFetch(API_CAPTURES_PATH, {
    method: "POST",
    body: JSON.stringify({
      text,
      context_hint: contextHint,
    }),
  });
}

async function createAudioCapture(audioBlob, filename, contextHint) {
  const formData = new FormData();
  formData.append("file", audioBlob, filename);
  if (contextHint) {
    formData.append("context_hint", contextHint);
  }
  return apiFetch(API_CAPTURES_PATH, {
    method: "POST",
    body: formData,
  });
}

async function loadActiveThreads() {
  const payload = await apiFetch(API_CONTEXTS_PATH);
  return payload.items || [];
}

async function submitThreadDecision(captureId, action, options = {}) {
  return apiFetch(`${API_CAPTURES_PATH}/${encodeURIComponent(captureId)}/context-decision`, {
    method: "POST",
    body: JSON.stringify({
      action,
      context_id: options.contextId || null,
      new_context_title: options.newContextTitle || null,
    }),
  });
}

async function loadCaptureList() {
  setStatusTone("Loading saved results...", "working");
  const payload = await apiFetch(API_CAPTURES_PATH);
  setStatus("");
  renderSavedResultsList(payload.items || []);
}

async function loadCapture(captureId) {
  setStatusTone("Loading saved result...", "working");
  const payload = await apiFetch(`${API_CAPTURES_PATH}/${encodeURIComponent(captureId)}`);
  setStatus("");
  renderPayload(payload, { activeThreads: [], isImmediateResult: false });
}

async function submitCapture(text, contextHint) {
  submitInFlight = true;
  syncSubmitState();
  renderLoadingState();
  setStatusTone("Shaping this into a next step...", "working");

  try {
    const payload = await createCapture(text, contextHint);
    clearPendingCapture();
    const nextPath = `/workflows/result/${encodeURIComponent(payload.capture_id)}`;
    history.pushState({}, "", nextPath);
    const activeThreads = payload?.threading?.confirmed_context_id
      ? []
      : await loadActiveThreads().catch(() => []);
    setStatus("");
    renderPayload(payload, { activeThreads, isImmediateResult: true });
  } catch (error) {
    console.error("[workflows] capture submission failed", error);
    logDebug("capture_submit_failed", {
      errorMessage: error?.message || "unknown error",
    });
    showCaptureScreen();
    resetResultCards();
    const message =
      error?.message === "text too short"
        ? "Add at least a short phrase before continuing."
        : "Something went wrong. Try again.";
    setStatusTone(message, "error");
  } finally {
    submitInFlight = false;
    syncSubmitState();
  }
}

function getVoiceCaptureErrorMessage(error) {
  if (error?.message === "text too short") {
    return "No audio was captured. Try again.";
  }
  if (error?.message === "audio file is too large for inline capture") {
    return "That recording is too long for inline capture. Try a shorter note.";
  }
  if (error?.message === "transcription failed") {
    return "Could not turn that recording into text. Try again.";
  }
  if (error?.message === "unsupported audio format") {
    return "This browser recorded an unsupported audio format.";
  }
  if (error?.message === "audio file is empty") {
    return "No audio was captured. Try again.";
  }
  return "Could not save that voice note. Try again.";
}

async function submitVoiceCapture(audioBlob, mimeType, contextHint) {
  submitInFlight = true;
  setVoiceCaptureState("uploading");
  setStatusTone("Uploading voice note...", "working");
  await nextPaint();
  renderLoadingState();
  setVoiceCaptureState("processing");
  setStatusTone("Processing voice note...", "working");

  try {
    const payload = await createAudioCapture(
      audioBlob,
      buildVoiceFilename(mimeType),
      contextHint,
    );
    clearPendingCapture();
    const nextPath = `/workflows/result/${encodeURIComponent(payload.capture_id)}`;
    history.pushState({}, "", nextPath);
    const activeThreads = payload?.threading?.confirmed_context_id
      ? []
      : await loadActiveThreads().catch(() => []);
    renderPayload(payload, { activeThreads, isImmediateResult: true });
    setStatusTone("Voice note saved.");
    logDebug("voice_capture_saved", {
      inputType: payload.input_type,
      sourceInputType: payload.source_event?.input_type,
      selectedMimeType: mimeType || "(browser-default)",
    });
  } catch (error) {
    console.error("[workflows] voice capture submission failed", error);
    logDebug("voice_capture_submit_failed", {
      errorMessage: error?.message || "unknown error",
      selectedMimeType: mimeType || "(browser-default)",
    });
    showCaptureScreen();
    resetResultCards();
    setStatusTone(getVoiceCaptureErrorMessage(error), "error");
  } finally {
    submitInFlight = false;
    setVoiceCaptureState("idle");
  }
}

async function handleRecordingStopped() {
  const contextHint = document.getElementById("capture-context")?.value.trim() || "";
  const mimeType = activeRecordingMimeType || "audio/webm";
  const audioBlob = new Blob(recordingChunks, {
    type: mimeType,
  });
  releaseRecordingResources();

  if (!audioBlob.size || audioBlob.size < MIN_AUDIO_BYTES) {
    setVoiceCaptureState("idle");
    setStatusTone("No audio was captured. Try again.", "error");
    return;
  }

  await submitVoiceCapture(audioBlob, mimeType, contextHint);
}

async function startVoiceRecording() {
  if (!currentUser && !bypassRemoteAuth) {
    setStatusTone("Sign in with Google to record voice notes.", "error");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    setStatusTone("This browser cannot record audio here.", "error");
    return;
  }
  if (typeof MediaRecorder === "undefined") {
    setStatusTone("This browser does not support in-browser recording.", "error");
    return;
  }

  setVoiceCaptureState("requesting");
  setStatusTone("Requesting microphone access...", "working");

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    setVoiceCaptureState("idle");
    const denied =
      error?.name === "NotAllowedError" || error?.name === "PermissionDeniedError";
    setStatusTone(
      denied ? "Microphone access was denied." : "Could not access the microphone.",
      "error",
    );
    return;
  }

  recordingChunks = [];
  let selectedMimeType = selectVoiceMimeType();
  try {
    mediaRecorder = selectedMimeType
      ? new MediaRecorder(mediaStream, { mimeType: selectedMimeType })
      : new MediaRecorder(mediaStream);
  } catch (_error) {
    try {
      mediaRecorder = new MediaRecorder(mediaStream);
      selectedMimeType = "";
    } catch (fallbackError) {
      releaseRecordingResources();
      setVoiceCaptureState("idle");
      setStatusTone("This browser could not start recording audio.", "error");
      return;
    }
  }

  activeRecordingMimeType = mediaRecorder.mimeType || selectedMimeType || "";
  mediaRecorder.addEventListener("dataavailable", (event) => {
    if (event.data?.size) {
      recordingChunks.push(event.data);
    }
  });
  mediaRecorder.addEventListener(
    "stop",
    () => {
      handleRecordingStopped().catch((error) => {
        console.error("[workflows] recording stop handling failed", error);
        releaseRecordingResources();
        submitInFlight = false;
        setVoiceCaptureState("idle");
        showCaptureScreen();
        resetResultCards();
        setStatusTone("Could not save that voice note. Try again.", "error");
      });
    },
    { once: true },
  );
  mediaRecorder.addEventListener(
    "error",
    () => {
      releaseRecordingResources();
      submitInFlight = false;
      setVoiceCaptureState("idle");
      setStatusTone("Could not continue recording. Try again.", "error");
    },
    { once: true },
  );

  try {
    mediaRecorder.start();
  } catch (_error) {
    releaseRecordingResources();
    setVoiceCaptureState("idle");
    setStatusTone("This browser could not start recording audio.", "error");
    return;
  }

  setVoiceCaptureState("recording");
  setStatusTone("Recording...", "working");
  logDebug("voice_recording_started", {
    selectedMimeType: activeRecordingMimeType || "(browser-default)",
  });
}

function stopVoiceRecording() {
  if (!mediaRecorder || mediaRecorder.state !== "recording") {
    return;
  }
  setVoiceCaptureState("stopping");
  setStatusTone("Stopping recording...", "working");
  mediaRecorder.stop();
}

async function handleRecordTrigger() {
  if (voiceCaptureState === "recording") {
    stopVoiceRecording();
    return;
  }
  if (voiceCaptureState !== "idle" || submitInFlight) {
    return;
  }
  await startVoiceRecording();
}

function restorePendingCaptureToForm() {
  const pending = readPendingCapture();
  if (!pending) {
    return null;
  }

  const input = document.getElementById("capture-text");
  const context = document.getElementById("capture-context");
  if (input && !input.value.trim() && pending.text) {
    setPastePanelVisible(true);
    input.value = pending.text;
  }
  if (context && !context.value.trim() && pending.contextHint) {
    context.value = pending.contextHint;
  }
  return pending;
}

function wireResultThreadControls(card, payload, options = {}) {
  if (!card || !options.isImmediateResult) {
    return;
  }
  const captureId = payload?.capture_id;
  const chooser = card.querySelector(".workflows-thread-chooser");
  const keepWithThreadButton = card.querySelector("#keep-with-thread");
  const keepSeparateButton = card.querySelector("#keep-separate");
  if (!captureId || (!chooser && !keepSeparateButton && !keepWithThreadButton)) {
    return;
  }

  keepWithThreadButton?.addEventListener("click", () => {
    if (chooser) {
      chooser.hidden = !chooser.hidden;
    }
  });

  keepSeparateButton?.addEventListener("click", async () => {
    setStatusTone("Keeping this separate...", "working");
    try {
      const updated = await submitThreadDecision(captureId, "kept_separate");
      setStatusTone("Kept separate.");
      renderPayload(updated, { activeThreads: [] });
    } catch (error) {
      console.error("[workflows] keep separate failed", error);
      setStatusTone("Something went wrong. Try again.", "error");
    }
  });

  for (const button of card.querySelectorAll(".workflows-thread-option")) {
    button.addEventListener("click", async () => {
      const contextId = button.getAttribute("data-context-id");
      if (!contextId) {
        return;
      }
      setStatusTone("Saving thread choice...", "working");
      try {
        const updated = await submitThreadDecision(captureId, "confirmed", {
          contextId,
        });
        setStatusTone("Saved with thread.");
        renderPayload(updated, { activeThreads: [] });
      } catch (error) {
        console.error("[workflows] thread confirmation failed", error);
        setStatusTone("Something went wrong. Try again.", "error");
      }
    });
  }
}

async function maybeResumePendingCapture() {
  const pending = readPendingCapture();
  if (bypassRemoteAuth && pending?.shouldResume && pending.text) {
    logDebug("resume_pending_capture_local", {
      textLength: pending.text.length,
      hasContextHint: Boolean(pending.contextHint),
    });
    clearPendingCapture();
    await submitCapture(pending.text, pending.contextHint || "");
    return true;
  }
  if (!currentUser || !pending?.shouldResume || !pending.text) {
    return false;
  }
  await submitCapture(pending.text, pending.contextHint || "");
  return true;
}

async function handleSubmit(event) {
  event.preventDefault();
  const text = document.getElementById("capture-text").value.trim();
  const contextHint = document.getElementById("capture-context").value.trim();
  if (!text) {
    return;
  }

  const validationError = getCaptureValidationError(text);
  if (validationError) {
    logDebug("capture_validation_failed", {
      textLength: text.length,
      validationError,
    });
    setStatusTone(validationError, "error");
    return;
  }

  logDebug("continue_clicked", {
    textLength: text.length,
    hasContextHint: Boolean(contextHint),
    nextAction: !currentUser && !bypassRemoteAuth ? "remote_auth_start" : "submit_capture",
  });

  if (!currentUser && !bypassRemoteAuth) {
    writePendingCapture({
      text,
      contextHint,
      shouldResume: true,
      savedAt: Date.now(),
    });
    setStatusTone("Redirecting to sign in so you can save this draft.", "working");
    safeNavigate(buildAuthStartUrl(), "continue_requires_remote_auth");
    return;
  }

  await submitCapture(text, contextHint);
}

function applySignedOutState() {
  resetResultCards();
  showCaptureScreen();
  setStatus("");
  syncAuthPrompt();
  syncSubmitState();
}

async function handleCurrentRoute() {
  const route = parseWorkflowsRoute(window.location.pathname);
  if (!currentUser && !bypassRemoteAuth) {
    applySignedOutState();
    return;
  }

  if (route.screen === "result") {
    try {
      await loadCapture(route.captureId);
    } catch (error) {
      console.error("[workflows] capture load failed", error);
      renderLoadError();
      setStatus("");
    }
    return;
  }

  if (route.screen === "saved") {
    try {
      await loadCaptureList();
    } catch (error) {
      console.error("[workflows] capture list load failed", error);
      renderSavedResultsError();
      setStatus("");
    }
    return;
  }

  showCaptureScreen();
  setStatus("");
  syncSubmitState();
}

export function mountWorkflowsApp() {
  const input = document.getElementById("capture-text");
  const context = document.getElementById("capture-context");
  const showPaste = document.getElementById("show-paste");
  const recordTrigger = document.getElementById("record-trigger");
  const uploadTrigger = document.getElementById("upload-trigger");
  const form = document.getElementById("capture-form");
  const signInLink = document.getElementById("workflows-signin");

  input?.addEventListener("input", syncSubmitState);
  context?.addEventListener("input", syncSubmitState);
  showPaste?.addEventListener("click", () => {
    setPastePanelVisible(true, { focus: true });
    setStatus("");
  });
  recordTrigger?.addEventListener("click", () => {
    handleRecordTrigger().catch((error) => {
      console.error("[workflows] record trigger failed", error);
      releaseRecordingResources();
      submitInFlight = false;
      setVoiceCaptureState("idle");
      setStatusTone("Could not save that voice note. Try again.", "error");
    });
  });
  uploadTrigger?.addEventListener("click", () => {
    setStatus("File upload is not available in this slice yet.");
  });
  form?.addEventListener("submit", handleSubmit);
  if (!bypassRemoteAuth) {
    signInLink?.addEventListener("click", () => {
      const text = input?.value.trim();
      const contextHint = context?.value.trim() || "";
      if (text) {
        writePendingCapture({
          text,
          contextHint,
          shouldResume: false,
          savedAt: Date.now(),
        });
      }
      logDebug("sign_in_link_clicked", {
        signInHref: signInLink.href,
      });
    });
  }
  window.addEventListener("popstate", () => {
    handleCurrentRoute();
  });

  if (callbackToken) {
    setStatus("Completing sign-in...");
    signInWithCustomToken(auth, callbackToken)
      .then(() => {
        const cleanUrl = new URL(window.location.href);
        cleanUrl.searchParams.delete("token");
        window.history.replaceState({}, "", cleanUrl.toString());
      })
      .catch(() => {
        setStatus("Could not complete sign-in.");
      });
  }

  if (authError) {
    setStatus("Google sign-in did not complete. Please try again.");
  }

  onAuthStateChanged(auth, async (user) => {
    currentUser = user;
    restorePendingCaptureToForm();
    syncSubmitState();
    if (initialRouteHandled && !user) {
      applySignedOutState();
      return;
    }
    initialRouteHandled = true;
    if (await maybeResumePendingCapture()) {
      return;
    }
    await handleCurrentRoute();
  });

  restorePendingCaptureToForm();
  syncSubmitState();
  renderDebugState("mount", {
    callbackTokenPresent: Boolean(callbackToken),
    authErrorPresent: Boolean(authError),
  });
}

mountWorkflowsApp();
