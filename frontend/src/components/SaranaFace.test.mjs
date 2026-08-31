// src/components/SaranaFace.test.mjs — focused regression tests for
// SARANA's normal-mode expressive face, source-inspection style (same
// convention as Controls.test.mjs/VisionStage.test.mjs — this project
// renders no components in tests; see those files' own header notes).
//
// Renders into the same "orb-stage" slot Orb.jsx/VisionStage.jsx occupy
// (see App.jsx) instead of a new floating element. The expression mapping
// itself is a pure function tested separately with real assertions in
// lib/faceExpressions.test.mjs — this file only covers the component's own
// structure/wiring, plus App.jsx integration.
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
const src = fs.readFileSync(path.join(__dirname, "SaranaFace.jsx"), "utf8");
const orbSrc = fs.readFileSync(path.join(__dirname, "Orb.jsx"), "utf8");
const controlsSrc = fs.readFileSync(path.join(__dirname, "Controls.jsx"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "..", "App.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "index.css"), "utf8");
const faceExpressionsSrc = fs.readFileSync(path.join(__dirname, "..", "lib", "faceExpressions.js"), "utf8");

// ── component structure ──────────────────────────────────────────────────

test("renders into the SAME \"orb-stage\" class Orb.jsx's own wrapper uses — not a distinct floating class", () => {
  assert.match(src, /className="orb-stage"/, "SaranaFace must reuse Orb's own wrapper class");
});

test("the face graphic is purely presentational (aria-hidden) — no interactive elements", () => {
  assert.match(src, /aria-hidden="true"/);
  assert.doesNotMatch(src, /<button/i, "the face itself must not contain buttons");
  assert.doesNotMatch(src, /onClick/);
});

test("has exactly two eyes and one mouth element", () => {
  assert.match(src, /face-eye-left/);
  assert.match(src, /face-eye-right/);
  assert.match(src, /face-mouth/);
});

test("delegates ALL status->expression logic to lib/faceExpressions.js — never reimplements the mapping inline", () => {
  assert.match(src, /from ["']\.\.\/lib\/faceExpressions["']/);
  assert.match(src, /mapStatusToExpression/);
  // None of the expression vocabulary words should be hardcoded as a
  // literal in this file — the component must only ever obtain them
  // dynamically via mapStatusToExpression(status), never duplicate the
  // lookup table itself.
  for (const word of ["neutral", "listening", "thinking", "speaking", "concerned"]) {
    assert.doesNotMatch(src, new RegExp(`["']${word}["']`), `"${word}" must not be hardcoded in SaranaFace.jsx`);
  }
});

test("the expression drives rendering via a data-expression attribute (styled entirely in CSS, not inline styles)", () => {
  assert.match(src, /data-expression=\{/);
  assert.doesNotMatch(src, /style=\{\{/, "expressions should be CSS-driven, not inline style objects");
});

test("every expression in the shared vocabulary has a matching CSS selector", () => {
  const vocabMatch = faceExpressionsSrc.match(/FACE_EXPRESSIONS = Object\.freeze\(\[([\s\S]*?)\]\)/);
  assert.ok(vocabMatch, "could not locate FACE_EXPRESSIONS in faceExpressions.js");
  const names = [...vocabMatch[1].matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
  assert.ok(names.length >= 10, "expected the full ten-expression vocabulary");
  for (const name of names) {
    assert.match(
      css,
      new RegExp(`\\[data-expression="${name}"\\]`),
      `missing CSS for expression "${name}"`,
    );
  }
});

// ── blinking / timers (spec section 11: natural, no leaks) ───────────────

test("blinking uses a randomized timer (not a fixed CSS `infinite` metronome) and is cleaned up on unmount", () => {
  assert.match(src, /setTimeout/);
  assert.match(
    src,
    /return\s*\(\)\s*=>\s*\{[\s\S]{0,120}clearTimeout/,
    "the blink effect must clear its pending timer on cleanup",
  );
});

test("blink scheduling checks document.hidden so a backgrounded tab doesn't keep animating unseen", () => {
  assert.match(src, /document\.hidden/);
});

test("no requestAnimationFrame / per-frame render loop — CSS animations carry the motion instead (spec section 17)", () => {
  assert.doesNotMatch(src, /requestAnimationFrame/);
  assert.doesNotMatch(src, /setInterval/, "prefer a single rescheduled timeout over an uncapped interval");
});

// ── visual/technology constraints ─────────────────────────────────────────

test("no canvas, WebGL, Three.js, or photorealistic <img> avatar — CSS-driven shapes only", () => {
  assert.doesNotMatch(src, /<canvas/i);
  assert.doesNotMatch(src, /<img/i);
  assert.doesNotMatch(src, /three\.js|WebGLRenderer/i);
});

test("no literal emoji characters used to represent an expression (status glyphs mirror Orb.jsx's own existing monospace symbols, not emoji)", () => {
  const emojiRange = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u;
  const statusLine = src.match(/STATUS_LABELS = \{[\s\S]*?\};/)[0];
  // Orb.jsx's own glyphs (⊘ ● ◈ ○) are outside the emoji block ranges above
  assert.doesNotMatch(statusLine, emojiRange);
});

test("accepts the same prop shape as Orb ({ status, assistantName }) for interchangeability in the same slot", () => {
  assert.match(src, /\{\s*status\s*,\s*assistantName\s*\}/);
  assert.match(orbSrc, /\{\s*status\s*,\s*assistantName\s*\}/);
});

// ── accessibility (spec section 16: face must not be the ONLY state signal) ─

test("a real text status label exists with role=\"status\"/aria-live so screen readers get it too", () => {
  assert.match(src, /role="status"/);
  assert.match(src, /aria-live="polite"/);
});

// ── JARVIS-mode compatibility (spec section 13) ───────────────────────────

test("Orb.jsx itself is left completely untouched and still exported as a component (reserved for a future JARVIS mode)", () => {
  assert.match(orbSrc, /export default function Orb\(/);
  assert.match(orbSrc, /className="orb-stage"/);
});

// ── App.jsx integration ────────────────────────────────────────────────────

test("App.jsx renders SaranaFace (not Orb) in the normal-mode branch of the orb-stage conditional", () => {
  assert.match(appSrc, /import SaranaFace from ["']\.\/components\/SaranaFace["']/);
  assert.doesNotMatch(appSrc, /import Orb from/, "Orb should no longer be imported/rendered directly by App.jsx");
  const usages = appSrc.match(/<SaranaFace\b/g) || [];
  assert.equal(usages.length, 1, "SaranaFace must be rendered from exactly one place in App.jsx");
});

test("VisionStage (camera/screen) still replaces the face in-place — never renders alongside it as a second element", () => {
  assert.match(
    appSrc,
    /visionRequest\s*\?\s*\(\s*<VisionStage[\s\S]{0,400}<SaranaFace/,
    "VisionStage and SaranaFace must be the two branches of the SAME conditional",
  );
});

test("Controls (mic/interrupt/message input) remains rendered exactly once, unconditionally", () => {
  const usages = appSrc.match(/<Controls\b/g) || [];
  assert.equal(usages.length, 1);
  assert.doesNotMatch(
    appSrc,
    /visionRequest[\s\S]{0,50}<Controls/,
    "Controls must not be gated behind vision/face state",
  );
});

test("the mic button, interrupt button, and message input are unchanged in Controls.jsx (untouched by this UI change)", () => {
  assert.match(controlsSrc, /onToggleMic/);
  assert.match(controlsSrc, /onInterrupt/);
  assert.match(controlsSrc, /type="submit"/);
});

test("logout/token-change teardown is unaffected by the face swap (still stops camera/screen capture, not face-related)", () => {
  assert.match(appSrc, /stopCameraVision\(\);\s*\n\s*stopScreenVision\(\);/);
});
