export const WORKFLOWS_DEBUG_BUILD = "local-debug-2026-06-27-redirect-trace-1";

export function isLocalStaticHost(hostname, port) {
  return ["localhost", "127.0.0.1"].includes(hostname)
    && ["8000", "8080"].includes(String(port));
}

export function shouldBypassRemoteAuth(hostname, port) {
  return isLocalStaticHost(hostname, port);
}

export function shouldShowLocalDebugUi(searchString, hostname, port) {
  if (!isLocalStaticHost(hostname, port)) {
    return false;
  }

  const params = new URLSearchParams(searchString || "");
  return params.get("workflowsDebug") === "1" || params.get("debug") === "workflows";
}

export function canonicalizeAuthReturnUrl(urlString) {
  const url = new URL(urlString);
  if (url.hostname === "127.0.0.1") {
    url.hostname = "localhost";
  }
  return url.toString();
}

export function shouldBlockUnexpectedNavigation(currentUrl, targetUrl) {
  const current = new URL(currentUrl);
  if (!isLocalStaticHost(current.hostname, current.port)) {
    return false;
  }

  const target = new URL(targetUrl, current);
  return !["localhost", "127.0.0.1"].includes(target.hostname);
}

export function getCaptureValidationError(text) {
  const wordCount = String(text || "").trim().split(/\s+/).filter(Boolean).length;
  if (wordCount < 3) {
    return "Add at least a short phrase before continuing.";
  }
  return "";
}
