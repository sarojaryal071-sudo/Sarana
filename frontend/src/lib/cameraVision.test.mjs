// src/lib/cameraVision.test.mjs — focused regression tests for SARANA
// Web Live Camera Vision's getUserMedia lifecycle (lib/cameraVision.js).
//
// No jsdom/canvas in this project's test environment, so — same
// convention as Controls.test.mjs/permissions.test.mjs — most of this is
// real behavioral testing against a mocked navigator.mediaDevices
// (startCameraVision/stopCameraVision/getStream are plain async
// functions with no DOM canvas dependency in their success/failure
// paths), plus a handful of source-inspection checks for the sampling
// constants and constraints that can't be exercised without a real
// <video>/<canvas>.
//
// Run with:
//   cd frontend && node --test src/lib/cameraVision.test.mjs
// (or `npm test`, see package.json)
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "cameraVision.js"), "utf8");

// document.createElement("video"/"canvas") is used internally by
// startCameraVision() — stub just enough for it to run without a real DOM.
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

const mockNavigator = {
  mediaDevices: {
    getUserMedia: async () => {
      const err = new Error("mock: no getUserMedia wired for this test");
      err.name = "NotAllowedError";
      throw err;
    },
  },
};
Object.defineProperty(globalThis, "navigator", {
  value: mockNavigator, configurable: true, writable: true,
});

const { startCameraVision, stopCameraVision, getStream, activeRequestId } =
  await import("./cameraVision.js");

test("startCameraVision requests facingMode:{ideal:...} — never {exact:...}", async () => {
  let lastConstraints = null;
  const track = { stopped: false, stop() { this.stopped = true; } };
  mockNavigator.mediaDevices.getUserMedia = async (constraints) => {
    lastConstraints = constraints;
    return { getTracks: () => [track] };
  };
  const ok = await startCameraVision("req-1", "environment", {});
  assert.equal(ok, true);
  assert.equal(lastConstraints.video.facingMode.ideal, "environment");
  assert.ok(!("exact" in lastConstraints.video.facingMode));
  assert.equal(lastConstraints.audio, false, "camera vision must never also request microphone audio");
  stopCameraVision();
});

test("stopCameraVision stops every track and clears activeRequestId", async () => {
  const tracks = [{ stop() { this.a = true; } }, { stop() { this.b = true; } }];
  mockNavigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => tracks });
  await startCameraVision("req-2", "environment", {});
  assert.equal(activeRequestId(), "req-2");
  assert.ok(getStream());

  stopCameraVision();
  assert.equal(activeRequestId(), null);
  assert.equal(getStream(), null);
  assert.ok(tracks.every((t) => t.a || t.b));
});

test("a NotAllowedError reports status 'denied', never silently 'streaming'", async () => {
  mockNavigator.mediaDevices.getUserMedia = async () => {
    const err = new Error("denied");
    err.name = "NotAllowedError";
    throw err;
  };
  const statuses = [];
  const ok = await startCameraVision("req-3", "environment", { onStatus: (s) => statuses.push(s) });
  assert.equal(ok, false);
  assert.ok(statuses.includes("denied"));
  assert.equal(activeRequestId(), null);
});

test("a missing camera (NotFoundError) reports 'unavailable', distinct from 'denied'", async () => {
  mockNavigator.mediaDevices.getUserMedia = async () => {
    const err = new Error("no camera");
    err.name = "NotFoundError";
    throw err;
  };
  const statuses = [];
  const ok = await startCameraVision("req-4", "environment", { onStatus: (s) => statuses.push(s) });
  assert.equal(ok, false);
  assert.ok(statuses.includes("unavailable"));
  assert.ok(!statuses.includes("denied"));
});

test("starting for the SAME requestId while already streaming is a no-op (no second getUserMedia call)", async () => {
  let calls = 0;
  mockNavigator.mediaDevices.getUserMedia = async () => {
    calls += 1;
    return { getTracks: () => [{ stop() {} }] };
  };
  await startCameraVision("req-5", "environment", {});
  assert.equal(calls, 1);
  await startCameraVision("req-5", "environment", {});
  assert.equal(calls, 1, "re-calling for the same active requestId must not re-open the camera");
  stopCameraVision();
});

test("starting for a DIFFERENT requestId stops the previous stream first", async () => {
  const oldTrack = { stopped: false, stop() { this.stopped = true; } };
  mockNavigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [oldTrack] });
  await startCameraVision("req-6a", "environment", {});
  mockNavigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }] });
  await startCameraVision("req-6b", "environment", {});
  assert.equal(oldTrack.stopped, true, "switching requestId must stop the old MediaStream's tracks");
  assert.equal(activeRequestId(), "req-6b");
  stopCameraVision();
});

// ── source-inspection: constants/behavior that need a real DOM to
//    exercise fully, checked the same way Controls.test.mjs checks
//    committed source rather than rendered behavior ──────────────────────

test("sampling stays a low-rate burst, not continuous video — SAMPLE_INTERVAL_MS is defined and >= 500ms", () => {
  const m = src.match(/SAMPLE_INTERVAL_MS\s*=\s*(\d+)/);
  assert.ok(m, "SAMPLE_INTERVAL_MS constant not found");
  assert.ok(Number(m[1]) >= 500, "sampling faster than ~2fps would defeat the point of a burst, not a stream");
});

test("sampled frames are downscaled well below the deliberate photo-upload path's 1600px cap", () => {
  const m = src.match(/MAX_DIM\s*=\s*(\d+)/);
  assert.ok(m);
  assert.ok(Number(m[1]) <= 1000, "live-vision frames should stay small/cheap, unlike the deliberate-upload path");
});

test("a brightness/technical filter exists and is documented as non-authoritative", () => {
  assert.match(src, /MIN_AVG_BRIGHTNESS/);
  assert.match(src, /never a "too dark to answer" judgment call/);
});

test("this module never imports or calls the upload pipeline's own downscale function", () => {
  assert.doesNotMatch(src, /from ["']\.\/image\.js["']/);
  assert.doesNotMatch(src, /prepareImageForUpload/);
});
