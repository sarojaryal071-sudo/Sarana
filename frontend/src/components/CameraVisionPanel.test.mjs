// src/components/CameraVisionPanel.test.mjs — focused regression tests
// for the live-camera preview panel, source-inspection style (same
// convention as Controls.test.mjs — this project renders no components
// in tests, see that file's own header note).
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
const css = fs.readFileSync(path.join(__dirname, "..", "index.css"), "utf8");

test("the preview <video> declares playsInline, autoPlay, and muted", () => {
  const m = src.match(/<video\b[^>]*\/>/);
  assert.ok(m, "no <video> element found");
  assert.match(m[0], /playsInline/);
  assert.match(m[0], /autoPlay/);
  assert.match(m[0], /muted/);
});

test("never calls getUserMedia directly — all camera access goes through lib/cameraVision.js", () => {
  // Matches an actual call (`getUserMedia(`), not the word appearing in a
  // plain-English comment (e.g. explaining the iOS user-gesture rule).
  assert.doesNotMatch(src, /getUserMedia\s*\(/);
  assert.match(src, /from ["']\.\.\/lib\/cameraVision["']/);
});

test("the FIRST camera use waits for an explicit user tap — never auto-starts on an unresolved/denied permission", () => {
  // effectiveState !== "granted" must route to the "prompt" status, which
  // renders the "Allow Camera" button (handleAllow) rather than calling
  // startCameraVision directly — required for iOS Safari's user-gesture
  // rule, good practice everywhere.
  assert.match(src, /setStatus\("prompt"\)/);
  assert.match(src, /function handleAllow/);
  assert.match(src, /handleAllow[\s\S]{0,400}startCameraVision/);
});

test("an already-granted effective state starts the camera without a permission prompt", () => {
  assert.match(src, /getEffectiveState\("camera"\)/);
  assert.match(src, /effective === "granted"/);
});

test("Stop always tears the camera down via stopCameraVision(), never just hides the UI", () => {
  assert.match(src, /function handleStop/);
  assert.match(src, /handleStop[\s\S]{0,120}stopCameraVision\(\)/);
});

test("unmounting (requestId cleared) always stops the camera — no leaked stream on teardown", () => {
  assert.match(src, /return\s*\(\)\s*=>\s*\{[\s\S]{0,120}stopCameraVision\(\)/);
});

test("the panel is never fullscreen — CSS uses fixed positioning with a bounded width, not inset:0", () => {
  const block = css.match(/\.vision-panel\s*\{[\s\S]*?\}/);
  assert.ok(block, ".vision-panel rule not found in index.css");
  assert.match(block[0], /position:\s*fixed/);
  assert.doesNotMatch(block[0], /inset:\s*0/, "a fullscreen inset:0 would contradict the 'never fullscreen' requirement");
  assert.match(block[0], /width:\s*min\(/, "width should be capped, not 100vw/100%");
});

test("has an explicit minimize control distinct from Stop", () => {
  assert.match(src, /vision-panel-min/);
  assert.match(src, /setMinimized/);
  assert.match(src, /vision-panel-stop/);
});

test("denied vs unavailable vs SARANA-disabled render distinct, honest copy — never a generic silent failure", () => {
  assert.match(src, /denied:/);
  assert.match(src, /unavailable:/);
  assert.match(src, /sarana_disabled:/);
});
