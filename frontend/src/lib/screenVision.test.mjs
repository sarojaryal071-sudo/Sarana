// src/lib/screenVision.test.mjs — focused regression tests for SARANA
// Web Screen Vision's getDisplayMedia lifecycle (lib/screenVision.js,
// Phase 4). Same conventions as cameraVision.test.mjs — a mocked
// navigator.mediaDevices plus source-inspection for constants/behavior
// that need a real DOM.
//
// Run with:
//   cd frontend && node --test src/lib/screenVision.test.mjs
// (or `npm test`, see package.json)
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "screenVision.js"), "utf8");

function makeFakeVideo() {
  return {
    play: async () => {},
    set srcObject(v) { this._srcObject = v; },
    get srcObject() { return this._srcObject; },
  };
}
function makeFakeCanvas() {
  return { getContext: () => null, toBlob: () => {} };
}
globalThis.document = {
  createElement: (tag) => (tag === "video" ? makeFakeVideo() : makeFakeCanvas()),
};

function makeTrack() {
  const listeners = {};
  return {
    stopped: false,
    stop() { this.stopped = true; },
    addEventListener(evt, fn) { listeners[evt] = fn; },
    _fireEnded() { listeners.ended?.(); },
  };
}

/** A realistic-shaped fake MediaStream — real ones expose BOTH
 * getTracks() and getVideoTracks() (screenVision.js calls the latter to
 * wire the browser's own "stop sharing" control's ended event). */
function makeFakeStream(tracks) {
  return {
    getTracks: () => tracks,
    getVideoTracks: () => tracks,
  };
}

const mockNavigator = { mediaDevices: {} };   // getDisplayMedia set per-test below
Object.defineProperty(globalThis, "navigator", {
  value: mockNavigator, configurable: true, writable: true,
});

const { startScreenVision, stopScreenVision, getStream, activeRequestId, isScreenShareSupported } =
  await import("./screenVision.js");

test("isScreenShareSupported() reflects the real capability, never assumed true", () => {
  mockNavigator.mediaDevices.getDisplayMedia = undefined;
  assert.equal(isScreenShareSupported(), false);
  mockNavigator.mediaDevices.getDisplayMedia = async () => ({ getTracks: () => [] });
  assert.equal(isScreenShareSupported(), true);
});

test("when unsupported, startScreenVision() reports 'unavailable' and never calls anything", async () => {
  mockNavigator.mediaDevices.getDisplayMedia = undefined;
  const statuses = [];
  const ok = await startScreenVision("req-1", { onStatus: (s) => statuses.push(s) });
  assert.equal(ok, false);
  assert.ok(statuses.includes("unavailable"));
  assert.equal(activeRequestId(), null);
});

test("startScreenVision() requests video only, never audio (visual observation only, no tab/system audio)", async () => {
  let lastConstraints = null;
  mockNavigator.mediaDevices.getDisplayMedia = async (c) => {
    lastConstraints = c;
    return makeFakeStream([makeTrack()]);
  };
  const ok = await startScreenVision("req-2", {});
  assert.equal(ok, true);
  assert.equal(lastConstraints.video, true);
  assert.equal(lastConstraints.audio, false);
  stopScreenVision();
});

test("stopScreenVision stops every track and clears activeRequestId", async () => {
  const tracks = [makeTrack(), makeTrack()];
  mockNavigator.mediaDevices.getDisplayMedia = async () => makeFakeStream(tracks);
  await startScreenVision("req-3", {});
  assert.equal(activeRequestId(), "req-3");
  assert.ok(getStream());

  stopScreenVision();
  assert.equal(activeRequestId(), null);
  assert.equal(getStream(), null);
  assert.ok(tracks.every((t) => t.stopped));
});

test("a NotAllowedError (user dismissed/denied the native picker) reports 'denied'", async () => {
  mockNavigator.mediaDevices.getDisplayMedia = async () => {
    const err = new Error("denied");
    err.name = "NotAllowedError";
    throw err;
  };
  const statuses = [];
  const ok = await startScreenVision("req-4", { onStatus: (s) => statuses.push(s) });
  assert.equal(ok, false);
  assert.ok(statuses.includes("denied"));
});

test("the browser's own native \"stop sharing\" control ending the track is treated as an explicit stop", async () => {
  const track = makeTrack();
  mockNavigator.mediaDevices.getDisplayMedia = async () => makeFakeStream([track]);
  await startScreenVision("req-5", {});
  assert.equal(activeRequestId(), "req-5");

  track._fireEnded();
  assert.equal(activeRequestId(), null, "an externally-ended track must stop the session, not leave it dangling");
});

test("starting for the SAME requestId while already streaming is a no-op (no second getDisplayMedia call)", async () => {
  let calls = 0;
  mockNavigator.mediaDevices.getDisplayMedia = async () => {
    calls += 1;
    return makeFakeStream([makeTrack()]);
  };
  await startScreenVision("req-6", {});
  assert.equal(calls, 1);
  await startScreenVision("req-6", {});
  assert.equal(calls, 1, "re-calling for the same active requestId must not re-open the share");
  stopScreenVision();
});

// ── source-inspection ──────────────────────────────────────────────────

test("never imports/calls getUserMedia — a completely separate capability from cameraVision.js", () => {
  assert.doesNotMatch(src, /getUserMedia\s*\(/);
});

test("never requests facingMode — a display has no camera direction", () => {
  assert.doesNotMatch(src, /facingMode/);
});

test("sampling stays a low-rate burst; frames are downscaled for readability (higher than camera's cap, screens/text need more resolution)", () => {
  const intervalMatch = src.match(/SAMPLE_INTERVAL_MS\s*=\s*(\d+)/);
  assert.ok(intervalMatch);
  assert.ok(Number(intervalMatch[1]) >= 500);
  const dimMatch = src.match(/MAX_DIM\s*=\s*(\d+)/);
  assert.ok(dimMatch);
  assert.ok(Number(dimMatch[1]) >= 960, "screen/text content needs more resolution than the camera's quick-look cap");
});

test("this module never imports the upload pipeline's own downscale function", () => {
  assert.doesNotMatch(src, /from ["']\.\/image\.js["']/);
  assert.doesNotMatch(src, /prepareImageForUpload/);
});
