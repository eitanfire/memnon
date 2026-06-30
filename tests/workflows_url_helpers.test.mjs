import test from "node:test";
import assert from "node:assert/strict";

import {
  WORKFLOWS_DEBUG_BUILD,
  canonicalizeAuthReturnUrl,
  getCaptureValidationError,
  isLocalStaticHost,
  shouldBlockUnexpectedNavigation,
  shouldBypassRemoteAuth,
  shouldShowLocalDebugUi,
} from "../public/workflows_url_helpers.js";

test("WORKFLOWS_DEBUG_BUILD is a non-empty marker", () => {
  assert.equal(typeof WORKFLOWS_DEBUG_BUILD, "string");
  assert.notEqual(WORKFLOWS_DEBUG_BUILD.length, 0);
});

test("canonicalizeAuthReturnUrl rewrites 127.0.0.1 auth returns to localhost", () => {
  const result = canonicalizeAuthReturnUrl(
    "http://127.0.0.1:8000/workflows?foo=bar",
  );

  assert.equal(result, "http://localhost:8000/workflows?foo=bar");
});

test("canonicalizeAuthReturnUrl preserves non-loopback frontend urls", () => {
  const result = canonicalizeAuthReturnUrl(
    "https://memnon.app/workflows?token=abc",
  );

  assert.equal(result, "https://memnon.app/workflows?token=abc");
});

test("isLocalStaticHost accepts localhost and 127.0.0.1 workflow ports", () => {
  assert.equal(isLocalStaticHost("localhost", "8000"), true);
  assert.equal(isLocalStaticHost("127.0.0.1", "8000"), true);
  assert.equal(isLocalStaticHost("127.0.0.1", "8080"), true);
  assert.equal(isLocalStaticHost("127.0.0.1", "5051"), false);
});

test("shouldBypassRemoteAuth is enabled only for local static workflow hosts", () => {
  assert.equal(shouldBypassRemoteAuth("localhost", "8000"), true);
  assert.equal(shouldBypassRemoteAuth("127.0.0.1", "8000"), true);
  assert.equal(shouldBypassRemoteAuth("memnon.app", ""), false);
  assert.equal(shouldBypassRemoteAuth("127.0.0.1", "5051"), false);
});

test("shouldShowLocalDebugUi requires a local host and explicit debug flag", () => {
  assert.equal(
    shouldShowLocalDebugUi("?workflowsDebug=1", "127.0.0.1", "8000"),
    true,
  );
  assert.equal(
    shouldShowLocalDebugUi("?debug=workflows", "localhost", "8000"),
    true,
  );
  assert.equal(
    shouldShowLocalDebugUi("", "127.0.0.1", "8000"),
    false,
  );
  assert.equal(
    shouldShowLocalDebugUi("?workflowsDebug=1", "memnon.app", ""),
    false,
  );
});

test("shouldBlockUnexpectedNavigation blocks local workflow pages from leaving localhost", () => {
  assert.equal(
    shouldBlockUnexpectedNavigation(
      "http://127.0.0.1:8000/workflows",
      "https://api-4hth6oktaa-uc.a.run.app/auth/start?return_to=http://localhost:8000/workflows",
    ),
    true,
  );
  assert.equal(
    shouldBlockUnexpectedNavigation(
      "http://127.0.0.1:8000/workflows",
      "/workflows/result/cap-123",
    ),
    false,
  );
  assert.equal(
    shouldBlockUnexpectedNavigation(
      "https://memnon.app/workflows",
      "https://memnon.app/dashboard",
    ),
    false,
  );
});

test("getCaptureValidationError requires at least three words", () => {
  assert.equal(
    getCaptureValidationError("Hello"),
    "Add at least a short phrase before continuing.",
  );
  assert.equal(
    getCaptureValidationError("Turn this into"),
    "",
  );
});
