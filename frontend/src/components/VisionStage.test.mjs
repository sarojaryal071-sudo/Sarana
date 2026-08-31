// src/components/VisionStage.test.mjs — focused regression tests for the
// live camera/screen preview, source-inspection style (same convention
// as Controls.test.mjs — this project renders no components in tests,
// see that file's own header note).
//
// Renders into the same "orb-stage" slot Orb.jsx occupies (see App.jsx)
// instead of a separate floating card, and covers BOTH visual sources
// (camera and screen — see the component's own header note on why they
// share this one thin UI shell but never share a capture mechanism).
//
// Run with:
//   cd frontend && node --test src/components/*.test.mjs
// (or `npm test`, see package.json)
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "VisionStage.jsx"), "utf8");
const orbSrc = fs.readFileSync(path.join(__dirname, "Orb.jsx"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "..", "App.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "index.css"), "utf8");

test("renders into the SAME \"orb-stage\" class Orb.jsx's own wrapper uses — not a distinct floating class", () => {
  assert.match(src, /className="orb-stage"/, "VisionStage must reuse Orb's own wrapper class");
  assert.match(orbSrc, /className="orb-stage"/, "sanity check: Orb.jsx must still use this exact class");
});

test("no fixed/floating positioning remains — the old separate camera box is gone", () => {
  assert.doesNotMatch(src, /vision-panel/, "old floating-panel classes must not reappear");
  assert.doesNotMatch(css, /\.vision-panel\b/);
});

test("uses a completely separate lib per source — camera never imports getDisplayMedia, screen never imports getUserMedia", () => {
  assert.match(src, /from ["']\.\.\/lib\/cameraVision["']/);
  assert.match(src, /from ["']\.\.\/lib\/screenVision["']/);
  const cameraLib = fs.readFileSync(path.join(__dirname, "..", "lib", "cameraVision.js"), "utf8");
  const screenLib = fs.readFileSync(path.join(__dirname, "..", "lib", "screenVision.js"), "utf8");
  assert.doesNotMatch(cameraLib, /getDisplayMedia/);
  assert.doesNotMatch(screenLib, /getUserMedia\s*\(/);
});

test("the preview <video> declares playsInline, autoPlay, and muted, and fills its container with object-fit: cover", () => {
  const m = src.match(/<video\b[^>]*\/>/);
  assert.ok(m, "no <video> element found");
  assert.match(m[0], /playsInline/);
  assert.match(m[0], /autoPlay/);
  assert.match(m[0], /muted/);
  const cssBlock = css.match(/\.vision-stage-video\s*\{[\s\S]*?\}/);
  assert.match(cssBlock[0], /object-fit:\s*cover/);
});

test("never calls getUserMedia/getDisplayMedia directly — all capture goes through the lib modules", () => {
  assert.doesNotMatch(src, /getUserMedia\s*\(/);
  assert.doesNotMatch(src, /getDisplayMedia\s*\(/);
});

test("no dedicated Stop or minimize button — stopping happens through the existing INTERRUPT control instead", () => {
  assert.doesNotMatch(src, />\s*Stop\s*</, "no standalone Stop button should exist in this component");
  assert.doesNotMatch(src, /setMinimized|minimized/i);
});

test("camera: waits for an explicit user tap on first use; screen: ALWAYS requires a tap (browsers never persist display-capture consent)", () => {
  assert.match(src, /setStatus\("prompt"\)/);
  assert.match(src, /function handleAllow/);
  assert.match(src, /isScreenShareSupported\(\)\s*\?\s*"prompt"\s*:\s*"unavailable"/,
    "screen source must always land on prompt/unavailable, never silently auto-start");
});

test("an already-granted camera effective state starts without a permission prompt", () => {
  assert.match(src, /getEffectiveState\("camera"\)/);
  assert.match(src, /effective === "granted"/);
});

test("screen support is checked honestly via isScreenShareSupported() before ever attempting capture", () => {
  assert.match(src, /isScreenShareSupported/);
});

test("unmounting (requestId cleared) always stops capture for whichever source was active — no leaked stream on teardown", () => {
  assert.match(src, /return\s*\(\)\s*=>\s*\{[\s\S]{0,200}stopCameraVision\(\)[\s\S]{0,60}stopScreenVision\(\)/);
});

test("has a small camera-active indicator, not a large card/panel/modal", () => {
  assert.match(src, /vision-stage-badge/);
  const block = css.match(/\.vision-stage-badge\s*\{[\s\S]*?\}/);
  assert.ok(block, ".vision-stage-badge rule not found");
  assert.doesNotMatch(block[0], /width:\s*100%/);
});

test("denied vs unavailable vs SARANA-disabled render distinct, honest copy for BOTH sources", () => {
  assert.match(src, /denied:\s*\{/);
  assert.match(src, /unavailable:\s*\{/);
  assert.match(src, /sarana_disabled:\s*\{/);
  assert.match(src, /camera:.*Camera access is off/);
  assert.match(src, /screen:.*Screen sharing wasn't allowed/);
});

// ── Phase 1: camera flip ─────────────────────────────────────────────────

test("a flip control exists, is camera-only, and only shows while actually streaming", () => {
  assert.match(src, /vision-stage-flip/);
  assert.match(src, /status === "streaming" && isCamera[\s\S]{0,200}vision-stage-flip/);
});

test("flip calls flipCameraFacing() and re-attaches the preview on success, never touches requestId/session", () => {
  assert.match(src, /flipCameraFacing/);
  assert.doesNotMatch(src, /new (WebSocket|JarvisSocket)/, "flip must never create a new connection");
  assert.match(src, /handleFlip[\s\S]{0,300}attachPreview\(\)/);
});

// ── App.jsx wiring: camera and screen both replace the orb, never sit beside it ─

test("App.jsx mounts exactly ONE of <VisionStage> or <SaranaFace> at a time (a single conditional slot)", () => {
  // Human-Orb UI task: the normal-mode sibling of VisionStage in this
  // conditional is SaranaFace, reached through an identity-stage
  // crossfade wrapper (state.jarvisMode ? Orb : SaranaFace) rather than
  // directly — see SaranaFace.test.mjs for the full crossfade/JARVIS-
  // mode-compatibility checks; this file only re-confirms VisionStage's
  // own "always wins, exactly one slot" invariant still holds.
  const usages = appSrc.match(/<VisionStage\b/g) || [];
  assert.equal(usages.length, 1, "VisionStage must be rendered from exactly one place in App.jsx");
  assert.match(
    appSrc,
    /visionRequest\s*\?\s*\(\s*<VisionStage[\s\S]{0,700}<SaranaFace/,
    "VisionStage and SaranaFace must be the two branches of the SAME conditional, not two independently-rendered elements",
  );
});

test("App.jsx handles screen_vision_request/screen_vision_stop, mirroring camera_vision_request/stop", () => {
  assert.match(appSrc, /case "screen_vision_request":/);
  assert.match(appSrc, /case "screen_vision_stop":/);
  assert.match(appSrc, /source:\s*"camera"/);
  assert.match(appSrc, /source:\s*"screen"/);
});

test("the existing INTERRUPT control stops an active camera OR screen vision session — no new button was added for this", () => {
  assert.match(
    appSrc,
    /function handleInterrupt\(\)\s*\{[\s\S]{0,600}visionRequest[\s\S]{0,150}handleVisionStopped/,
    "handleInterrupt must also stop vision capture when a request is active",
  );
});

test("mic button, interrupt button, and message input remain unconditionally rendered (never gated by camera/screen state)", () => {
  const controlsUsages = appSrc.match(/<Controls\b/g) || [];
  assert.equal(controlsUsages.length, 1, "Controls must be rendered exactly once, unconditionally");
});

test("logout/token-change teardown stops BOTH camera and screen capture, not just one", () => {
  assert.match(appSrc, /stopCameraVision\(\);\s*\n\s*stopScreenVision\(\);/);
});
