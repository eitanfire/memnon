function parseWorkflowsRoute(pathname) {
  const clean = pathname.replace(/\/+$/, "") || "/workflows";
  const match = clean.match(/^\/workflows\/result\/([^/]+)$/);
  if (match) {
    return { screen: "result", captureId: decodeURIComponent(match[1]) };
  }
  return { screen: "capture" };
}

function syncSubmitState() {
  const input = document.getElementById("capture-text");
  const submit = document.getElementById("capture-submit");
  submit.disabled = !input || !input.value.trim();
}

function renderPlaceholderResult() {
  const result = document.getElementById("result-view");
  result.hidden = false;
  result.innerHTML = `
    <article class="card">
      <p class="workflows-kicker">Result</p>
      <h2>Waiting for a saved capture</h2>
      <p>The result view will render the primary artifact here once the backend is wired.</p>
    </article>
  `;
}

export function mountWorkflowsApp() {
  const route = parseWorkflowsRoute(window.location.pathname);
  const input = document.getElementById("capture-text");
  const showPaste = document.getElementById("show-paste");

  input?.addEventListener("input", syncSubmitState);
  showPaste?.addEventListener("click", () => input?.focus());
  syncSubmitState();

  if (route.screen === "result") {
    renderPlaceholderResult();
  }
}

mountWorkflowsApp();
