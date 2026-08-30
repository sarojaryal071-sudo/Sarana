// src/components/CameraVisionPanel.test.mjs — focused regression tests
// for the live-camera preview, source-inspection style (same convention
// as Controls.test.mjs — this project renders no components in tests,
// see that file's own header note).
//
// This component now renders INSIDE the same "orb-stage" slot Orb.jsx
// occupies (see App.jsx) instead of a separate floating card — these
// tests guard that specific requirement, not just "a video exists
// somewhere".
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
const src = fs.readFileSync(path.join(__dirname, "CameraVisionPanel.jsx"), "utf8");
const orbSrc = fs.readFileSync(path.join(__dirname, "Orb.jsx"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "..", "App.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "index.css"), "utf8");

test("renders into the SAME \"orb-stage\" class Orb.jsx's own wrapper uses — not a distinct floating class", () => {
  assert.match(src, /className="orb-stage"/, "CameraVisionPanel must reuse Orb's own wrapper class");
  assert.match(orbSrc, /className="orb-stage"/, "sanity check: Orb.jsx must still use this exact class");
});

test("no fixed/floating positioning remains — the old separate camera box is gone", () => {
  assert.doesNotMatch(src, /vision-panel/, "old floating-panel classes must be fully removed from this component");
  assert.doesNotMatch(css, /\.vision-panel\b/, "old floating-panel CSS rule must be fully removed");
  const block = css.match(/\.vision-stage-video\s*\{[\s\S]*?\}/);
  assert.ok(block, ".vision-stage-video rule not found");
  assert.doesNotMatch(block[0], /position:\s*fixed/);
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

test("never calls getUserMedia directly — all camera access goes through lib/cameraVision.js", () => {
  assert.doesNotMatch(src, /getUserMedia\s*\(/);
  assert.match(src, /from ["']\.\.\/lib\/cameraVision["']/);
});

test("no dedicated Stop or minimize button — stopping happens through the existing INTERRUPT control instead", () => {
  assert.doesNotMatch(src, />\s*Stop\s*</, "no standalone Stop button should remain in this component");
  assert.doesNotMatch(src, /setMinimized|minimized/i, "the old minimize toggle must be removed, not just hidden");
});

test("the FIRST camera use waits for an explicit user tap — never auto-starts on an unresolved/denied permission", () => {
  assert.match(src, /setStatus\("prompt"\)/);
  assert.match(src, /function handleAllow/);
  assert.match(src, /handleAllow[\s\S]{0,400}startCameraVision/);
});

test("an already-granted effective state starts the camera without a permission prompt", () => {
  assert.match(src, /getEffectiveState\("camera"\)/);
  assert.match(src, /effective === "granted"/);
});

test("unmounting (requestId cleared) always stops the camera — no leaked stream on teardown", () => {
  assert.match(src, /return\s*\(\)\s*=>\s*\{[\s\S]{0,120}stopCameraVision\(\)/);
});

test("has a small camera-active indicator, not a large card/panel/modal", () => {
  assert.match(src, /vision-stage-badge/);
  const block = css.match(/\.vision-stage-badge\s*\{[\s\S]*?\}/);
  assert.ok(block, ".vision-stage-badge rule not found");
  // "small" as a rough proxy: no full-width/fixed-size card styling.
  assert.doesNotMatch(block[0], /width:\s*100%/);
});

test("denied vs unavailable vs SARANA-disabled render distinct, honest copy — never a generic silent failure", () => {
  assert.match(src, /denied:/);
  assert.match(src, /unavailable:/);
  assert.match(src, /sarana_disabled:/);
});

// ── App.jsx wiring: the preview replaces the orb, never sits beside it ──

test("App.jsx mounts exactly ONE of <CameraVisionPanel> or <Orb> at a time (a single conditional slot)", () => {
  const usages = appSrc.match(/<CameraVisionPanel\b/g) || [];
  assert.equal(usages.length, 1, "CameraVisionPanel must be rendered from exactly one place in App.jsx");
  assert.match(
    appSrc,
    /visionRequest\s*\?\s*\(\s*<CameraVisionPanel[\s\S]{0,400}<Orb/,
    "CameraVisionPanel and Orb must be the two branches of the SAME conditional, not two independently-rendered elements",
  );
});

test("the existing INTERRUPT control stops an active camera vision session — no new button was added for this", () => {
  assert.match(
    appSrc,
    /function handleInterrupt\(\)\s*\{[\s\S]{0,600}visionRequest[\s\S]{0,150}handleVisionStopped/,
    "handleInterrupt must also stop camera vision when a request is active",
  );
});

test("mic button, interrupt button, and message input remain unconditionally rendered (never gated by camera state)", () => {
  // <Controls> (which owns the mic button, interrupt button, and message
  // input/send) must be rendered outside the Orb/CameraVisionPanel
  // conditional -- i.e. exactly once, unconditionally, in panel-center.
  const controlsUsages = appSrc.match(/<Controls\b/g) || [];
  assert.equal(controlsUsages.length, 1, "Controls must be rendered exactly once, unconditionally");
});
