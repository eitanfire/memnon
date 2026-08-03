import { initializeApp, getApps, getApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
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

const app = getApps().length ? getApp() : initializeApp(firebaseConfig);
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
const SAVED_RESULTS_PATH = "/today/saved";
const PENDING_CAPTURE_KEY = "memnon_workflows_pending_capture_v1";
const LOCAL_DEV_ID_TOKEN = "local-dev-token";
const SCRIPT_LOADED_AT = new Date().toISOString();
const SOURCE_EXCERPT_LABEL = "From your note";
const KEY_POINT_LABEL = "Key point";
const NEXT_STEP_LABEL = "Next step";
const WHY_KEEP_THIS_LABEL = "Why keep this";
const CONTEXTUAL_SUGGESTION_COPY = {
  draft_social_post: {
    copy: "This could become a social post.",
    actionLabel: "Draft social post",
  },
  analyze_professionally: {
    copy: "Analyze this through your professional lens.",
    actionLabel: "Analyze professionally",
  },
};
const MAX_TEXT_FILE_BYTES = 512 * 1024;
const SUPPORTED_TEXT_FILE_EXTENSIONS = new Set([".txt", ".md"]);
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
let selectedUploadFile = null;
const activeVoiceReviewObjectUrls = new Set();

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
    pathname === "/today.html" ? "/today" : pathname.replace(/\/+$/, "") || "/today";
  if (normalized === SAVED_RESULTS_PATH) {
    return { screen: "saved" };
  }
  const match = normalized.match(/^\/today\/result\/([^/]+)$/);
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

function normalizedUploadExtension(filename) {
  const normalized = String(filename || "").toLowerCase();
  const dotIndex = normalized.lastIndexOf(".");
  return dotIndex >= 0 ? normalized.slice(dotIndex) : "";
}

function resolveCaptureSource({ text, selectedFile }) {
  if (selectedFile) {
    const hasPastedText = Boolean((text || "").trim());
    return {
      activeSource: "file",
      message: hasPastedText
        ? "File selected. Pasted text won't be submitted unless you clear the file."
        : "File selected.",
    };
  }
  if ((text || "").trim()) {
    return {
      activeSource: "text",
      message: "",
    };
  }
  return {
    activeSource: "",
    message: "",
  };
}

function syncFileSelectionUi() {
  const panel = document.getElementById("file-selection-state");
  const name = document.getElementById("file-selection-name");
  const copy = document.getElementById("file-selection-copy");
  const text = document.getElementById("capture-text")?.value || "";
  if (!panel || !name || !copy) {
    return;
  }
  if (!selectedUploadFile) {
    panel.hidden = true;
    name.textContent = "";
    copy.textContent = "";
    return;
  }
  const source = resolveCaptureSource({ text, selectedFile: selectedUploadFile });
  panel.hidden = false;
  name.textContent = selectedUploadFile.name;
  copy.textContent = source.message;
}

function clearSelectedUpload() {
  selectedUploadFile = null;
  const input = document.getElementById("capture-file");
  if (input) {
    input.value = "";
  }
  syncFileSelectionUi();
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
  const fileInput = document.getElementById("capture-file");
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
  const selectedFileError = validateSelectedFile(selectedUploadFile);
  const sourceState = resolveCaptureSource({
    text: input?.value || "",
    selectedFile: selectedUploadFile,
  });
  submit.disabled = submitInFlight || voiceBusy || !sourceState.activeSource || Boolean(selectedFileError);
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
  if (fileInput) {
    fileInput.disabled = submitInFlight || voiceBusy;
  }
  if (context) {
    context.disabled = submitInFlight || blockingVoiceState;
  }
  if (form) {
    form.setAttribute("aria-busy", submitInFlight || voiceBusy ? "true" : "false");
  }
  syncFileSelectionUi();
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

async function apiFetchBlob(path, init = {}) {
  const token = await getToken();
  const headers = new Headers(init.headers || {});
  headers.set("Authorization", `Bearer ${token}`);

  logDebug("api_fetch", {
    method: init.method || "GET",
    target: `${API_ORIGIN}${path}`,
  });

  const response = await fetch(`${API_ORIGIN}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return response.blob();
}

function releaseVoiceReviewObjectUrls() {
  activeVoiceReviewObjectUrls.forEach((objectUrl) => URL.revokeObjectURL(objectUrl));
  activeVoiceReviewObjectUrls.clear();
}

function resetResultCards() {
  releaseVoiceReviewObjectUrls();
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
  clearSelectedUpload();
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
  if (inputType === "file") {
    return "Uploaded file";
  }
  return "Saved note";
}

function compactSourceFilename(filename, maxLength = 40) {
  const normalized = String(filename || "").trim();
  if (!normalized) {
    return "";
  }
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`;
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
  return parts.join(" · ");
}

function resolveResultSourceEvent(payload) {
  const payloadSourceEvent = payload?.source_event || {};
  const manifestSourceEvent = payload?.event_manifest?.source_event || {};
  return {
    ...manifestSourceEvent,
    ...payloadSourceEvent,
    capture_id: payloadSourceEvent.capture_id || payload?.capture_id || manifestSourceEvent.capture_id || "",
    input_type: payloadSourceEvent.input_type || payload?.input_type || manifestSourceEvent.input_type || "",
    created_at: payloadSourceEvent.created_at || payload?.created_at || manifestSourceEvent.created_at || "",
    source_filename: payloadSourceEvent.source_filename || manifestSourceEvent.source_filename || "",
    source_text: payloadSourceEvent.source_text || manifestSourceEvent.source_text || "",
    source_preview: payloadSourceEvent.source_preview || manifestSourceEvent.source_preview || "",
    source_audio_storage_path: payloadSourceEvent.source_audio_storage_path || manifestSourceEvent.source_audio_storage_path || "",
    source_audio_content_type: payloadSourceEvent.source_audio_content_type || manifestSourceEvent.source_audio_content_type || "",
  };
}

function resolveResultMetadataLine(payload, artifact = {}) {
  return buildMetadataLine(resolveResultSourceEvent(payload)) || artifact.metadata_line || "";
}

function renderVoiceReview(sourceEvent) {
  if (sourceEvent?.input_type !== "voice" || !sourceEvent?.capture_id || !sourceEvent?.source_audio_storage_path) {
    return "";
  }
  const apiOriginPrefix = typeof API_ORIGIN === "string" ? API_ORIGIN : "";
  const audioEndpoint = `${apiOriginPrefix}${API_CAPTURES_PATH}/${encodeURIComponent(sourceEvent.capture_id)}/source-audio`;
  return `
    <div class="workflows-voice-review">
      <p class="workflows-source-label">Review captured audio</p>
      <audio controls preload="none" data-source-audio-endpoint="${escapeHtml(audioEndpoint)}"></audio>
    </div>
  `;
}

async function wireVoiceReviewAudio(card) {
  const sourceAudioElement = card?.querySelector("audio[data-source-audio-endpoint]");
  if (!sourceAudioElement) {
    return;
  }
  if (
    sourceAudioElement.dataset.sourceAudioLoaded === "true"
    || sourceAudioElement.dataset.sourceAudioLoading === "true"
  ) {
    return;
  }

  sourceAudioElement.dataset.sourceAudioLoading = "true";
  try {
    const blob = await apiFetchBlob(sourceAudioElement.dataset.sourceAudioEndpoint);
    const objectUrl = URL.createObjectURL(blob);
    if (!document.contains(sourceAudioElement)) {
      URL.revokeObjectURL(objectUrl);
      return;
    }
    activeVoiceReviewObjectUrls.add(objectUrl);
    sourceAudioElement.src = objectUrl;
    sourceAudioElement.dataset.sourceAudioLoaded = "true";
    sourceAudioElement.load();
  } catch (error) {
    console.error("[workflows] voice review audio load failed", error);
    sourceAudioElement.closest(".workflows-voice-review")?.setAttribute("hidden", "");
  } finally {
    delete sourceAudioElement.dataset.sourceAudioLoading;
  }
}

function buildSourceExcerptLabel(sourceEvent) {
  if (sourceEvent?.input_type === "file" && sourceEvent?.source_filename) {
    return `From ${compactSourceFilename(sourceEvent.source_filename)}`;
  }
  return SOURCE_EXCERPT_LABEL;
}

function renderSourceExcerpt(text, sourceEvent) {
  if (!text) {
    return "";
  }
  return `
    <div class="workflows-source-excerpt">
      <p class="workflows-source-label">${escapeHtml(buildSourceExcerptLabel(sourceEvent))}</p>
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
      <form class="workflows-thread-create-form">
        <label class="workflows-visually-hidden" for="new-thread-title">Create new thread</label>
        <input
          id="new-thread-title"
          name="new_thread_title"
          type="text"
          maxlength="80"
          placeholder="Create new thread"
        />
        <button type="submit" class="btn btn-outline" data-create-context>Create</button>
      </form>
    </div>
  `;
}

function isImmediateResultNavigation(payload) {
  return Boolean(payload?.threading?.suggestion_active);
}

function renderRelatedThreadSuggestion(payload, threads = []) {
  const relatedThread = payload?.result?.related_thread || {};
  if (!payload?.threading?.suggestion_active || !relatedThread.suggested_title) {
    return "";
  }

  return `
    <div class="workflows-related-thread-block" data-thread-ui="compact">
      <p class="workflows-related-thread-copy">This looks related to ${escapeHtml(relatedThread.suggested_title)}.</p>
      <div class="workflows-related-thread-actions">
        <button type="button" class="btn btn-primary" id="confirm-related-thread">Continue there</button>
        <button type="button" class="btn btn-quiet" id="reveal-thread-escape">Not this</button>
      </div>
      <div class="workflows-related-thread-escape" hidden>
        <button type="button" class="btn btn-outline" id="keep-thread-separate">Keep separate</button>
        ${threads.length > 1 ? '<button type="button" class="btn btn-quiet" id="choose-another-thread">Choose another</button>' : ""}
      </div>
      <div class="workflows-thread-chooser-slot" data-thread-count="${threads.length}"></div>
    </div>
  `;
}

function renderConfirmedThreadDisplay(payload) {
  const relatedThread = payload?.result?.related_thread || {};
  if (!relatedThread.confirmed_title) {
    return "";
  }
  return `<p class="workflows-related-thread-confirmed">Related to ${escapeHtml(relatedThread.confirmed_title)}</p>`;
}

function renderSummary(artifact) {
  const summary = artifact?.summary;
  if (!summary) {
    return "";
  }
  const lines = summary
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const blocks = [];
  let bulletBuffer = [];
  const flushBullets = () => {
    if (bulletBuffer.length) {
      blocks.push(`<ul>${bulletBuffer.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`);
      bulletBuffer = [];
    }
  };
  for (const line of lines) {
    const bulletMatch = line.match(/^[-*]\s+(.*)$/);
    if (bulletMatch) {
      bulletBuffer.push(bulletMatch[1]);
    } else {
      flushBullets();
      blocks.push(`<p>${escapeHtml(line)}</p>`);
    }
  }
  flushBullets();
  return `<div class="workflows-summary">${blocks.join("")}</div>`;
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

function renderContextualSuggestions(payload, options = {}) {
  if (!options.isImmediateResult) {
    return "";
  }

  const suggestionCopy =
    typeof CONTEXTUAL_SUGGESTION_COPY !== "undefined"
      ? CONTEXTUAL_SUGGESTION_COPY
      : {
          draft_social_post: {
            copy: "This could become a social post.",
            actionLabel: "Draft social post",
          },
          analyze_professionally: {
            copy: "Analyze this through your professional lens.",
            actionLabel: "Analyze professionally",
          },
        };

  const suggestions = (payload?.result?.contextual_suggestions || [])
    .map((item) => {
      const defaults = suggestionCopy[item?.type] || {};
      return {
        type: item?.type || "",
        copy: item?.copy || defaults.copy || "",
        action_label: item?.action_label || defaults.actionLabel || "",
      };
    })
    .filter((item) => item.type && item.copy && item.action_label);
  if (!suggestions.length) {
    return "";
  }

  return `
    <section class="workflows-contextual-suggestions" aria-label="${suggestions.length > 1 ? "Optional next steps" : "Optional next step"}">
      <p class="workflows-contextual-suggestions-label">${suggestions.length > 1 ? "Optional next steps" : "Optional next step"}</p>
      <div class="workflows-contextual-suggestions-list">
        ${suggestions
          .slice(0, 2)
          .map(
            (item) => `
              <div class="workflows-contextual-suggestion-row">
                <p class="workflows-contextual-suggestion-copy">${escapeHtml(item.copy)}</p>
                <button
                  type="button"
                  class="btn btn-outline workflows-contextual-suggestion-button"
                  data-contextual-suggestion-type="${escapeHtml(item.type)}"
                >
                  ${escapeHtml(item.action_label)}
                </button>
              </div>
            `,
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderResultFeedback(payload, options = {}) {
  if (!payload?.capture_id) {
    return "";
  }

  const feedbackChoice = payload.feedback_choice || "";
  const feedbackNote = payload.feedback_note || "";
  const isUsefulSelected = feedbackChoice === "useful";
  const isNotUsefulSelected = feedbackChoice === "not_useful";

  return `
    <section class="workflows-feedback-block" aria-label="Result feedback">
      <p class="workflows-feedback-label">How was this result?</p>
      <div class="workflows-feedback-actions" role="group" aria-label="Result feedback options">
        <button
          type="button"
          class="btn btn-outline workflows-feedback-button ${isUsefulSelected ? "is-selected" : ""}"
          data-feedback-choice="useful"
          aria-pressed="${isUsefulSelected ? "true" : "false"}"
        >
          Useful
        </button>
        <button
          type="button"
          class="btn btn-outline workflows-feedback-button ${isNotUsefulSelected ? "is-selected" : ""}"
          data-feedback-choice="not_useful"
          aria-pressed="${isNotUsefulSelected ? "true" : "false"}"
        >
          Not useful
        </button>
      </div>
      ${renderFeedbackNoteForm(payload.capture_id, feedbackChoice, feedbackNote)}
    </section>
  `;
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
        .map((item) => {
          const isUsefulSelected = item.feedback_choice === "useful";
          const isNotUsefulSelected = item.feedback_choice === "not_useful";
          return `
            <article class="workflows-saved-results-item" data-capture-id="${escapeHtml(item.capture_id || "")}">
              <div class="workflows-saved-results-item-header">
                <h3>${escapeHtml(item.title || "Saved note")}${item.looks_like_dev_data ? ' <span class="workflows-dev-data-tag">Dev/QA</span>' : ""}${item.looks_like_possible_duplicate ? ' <span class="workflows-duplicate-tag">Possible duplicate</span>' : ""}${!item.looks_like_dev_data && !item.feedback_choice ? ' <span class="workflows-needs-score-tag">Needs a score</span>' : ""}</h3>
                <a class="workflows-saved-results-link" href="${escapeHtml(item.next_route || "/today")}">Open</a>
              </div>
              <p class="workflows-saved-results-meta">${escapeHtml(item.metadata_line || item.status || "")}</p>
              <div class="workflows-feedback-actions workflows-feedback-actions--compact" role="group" aria-label="Result feedback options">
                <button
                  type="button"
                  class="btn btn-outline workflows-feedback-button ${isUsefulSelected ? "is-selected" : ""}"
                  data-feedback-choice="useful"
                  data-capture-id="${escapeHtml(item.capture_id || "")}"
                  aria-pressed="${isUsefulSelected ? "true" : "false"}"
                >
                  Useful
                </button>
                <button
                  type="button"
                  class="btn btn-outline workflows-feedback-button ${isNotUsefulSelected ? "is-selected" : ""}"
                  data-feedback-choice="not_useful"
                  data-capture-id="${escapeHtml(item.capture_id || "")}"
                  aria-pressed="${isNotUsefulSelected ? "true" : "false"}"
                >
                  Not useful
                </button>
              </div>
              ${renderFeedbackNoteForm(item.capture_id, item.feedback_choice, item.feedback_note, { compact: true })}
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderFeedbackNoteForm(captureId, feedbackChoice, feedbackNote, options = {}) {
  if (!feedbackChoice || !captureId) {
    return "";
  }
  const inputId = `feedback-note-input-${captureId}`;
  const compactClass = options.compact ? " workflows-feedback-note-form--compact" : "";
  return `
    <form class="workflows-feedback-note-form${compactClass}" data-feedback-note-form data-capture-id="${escapeHtml(captureId)}">
      <label class="workflows-visually-hidden" for="${escapeHtml(inputId)}">Why?</label>
      <input
        id="${escapeHtml(inputId)}"
        name="feedback_note"
        type="text"
        maxlength="500"
        placeholder="Say why (optional)"
        value="${escapeHtml(feedbackNote || "")}"
      />
      <button type="submit" class="btn btn-quiet">Save</button>
    </form>
  `;
}

function wireSavedResultsListFeedbackControls(card) {
  if (!card) {
    return;
  }

  for (const button of card.querySelectorAll("[data-feedback-choice][data-capture-id]")) {
    if (button.dataset.wired === "true") {
      continue;
    }
    button.dataset.wired = "true";
    button.addEventListener("click", async () => {
      const feedbackChoice = button.getAttribute("data-feedback-choice");
      const captureId = button.getAttribute("data-capture-id");
      if (!feedbackChoice || !captureId) {
        return;
      }
      const item = card.querySelector(`.workflows-saved-results-item[data-capture-id="${CSS.escape(captureId)}"]`);
      const existingNoteInput = item?.querySelector("[data-feedback-note-form] input");
      const existingNote = existingNoteInput?.value || "";

      setStatusTone("Saving feedback...", "working");
      try {
        await submitFeedbackChoice(captureId, feedbackChoice, existingNote);
        setStatusTone("Feedback saved.");
        for (const feedbackButton of item?.querySelectorAll("[data-feedback-choice]") || []) {
          const isSelected = feedbackButton.getAttribute("data-feedback-choice") === feedbackChoice;
          feedbackButton.classList.toggle("is-selected", isSelected);
          feedbackButton.setAttribute("aria-pressed", isSelected ? "true" : "false");
        }
        item?.querySelector(".workflows-needs-score-tag")?.remove();
        if (item && !item.querySelector("[data-feedback-note-form]")) {
          const actionsBlock = item.querySelector(".workflows-feedback-actions");
          actionsBlock?.insertAdjacentHTML(
            "afterend",
            renderFeedbackNoteForm(captureId, feedbackChoice, existingNote, { compact: true }),
          );
          wireSavedResultsListFeedbackControls(card);
        }
      } catch (error) {
        console.error("[workflows] feedback submission failed", error);
        setStatusTone("Could not save feedback. Try again.", "error");
      }
    });
  }

  for (const noteForm of card.querySelectorAll("[data-feedback-note-form]")) {
    if (noteForm.dataset.wired === "true") {
      continue;
    }
    noteForm.dataset.wired = "true";
    noteForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const captureId = noteForm.getAttribute("data-capture-id");
      const item = card.querySelector(`.workflows-saved-results-item[data-capture-id="${CSS.escape(captureId)}"]`);
      const selectedButton = item?.querySelector("[data-feedback-choice].is-selected");
      const feedbackChoice = selectedButton?.getAttribute("data-feedback-choice") || "";
      if (!captureId || !feedbackChoice) {
        return;
      }
      const input = noteForm.querySelector("input");
      const feedbackNote = (input?.value || "").trim();

      setStatusTone("Saving note...", "working");
      try {
        await submitFeedbackChoice(captureId, feedbackChoice, feedbackNote);
        setStatusTone("Note saved.");
      } catch (error) {
        console.error("[workflows] feedback note submission failed", error);
        setStatusTone("Could not save note. Try again.", "error");
      }
    });
  }
}

function renderSavedResultsList(items) {
  resetResultCards();
  showResultScreen();
  const card = document.getElementById("primary-artifact-card");
  renderResultCard(card, {
    statusLabel: "Saved results",
    statusTone: "saved",
    kicker: "History",
    title: "Saved results",
    framingLine: "Reopen a saved result.",
    bodyHtml: renderSavedResultsBody(items),
    actions: [
      { html: '<button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>' },
    ],
  });
  wireSavedResultsListFeedbackControls(card);
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
  const sourceEvent = resolveResultSourceEvent(payload);
  const defaultFramingLine =
    savedArtifact.state === "weak_signal"
      ? "This is a small note worth preserving."
      : "This note is worth keeping, but it needs clearer direction before acting on it.";
  const savedStatusLabel =
    savedArtifact.state === "needs_direction" ? "Needs light direction" : "Saved for later";
  const savedKicker =
    savedArtifact.state === "needs_direction" ? "Worth keeping" : "Kept as a saved note";
  renderResultCard(card, {
    statusLabel: savedArtifact.status || savedStatusLabel,
    statusTone: "saved",
    kicker: savedKicker,
    title: savedArtifact.title || "Saved note",
    metadataLine: resolveResultMetadataLine(payload, savedArtifact),
    interpretationLine: payload.result.interpretation_line,
    framingLine: savedArtifact.framing_line || defaultFramingLine,
    bodyHtml: `
      ${renderRelatedThreadSuggestion(payload, activeThreads)}
      ${renderConfirmedThreadDisplay(payload)}
      ${renderVoiceReview(sourceEvent)}
      ${renderSourceExcerpt(
        savedArtifact.source_excerpt || payload.result.source_preview || sourceEvent.source_preview || "",
        sourceEvent,
      )}
      ${renderSections(savedArtifact.sections || [])}
      ${renderContextualSuggestions(payload, options)}
      ${renderResultFeedback(payload, options)}
    `,
    actions: [
      { html: '<a class="btn btn-outline" href="/today/saved">View saved results</a>' },
      { html: '<button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>' },
    ],
  });

  sourcePanel.hidden = false;
  sourceText.textContent = sourceEvent.source_text;
  wireResultThreadControls(card, payload, {
    activeThreads,
    isImmediateResult: options.isImmediateResult,
  });
  wireResultContextualSuggestionControls(card, payload, {
    isImmediateResult: options.isImmediateResult,
  });
  wireResultFeedbackControls(card, payload, {
    activeThreads,
    isImmediateResult: options.isImmediateResult,
  });
  void wireVoiceReviewAudio(card);
  wireReturnToCapture();
}

function isLegacySchemaArtifact(artifact) {
  if (!artifact || artifact.summary) {
    return false;
  }
  return (artifact.sections || []).some((section) => section.label === "Key point");
}

function renderPrimaryArtifact(payload, options = {}) {
  resetResultCards();
  showResultScreen();

  const card = document.getElementById("primary-artifact-card");
  const sourcePanel = document.getElementById("source-text-panel");
  const sourceText = document.getElementById("source-text-content");
  const artifact = payload.result.primary_artifact;
  const activeThreads = options.activeThreads || [];
  const sourceEvent = resolveResultSourceEvent(payload);
  const bodyHtml = `
    ${renderRelatedThreadSuggestion(payload, activeThreads)}
    ${renderConfirmedThreadDisplay(payload)}
    ${renderVoiceReview(sourceEvent)}
    ${renderSummary(artifact)}
    ${renderSourceExcerpt(artifact.source_excerpt, sourceEvent)}
    ${renderSections(artifact.sections || [])}
    ${renderContextualSuggestions(payload, options)}
    ${renderResultFeedback(payload, options)}
  `;

  const actions = [
    { html: `<button type="button" class="btn btn-primary" id="copy-artifact-body">${escapeHtml(artifact.primary_action || "Copy note")}</button>` },
    { html: '<a class="btn btn-outline" href="/today/saved">View saved results</a>' },
    { html: '<button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>' },
  ];
  if (isLegacySchemaArtifact(artifact)) {
    actions.push({
      html: '<button type="button" class="btn btn-quiet" id="regenerate-capture">Update to new format</button>',
    });
  }

  renderResultCard(card, {
    statusLabel: artifact.status || "Saved and shaped",
    statusTone: "ready",
    kicker: "Saved result",
    title: artifact.title,
    metadataLine: resolveResultMetadataLine(payload, artifact),
    interpretationLine: payload.result.interpretation_line,
    bodyHtml,
    actions,
  });

  sourcePanel.hidden = false;
  sourceText.textContent = sourceEvent.source_text;

  document.getElementById("copy-artifact-body")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(artifact.copy_text || artifact.body || "");
    setStatusTone("Copied note.");
  });
  document.getElementById("regenerate-capture")?.addEventListener("click", async () => {
    setStatusTone("Updating to the new format...", "working");
    try {
      const updated = await submitRegenerateCapture(payload.capture_id);
      setStatusTone("Updated.");
      renderPayload(updated, {
        activeThreads,
        isImmediateResult: Boolean(options.isImmediateResult),
      });
    } catch (error) {
      console.error("[workflows] regenerate failed", error);
      setStatusTone("Could not update this result. Try again.", "error");
    }
  });
  wireResultThreadControls(card, payload, {
    activeThreads,
    isImmediateResult: options.isImmediateResult,
  });
  wireResultContextualSuggestionControls(card, payload, {
    isImmediateResult: options.isImmediateResult,
  });
  wireResultFeedbackControls(card, payload, {
    activeThreads,
    isImmediateResult: options.isImmediateResult,
  });
  void wireVoiceReviewAudio(card);
  wireReturnToCapture();
}

function wireReturnToCapture() {
  document.getElementById("start-another-capture")?.addEventListener("click", () => {
    history.pushState({}, "", "/today");
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

async function createFileCapture(file, contextHint) {
  const formData = new FormData();
  formData.append("file", file, file.name);
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

async function submitRegenerateCapture(captureId) {
  return apiFetch(`${API_CAPTURES_PATH}/${encodeURIComponent(captureId)}/regenerate`, {
    method: "POST",
  });
}

async function submitFeedbackChoice(captureId, feedbackChoice, feedbackNote = "") {
  return apiFetch(`${API_CAPTURES_PATH}/${encodeURIComponent(captureId)}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      feedback_choice: feedbackChoice,
      feedback_note: feedbackNote,
    }),
  });
}

async function submitContextualSuggestion(captureId, suggestionType) {
  return apiFetch(`${API_CAPTURES_PATH}/${encodeURIComponent(captureId)}/suggestions`, {
    method: "POST",
    body: JSON.stringify({
      suggestion_type: suggestionType,
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
    const nextPath = `/today/result/${encodeURIComponent(payload.capture_id)}`;
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

function getFileCaptureErrorMessage(error) {
  if (error?.message === "File must be .txt or .md.") {
    return error.message;
  }
  if (error?.message === "File is too large. Maximum size is 512 KB.") {
    return error.message;
  }
  if (error?.message === "We couldn’t read text from this file.") {
    return error.message;
  }
  return "Something went wrong. Try again.";
}

function validateSelectedFile(file) {
  if (!file) {
    return "";
  }
  const extension = normalizedUploadExtension(file.name);
  if (!SUPPORTED_TEXT_FILE_EXTENSIONS.has(extension)) {
    return "File must be .txt or .md.";
  }
  if (file.size > MAX_TEXT_FILE_BYTES) {
    return "File is too large. Maximum size is 512 KB.";
  }
  return "";
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
    const nextPath = `/today/result/${encodeURIComponent(payload.capture_id)}`;
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

async function submitFileCapture(file, contextHint) {
  submitInFlight = true;
  syncSubmitState();
  renderLoadingState();
  setStatusTone("Uploading file...", "working");

  try {
    const payload = await createFileCapture(file, contextHint);
    clearPendingCapture();
    const nextPath = `/today/result/${encodeURIComponent(payload.capture_id)}`;
    history.pushState({}, "", nextPath);
    const activeThreads = payload?.threading?.confirmed_context_id
      ? []
      : await loadActiveThreads().catch(() => []);
    setStatus("");
    renderPayload(payload, { activeThreads, isImmediateResult: true });
  } catch (error) {
    console.error("[workflows] file capture submission failed", error);
    logDebug("file_capture_submit_failed", {
      errorMessage: error?.message || "unknown error",
      filename: file?.name || "",
    });
    showCaptureScreen();
    resetResultCards();
    setStatusTone(getFileCaptureErrorMessage(error), "error");
  } finally {
    submitInFlight = false;
    syncSubmitState();
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

function handleFileSelection(event) {
  const file = event?.target?.files?.[0] || null;
  selectedUploadFile = file;
  const validationError = validateSelectedFile(selectedUploadFile);
  if (validationError) {
    setStatusTone(validationError, "error");
  } else if (selectedUploadFile) {
    setStatus("");
  }
  syncSubmitState();
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
  if (!card || !options.isImmediateResult || !isImmediateResultNavigation(payload)) {
    return;
  }
  const captureId = payload?.capture_id;
  const suggestedContextId = payload?.threading?.suggested_context_id || "";
  const chooserSlot = card.querySelector(".workflows-thread-chooser-slot");
  let chooser = card.querySelector(".workflows-thread-chooser");
  const confirmRelatedThreadButton = card.querySelector("#confirm-related-thread");
  const revealEscapeButton = card.querySelector("#reveal-thread-escape");
  const chooseAnotherThreadButton = card.querySelector("#choose-another-thread");
  const keepSeparateButton = card.querySelector("#keep-thread-separate");
  const escapePanel = card.querySelector(".workflows-related-thread-escape");
  if (!captureId || (!chooserSlot && !keepSeparateButton && !confirmRelatedThreadButton)) {
    return;
  }

  function wireChooserControls() {
    chooser = card.querySelector(".workflows-thread-chooser");
    const createForm = card.querySelector(".workflows-thread-create-form");
    const createInput = createForm?.querySelector('input[name="new_thread_title"]');

    for (const button of card.querySelectorAll(".workflows-thread-option")) {
      if (button.dataset.wired === "true") {
        continue;
      }
      button.dataset.wired = "true";
      button.addEventListener("click", async () => {
        const contextId = button.getAttribute("data-context-id");
        if (!contextId) {
          return;
        }
        setStatusTone("Saving thread choice...", "working");
        try {
          const updated = await submitThreadDecision(captureId, "selected_different_context", {
            contextId,
          });
          setStatusTone("Saved with thread.");
          renderPayload(updated, {
            activeThreads: [],
            isImmediateResult: true,
          });
        } catch (error) {
          console.error("[workflows] thread confirmation failed", error);
          setStatusTone("Something went wrong. Try again.", "error");
        }
      });
    }

    if (createForm && createForm.dataset.wired !== "true") {
      createForm.dataset.wired = "true";
      createForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const newContextTitle = createInput?.value.trim() || "";
        if (newContextTitle.length < 2) {
          setStatusTone("Add a thread title first.", "error");
          createInput?.focus();
          return;
        }
        setStatusTone("Creating thread...", "working");
        try {
          const updated = await submitThreadDecision(captureId, "created_new_context", {
            newContextTitle,
          });
          setStatusTone("Saved with thread.");
          renderPayload(updated, {
            activeThreads: [],
            isImmediateResult: true,
          });
        } catch (error) {
          console.error("[workflows] thread creation failed", error);
          setStatusTone("Something went wrong. Try again.", "error");
        }
      });
    }

    return { chooser, createInput };
  }

  confirmRelatedThreadButton?.addEventListener("click", async () => {
    if (!suggestedContextId) {
      return;
    }
    setStatusTone("Saving thread choice...", "working");
    try {
      const updated = await submitThreadDecision(captureId, "confirmed", {
        contextId: suggestedContextId,
      });
      setStatusTone("Saved with thread.");
      renderPayload(updated, {
        activeThreads: [],
        isImmediateResult: true,
      });
    } catch (error) {
      console.error("[workflows] thread confirmation failed", error);
      setStatusTone("Something went wrong. Try again.", "error");
    }
  });

  revealEscapeButton?.addEventListener("click", () => {
    if (!escapePanel) {
      return;
    }
    escapePanel.hidden = !escapePanel.hidden;
    if (escapePanel.hidden && chooser) {
      chooser.hidden = true;
    }
  });

  chooseAnotherThreadButton?.addEventListener("click", () => {
    if (!chooser && chooserSlot) {
      chooserSlot.innerHTML = renderThreadChooser(options.activeThreads || []);
      const wiredChooser = wireChooserControls();
      chooser = wiredChooser?.chooser || card.querySelector(".workflows-thread-chooser");
      if (chooser) {
        chooser.hidden = false;
      }
      wiredChooser?.createInput?.focus();
      return;
    }
    if (chooser) {
      chooser.hidden = !chooser.hidden;
    }
  });

  keepSeparateButton?.addEventListener("click", async () => {
    setStatusTone("Keeping this separate...", "working");
    try {
      const updated = await submitThreadDecision(captureId, "kept_separate");
      setStatusTone("Kept separate.");
      renderPayload(updated, {
        activeThreads: [],
        isImmediateResult: true,
      });
    } catch (error) {
      console.error("[workflows] keep separate failed", error);
      setStatusTone("Something went wrong. Try again.", "error");
    }
  });
}

function wireResultFeedbackControls(card, payload, options = {}) {
  if (!card) {
    return;
  }
  const captureId = payload?.capture_id;
  if (!captureId) {
    return;
  }

  for (const button of card.querySelectorAll("[data-feedback-choice]")) {
    if (button.dataset.wired === "true") {
      continue;
    }
    button.dataset.wired = "true";
    button.addEventListener("click", async () => {
      const feedbackChoice = button.getAttribute("data-feedback-choice");
      if (!feedbackChoice) {
        return;
      }

      setStatusTone("Saving feedback...", "working");
      try {
        const updated = await submitFeedbackChoice(captureId, feedbackChoice, payload.feedback_note || "");
        setStatusTone("Feedback saved.");
        renderPayload(updated, {
          activeThreads: options.activeThreads || [],
          isImmediateResult: Boolean(options.isImmediateResult),
        });
      } catch (error) {
        console.error("[workflows] feedback submission failed", error);
        setStatusTone("Could not save feedback. Try again.", "error");
      }
    });
  }

  const noteForm = card.querySelector("[data-feedback-note-form]");
  if (noteForm && noteForm.dataset.wired !== "true") {
    noteForm.dataset.wired = "true";
    noteForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const feedbackChoice = payload.feedback_choice || "";
      if (!feedbackChoice) {
        return;
      }
      const input = noteForm.querySelector("input");
      const feedbackNote = (input?.value || "").trim();

      setStatusTone("Saving note...", "working");
      try {
        const updated = await submitFeedbackChoice(captureId, feedbackChoice, feedbackNote);
        setStatusTone("Note saved.");
        renderPayload(updated, {
          activeThreads: options.activeThreads || [],
          isImmediateResult: Boolean(options.isImmediateResult),
        });
      } catch (error) {
        console.error("[workflows] feedback note submission failed", error);
        setStatusTone("Could not save note. Try again.", "error");
      }
    });
  }
}

function wireResultContextualSuggestionControls(card, payload, options = {}) {
  if (!card || !options.isImmediateResult) {
    return;
  }
  const captureId = payload?.capture_id;
  if (!captureId) {
    return;
  }

  for (const button of card.querySelectorAll("[data-contextual-suggestion-type]")) {
    if (button.dataset.wired === "true") {
      continue;
    }
    button.dataset.wired = "true";
    button.addEventListener("click", async () => {
      const suggestionType = button.getAttribute("data-contextual-suggestion-type");
      if (!suggestionType) {
        return;
      }

      setStatusTone("Shaping that result...", "working");
      try {
        const created = await submitContextualSuggestion(captureId, suggestionType);
        const nextPath = `/today/result/${encodeURIComponent(created.capture_id)}`;
        history.pushState({}, "", nextPath);
        setStatus("");
        renderPayload(created, {
          activeThreads: [],
          isImmediateResult: true,
        });
      } catch (error) {
        console.error("[workflows] contextual suggestion failed", error);
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
  const sourceState = resolveCaptureSource({
    text,
    selectedFile: selectedUploadFile,
  });
  if (!sourceState.activeSource) {
    return;
  }

  if (sourceState.activeSource === "file" && selectedUploadFile) {
    const fileValidationError = validateSelectedFile(selectedUploadFile);
    if (fileValidationError) {
      setStatusTone(fileValidationError, "error");
      return;
    }
    if (!currentUser && !bypassRemoteAuth) {
      setStatusTone("Sign in with Google to upload a file.", "error");
      return;
    }

    logDebug("continue_clicked", {
      activeSource: "file",
      filename: selectedUploadFile.name,
      fileSize: selectedUploadFile.size,
      hasContextHint: Boolean(contextHint),
    });
    await submitFileCapture(selectedUploadFile, contextHint);
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
    activeSource: "text",
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

function landOnCaptureSection() {
  // Today's own content (Daily Brief, latest-result widgets) loads
  // asynchronously and can still be expanding page height around this point,
  // so one scroll call isn't reliable -- rescroll once more after layout has
  // had a chance to settle.
  const scrollToApp = () => document.getElementById("workflows-app")?.scrollIntoView({ behavior: "auto", block: "start" });
  scrollToApp();
  window.setTimeout(scrollToApp, 400);
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
    landOnCaptureSection();
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
    landOnCaptureSection();
    return;
  }

  showCaptureScreen();
  setStatus("");
  syncSubmitState();
}

function focusCaptureComponent() {
  const captureApp = document.getElementById("workflows-app");
  captureApp?.scrollIntoView({ behavior: "smooth", block: "start" });
  restorePendingCaptureToForm();
  const recordTrigger = document.getElementById("record-trigger");
  recordTrigger?.focus({ preventScroll: true });
}

// Exposed so a same-page entry point outside this module (the Today section's
// "Open capture" button and "continue the thread" action) can open/focus the
// one existing capture component in place, instead of navigating to it.
window.memnonFocusCapture = focusCaptureComponent;

export function mountWorkflowsApp() {
  const input = document.getElementById("capture-text");
  const context = document.getElementById("capture-context");
  const fileInput = document.getElementById("capture-file");
  const showPaste = document.getElementById("show-paste");
  const recordTrigger = document.getElementById("record-trigger");
  const uploadTrigger = document.getElementById("upload-trigger");
  const clearUpload = document.getElementById("clear-upload");
  const form = document.getElementById("capture-form");
  const signInLink = document.getElementById("workflows-signin");
  const openCaptureLink = document.getElementById("today-open-capture");

  openCaptureLink?.addEventListener("click", (event) => {
    event.preventDefault();
    focusCaptureComponent();
  });

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
    fileInput?.click();
  });
  fileInput?.addEventListener("change", handleFileSelection);
  clearUpload?.addEventListener("click", () => {
    clearSelectedUpload();
    syncSubmitState();
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
