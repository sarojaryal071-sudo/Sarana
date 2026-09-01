// src/components/SaranaFace.test.mjs — focused regression tests for
// SARANA's Human-Orb wireframe face, source-inspection style (same
// convention as Controls.test.mjs/VisionStage.test.mjs — this project
// renders no components in tests; see those files' own header notes).
//
// Renders into the same "orb-stage" slot Orb.jsx/VisionStage.jsx occupy
// (see App.jsx) instead of a new floating element. The expression mapping
// and mesh geometry are pure functions tested separately with real
// assertions (lib/faceExpressions.test.mjs, lib/faceMesh.test.mjs) — this
// file only covers the component's own structure/wiring, the identity
// crossfade, and App.jsx integration.
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
const faceMeshSrc = fs.readFileSync(path.join(__dirname, "..", "lib", "faceMesh.js"), "utf8");

// ── component structure ──────────────────────────────────────────────────

test("renders into the SAME \"orb-stage\" class Orb.jsx's own wrapper uses — not a distinct floating class", () => {
  assert.match(src, /className="orb-stage"/, "SaranaFace must reuse Orb's own wrapper class");
});

test("the face graphic is purely presentational (aria-hidden) — no interactive elements", () => {
  assert.match(src, /aria-hidden="true"/);
  assert.doesNotMatch(src, /<button/i, "the face itself must not contain buttons");
  assert.doesNotMatch(src, /onClick/);
});

test("renders the procedural wireframe mesh from lib/faceMesh.js — not hand-authored SVG paths or an image asset", () => {
  assert.match(src, /from ["']\.\.\/lib\/faceMesh["']/);
  assert.match(src, /buildFacePoints/);
  assert.match(src, /buildFaceEdges/);
  assert.doesNotMatch(src, /<img/i, "no image asset for the face");
});

test("geometry is computed ONCE at module load, not inside the component function (no per-render/per-frame recomputation)", () => {
  // The buildFacePoints()/buildFaceEdges() calls must appear at the top
  // level of the module, before the exported component function starts —
  // never inside it, which would recompute the same fixed geometry on
  // every render for no reason.
  const componentStart = src.indexOf("export default function SaranaFace");
  const pointsCallIdx = src.indexOf("buildFacePoints()");
  assert.ok(pointsCallIdx > -1 && pointsCallIdx < componentStart, "buildFacePoints() must run at module scope, before the component");
});

test("has all thirteen anatomical mesh groups referenced (eyes, brows, pupils, nose, cheeks, mouth, jaw, head)", () => {
  for (const group of ["head", "browL", "browR", "eyeL", "eyeR", "pupilL", "pupilR", "nose", "cheekL", "cheekR", "mouthTop", "mouthBottom", "jaw"]) {
    assert.match(faceMeshSrc, new RegExp(`"${group}"`), `mesh group "${group}" missing from faceMesh.js`);
  }
});

test("delegates ALL status->expression logic to lib/faceExpressions.js — never reimplements the mapping inline", () => {
  assert.match(src, /from ["']\.\.\/lib\/faceExpressions["']/);
  assert.match(src, /mapStatusToExpression/);
  // None of the expression vocabulary words should be hardcoded as a
  // literal in this file — the component must only ever obtain them
  // dynamically via mapStatusToExpression(status), never duplicate the
  // lookup table itself.
  for (const word of ["neutral", "listening", "thinking", "speaking", "concerned", "empathetic", "surprised", "calm", "focused"]) {
    assert.doesNotMatch(src, new RegExp(`["']${word}["']`), `"${word}" must not be hardcoded in SaranaFace.jsx`);
  }
});

test("the expression drives rendering via a data-expression attribute (styled entirely in CSS, not inline styles)", () => {
  assert.match(src, /data-expression=\{/);
  assert.doesNotMatch(src, /style=\{\{/, "expressions should be CSS-driven, not inline style objects");
});

test("every expression in the shared fifteen-word vocabulary has a matching CSS selector", () => {
  const vocabMatch = faceExpressionsSrc.match(/FACE_EXPRESSIONS = Object\.freeze\(\[([\s\S]*?)\]\)/);
  assert.ok(vocabMatch, "could not locate FACE_EXPRESSIONS in faceExpressions.js");
  const names = [...vocabMatch[1].matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
  assert.equal(names.length, 15, "expected the full fifteen-expression vocabulary");
  for (const name of names) {
    assert.match(
      css,
      new RegExp(`\\[data-expression="${name}"\\]`),
      `missing CSS for expression "${name}"`,
    );
  }
});

// ── blinking / timers (natural, no leaks) ─────────────────────────────────

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

test("no requestAnimationFrame / per-frame render loop — CSS animations carry the motion instead", () => {
  assert.doesNotMatch(src, /requestAnimationFrame/);
  assert.doesNotMatch(src, /setInterval/, "prefer a single rescheduled timeout over an uncapped interval");
});

// ── visual/technology constraints ─────────────────────────────────────────

test("no canvas, WebGL, Three.js, or photorealistic <img> avatar — SVG-driven procedural geometry only", () => {
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

// ── accessibility (face must not be the ONLY state signal) ────────────────

test("a real text status label exists with role=\"status\"/aria-live so screen readers get it too", () => {
  assert.match(src, /role="status"/);
  assert.match(src, /aria-live="polite"/);
});

// ── JARVIS-mode compatibility ──────────────────────────────────────────────

test("Orb.jsx itself is left completely untouched and still exported as a component (JARVIS mode)", () => {
  assert.match(orbSrc, /export default function Orb\(/);
  assert.match(orbSrc, /className="orb-stage"/);
});

test("Orb.jsx contains no reference to the new face-mesh system — the two visual identities stay fully independent", () => {
  assert.doesNotMatch(orbSrc, /faceMesh/);
  assert.doesNotMatch(orbSrc, /mesh-group/);
});

// ── App.jsx integration ────────────────────────────────────────────────────

test("App.jsx renders SaranaFace in the normal-identity branch, exactly once", () => {
  assert.match(appSrc, /import SaranaFace from ["']\.\/components\/SaranaFace["']/);
  const usages = appSrc.match(/<SaranaFace\b/g) || [];
  assert.equal(usages.length, 1, "SaranaFace must be rendered from exactly one place in App.jsx");
});

test("App.jsx imports and renders Orb exactly once, as the JARVIS-identity branch (not alongside SaranaFace)", () => {
  assert.match(appSrc, /import Orb from ["']\.\/components\/Orb["']/);
  const usages = appSrc.match(/<Orb\b/g) || [];
  assert.equal(usages.length, 1, "Orb must be rendered from exactly one place in App.jsx");
});

test("the central stage is a single conditional: VisionStage first, then an identity-stage wrapper choosing Orb or SaranaFace — never two mounted at once", () => {
  assert.match(
    appSrc,
    /visionRequest\s*\?\s*\(\s*<VisionStage[\s\S]{0,600}identity === "jarvis"[\s\S]{0,200}<Orb[\s\S]{0,300}<SaranaFace/,
    "VisionStage, then identity-gated Orb/SaranaFace must be the branches of the SAME central-stage conditional",
  );
});

test("Orb/SaranaFace are chosen via `identity` state, itself derived from state.jarvisMode — which the frontend only ever sets FROM the backend's jarvis_mode_changed message, never toggled locally", () => {
  assert.match(appSrc, /const targetIdentity = state\.jarvisMode \? "jarvis" : "sarana"/);
  assert.match(appSrc, /case "jarvis_mode_changed":/);
  assert.match(appSrc, /dispatch\(\{\s*type:\s*"JARVIS_MODE"\s*,\s*value:\s*msg\.active\s*\}\)/);
  // No local mic/interrupt/click handler should dispatch JARVIS_MODE —
  // the ONLY dispatch of it in the whole file must be the WS case above.
  const dispatches = appSrc.match(/dispatch\(\{\s*type:\s*"JARVIS_MODE"/g) || [];
  assert.equal(dispatches.length, 1, "JARVIS_MODE must be dispatched from exactly one place — the WS message handler");
});

test("VisionStage (camera/screen) still replaces the identity stage in-place, instantly — never renders alongside it as a second element", () => {
  assert.match(
    appSrc,
    /visionRequest\s*\?\s*\(\s*<VisionStage[\s\S]{0,600}<SaranaFace/,
    "VisionStage and the identity-stage branch must be the two arms of the SAME conditional",
  );
});

// ── SARANA <-> JARVIS crossfade (Human-Orb UI task) ────────────────────────

test("switching identity is a fade transition (a mounted-component swap at the fade midpoint), not an instant hard replace", () => {
  assert.match(appSrc, /identity-stage/);
  assert.match(appSrc, /identityFading/);
  assert.match(appSrc, /setTimeout/);
  assert.match(css, /\.identity-stage\s*\{/);
  assert.match(css, /\.identity-stage-fading\s*\{/);
});

test("the identity crossfade timer is cleaned up (no leaked timeout across re-renders/unmount)", () => {
  const effectBlock = appSrc.match(/targetIdentity === identity[\s\S]{0,400}/)[0];
  assert.match(effectBlock, /return \(\) => clearTimeout/);
});

test("VisionStage bypasses the identity crossfade entirely — camera/screen vision always wins instantly, per the existing architecture", () => {
  // Bounded specifically to <VisionStage ...props.../> itself (up to its
  // own self-closing tag), not an arbitrary character window that would
  // overrun into the following identity-stage branch and false-pass.
  const visionTagMatch = appSrc.match(/<VisionStage\b[\s\S]*?\/>/);
  assert.ok(visionTagMatch, "could not locate the <VisionStage ... /> element");
  assert.doesNotMatch(visionTagMatch[0], /identity-stage/, "VisionStage's own element must not be wrapped in the identity-stage fade");
});

test("the identity crossfade respects prefers-reduced-motion", () => {
  const reducedMotionBlock = css.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*$/)[0];
  assert.match(reducedMotionBlock, /\.identity-stage/);
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
