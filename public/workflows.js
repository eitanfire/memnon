import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import {
  getAuth,
  onAuthStateChanged,
  signInWithCustomToken,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

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
const isStaticLocalhost =
  window.location.hostname === "localhost" &&
  (window.location.port === "8000" || window.location.port === "8080");
const API_ORIGIN = isStaticLocalhost ? "https://api-4hth6oktaa-uc.a.run.app" : "";
const API_CAPTURES_PATH = "/api/workflows/captures";
const AUTH_START_URL = "https://api-4hth6oktaa-uc.a.run.app/auth/start";
const PENDING_CAPTURE_KEY = "memnon_workflows_pending_capture_v1";

let currentUser = null;
let initialRouteHandled = false;
let submitInFlight = false;

function parseWorkflowsRoute(pathname) {
  const normalized =
    pathname === "/workflows.html" ? "/workflows" : pathname.replace(/\/+$/, "") || "/workflows";
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
  }
}

function currentReturnUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("token");
  url.searchParams.delete("error");
  return url.toString();
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
  prompt.hidden = Boolean(currentUser);
}

function syncSubmitState() {
  const input = document.getElementById("capture-text");
  const submit = document.getElementById("capture-submit");
  if (!submit) {
    return;
  }
  submit.disabled = submitInFlight || !input || !input.value.trim();
  syncAuthPrompt();
}

async function getToken() {
  const user = auth.currentUser;
  if (!user) {
    throw new Error("Sign in required");
  }
  return user.getIdToken();
}

async function apiFetch(path, init = {}) {
  const token = await getToken();
  const headers = new Headers(init.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

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
  document.getElementById("primary-artifact-card").hidden = true;
  document.getElementById("saved-note-card").hidden = true;
  document.getElementById("source-text-panel").hidden = true;
}

function showCaptureScreen() {
  document.getElementById("capture-form").hidden = false;
  document.getElementById("result-view").hidden = true;
}

function showResultScreen() {
  document.getElementById("capture-form").hidden = true;
  document.getElementById("result-view").hidden = false;
}

function renderSavedNote(payload) {
  resetResultCards();
  showResultScreen();

  const card = document.getElementById("saved-note-card");
  const sourcePanel = document.getElementById("source-text-panel");
  const sourceText = document.getElementById("source-text-content");
  const themes = payload.result.likely_themes?.length
    ? payload.result.likely_themes.map(escapeHtml).join(", ")
    : "None yet";

  card.hidden = false;
  card.innerHTML = `
    <p class="workflows-kicker">Saved note</p>
    <h2>${escapeHtml(payload.result.interpretation_line)}</h2>
    <p>This capture did not justify a visible draft yet.</p>
    <p><strong>Likely themes:</strong> ${themes}</p>
    <div class="workflows-inline-actions">
      <button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>
    </div>
  `;

  sourcePanel.hidden = false;
  sourceText.textContent = payload.source_event.source_text;
  wireReturnToCapture();
}

function renderPrimaryArtifact(payload) {
  resetResultCards();
  showResultScreen();

  const card = document.getElementById("primary-artifact-card");
  const sourcePanel = document.getElementById("source-text-panel");
  const sourceText = document.getElementById("source-text-content");
  const artifact = payload.result.primary_artifact;
  const bodyParagraphs = String(artifact.body || "")
    .split(/\n{2,}/)
    .filter(Boolean)
    .map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`)
    .join("");

  card.hidden = false;
  card.innerHTML = `
    <p class="workflows-kicker">Result</p>
    <p class="workflows-interpretation">${escapeHtml(payload.result.interpretation_line)}</p>
    <h2>${escapeHtml(artifact.title)}</h2>
    <p>${escapeHtml(artifact.framing_line)}</p>
    <div class="workflows-body">${bodyParagraphs}</div>
    <div class="workflows-inline-actions">
      <button type="button" class="btn btn-primary" id="copy-artifact-body">Copy note</button>
      <button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>
    </div>
  `;

  sourcePanel.hidden = false;
  sourceText.textContent = payload.source_event.source_text;

  document.getElementById("copy-artifact-body")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(artifact.body || "");
    setStatus("Copied note.");
  });
  wireReturnToCapture();
}

function wireReturnToCapture() {
  document.getElementById("start-another-capture")?.addEventListener("click", () => {
    history.pushState({}, "", "/workflows");
    resetResultCards();
    showCaptureScreen();
    setStatus("");
    document.getElementById("capture-text")?.focus();
    syncSubmitState();
  });
}

function renderPayload(payload) {
  if (payload.result.route_kind === "saved_note" || !payload.result.primary_artifact) {
    renderSavedNote(payload);
    return;
  }
  renderPrimaryArtifact(payload);
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

async function loadCapture(captureId) {
  setStatus("Loading saved result...");
  const payload = await apiFetch(`${API_CAPTURES_PATH}/${encodeURIComponent(captureId)}`);
  setStatus("");
  renderPayload(payload);
}

async function submitCapture(text, contextHint) {
  submitInFlight = true;
  syncSubmitState();
  setStatus("Turning this into a next step...");

  try {
    const payload = await createCapture(text, contextHint);
    clearPendingCapture();
    const nextPath = `/workflows/result/${encodeURIComponent(payload.capture_id)}`;
    history.pushState({}, "", nextPath);
    setStatus("");
    renderPayload(payload);
  } catch (error) {
    setStatus(error.message || "Could not save this capture.");
  } finally {
    submitInFlight = false;
    syncSubmitState();
  }
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

async function maybeResumePendingCapture() {
  const pending = readPendingCapture();
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

  if (!currentUser) {
    writePendingCapture({
      text,
      contextHint,
      shouldResume: true,
      savedAt: Date.now(),
    });
    setStatus("Sign in to continue.");
    window.location.href = buildAuthStartUrl();
    return;
  }

  await submitCapture(text, contextHint);
}

function applySignedOutState() {
  resetResultCards();
  showCaptureScreen();
  setStatus("Sign in required to save captures.");
  syncAuthPrompt();
  syncSubmitState();
}

async function handleCurrentRoute() {
  const route = parseWorkflowsRoute(window.location.pathname);
  if (!currentUser) {
    applySignedOutState();
    return;
  }

  if (route.screen === "result") {
    try {
      await loadCapture(route.captureId);
    } catch (error) {
      showResultScreen();
      resetResultCards();
      const card = document.getElementById("saved-note-card");
      card.hidden = false;
      card.innerHTML = `
        <p class="workflows-kicker">Result</p>
        <h2>Could not load this capture</h2>
        <p>${escapeHtml(error.message || "Please try again.")}</p>
        <div class="workflows-inline-actions">
          <button type="button" class="btn btn-outline" id="start-another-capture">Start another capture</button>
        </div>
      `;
      wireReturnToCapture();
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
    setStatus("Recording is not available in this slice yet.");
  });
  uploadTrigger?.addEventListener("click", () => {
    setStatus("File upload is not available in this slice yet.");
  });
  form?.addEventListener("submit", handleSubmit);
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
  });
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
}

mountWorkflowsApp();
