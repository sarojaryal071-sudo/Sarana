// src/lib/desktopBridge.test.mjs — the one platform-specific transport
// adapter for the desktop-embedded Presentation Engine entry. Real
// behavioral tests: installDesktopBridge() + window.__jarvisBridge.receive()
// against a fake dispatch function, verifying the scoped subset of
// message types this view actually needs (see that module's own header
// for why the rest — camera_vision_request, location_refresh_request,
// etc. — are deliberately not handled here).
import assert from "node:assert/strict";
import { test, beforeEach } from "node:test";

// No jsdom/DOM in this project's test environment (same situation
// Controls.test.mjs/cameraVision.test.mjs's own headers document) — the
// module's real target (a QWebEngineView) always has a genuine `window`,
// so this is purely a test-environment stand-in for the one property
// installDesktopBridge() attaches to it.
globalThis.window = globalThis.window || globalThis;

const { installDesktopBridge } = await import("./desktopBridge.js");

let dispatched;
function fakeDispatch(action) { dispatched.push(action); }

beforeEach(() => {
  dispatched = [];
  installDesktopBridge(fakeDispatch);
});

test("installDesktopBridge exposes window.__jarvisBridge.receive", () => {
  assert.equal(typeof globalThis.window, "object");
  assert.equal(typeof window.__jarvisBridge.receive, "function");
});

test("a 'content' message dispatches CONTENT_MESSAGE with the presentation payload intact", () => {
  window.__jarvisBridge.receive({
    type: "content", title: "WEATHER", text: "5.2C",
    presentation: { type: "weather", data: { current: {} } },
  });
  assert.deepEqual(dispatched, [{
    type: "CONTENT_MESSAGE", title: "WEATHER", text: "5.2C",
    presentation: { type: "weather", data: { current: {} } },
  }]);
});

test("a 'content' message with no presentation defaults it to null, not undefined", () => {
  window.__jarvisBridge.receive({ type: "content", title: "T", text: "x" });
  assert.equal(dispatched[0].presentation, null);
});

test("a 'status' message dispatches STATUS_MESSAGE", () => {
  window.__jarvisBridge.receive({ type: "status", state: "THINKING" });
  assert.deepEqual(dispatched, [{ type: "STATUS_MESSAGE", state: "THINKING" }]);
});

test("a 'jarvis_mode_changed' message dispatches JARVIS_MODE", () => {
  window.__jarvisBridge.receive({ type: "jarvis_mode_changed", active: true });
  assert.deepEqual(dispatched, [{ type: "JARVIS_MODE", value: true }]);
});

test("an 'audio_cue' message never dispatches — it's played directly, not routed through the reducer", () => {
  assert.doesNotThrow(() => window.__jarvisBridge.receive({ type: "audio_cue", event: "blocked" }));
  assert.deepEqual(dispatched, []);
});

test("log/sys messages never dispatch — this view has no transcript of its own", () => {
  window.__jarvisBridge.receive({ type: "log", speaker: "jarvis", text: "hi" });
  window.__jarvisBridge.receive({ type: "sys", text: "hi" });
  assert.deepEqual(dispatched, []);
});

test("an unrecognized message type is silently ignored, never throws", () => {
  assert.doesNotThrow(() => window.__jarvisBridge.receive({ type: "camera_vision_request", request_id: "x" }));
  assert.deepEqual(dispatched, []);
});

test("malformed input (null/non-object) never throws", () => {
  assert.doesNotThrow(() => window.__jarvisBridge.receive(null));
  assert.doesNotThrow(() => window.__jarvisBridge.receive(undefined));
  assert.doesNotThrow(() => window.__jarvisBridge.receive("not an object"));
});
