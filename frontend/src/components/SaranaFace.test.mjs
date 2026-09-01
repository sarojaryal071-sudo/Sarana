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

test("renders the bold-filled eyes/brows/mouth/cheeks geometry from lib/faceMesh.js — hand-authored SVG paths, not a dense mesh or an image asset", () => {
  assert.match(src, /from ["']\.\.\/lib\/faceMesh["']/);
  assert.match(src, /EYE_PATHS/);
  assert.match(src, /BROW_PATHS/);
  assert.match(src, /MOUTH_PATH/);
  assert.match(src, /CHEEKS/);
  assert.doesNotMatch(src, /<img/i, "no image asset for the face");
});

test("eyes render as a filled solid shape (face-eye), not a stroked outline — matches the reference image's bold solid eyes", () => {
  assert.match(src, /className="face-eye"/);
  const rule = css.match(/\.face-eye\s*\{[\s\S]*?\}/);
  assert.ok(rule, ".face-eye rule not found");
  assert.match(rule[0], /fill:\s*var\(--face-glow\)/);
});

test("the mouth is ONE path, styled as a bold stroke by default with fill toggled on per-expression (auto-close-on-fill), never two separate open/closed path strings", () => {
  const mouthCount = (src.match(/MOUTH_PATH/g) || []).length;
  assert.ok(mouthCount >= 1);
  assert.doesNotMatch(faceMeshSrc, /MOUTH_PATH_OPEN|MOUTH_OPEN_PATH/, "must not have grown a second mouth path for the open state");
  const rule = css.match(/\.face-mouth\s*\{[\s\S]*?\}/);
  assert.match(rule[0], /fill:\s*none/, "the mouth must default to an unfilled stroke — fill is a per-expression override");
});

test("no outer head-ring/frame — the previous generation's HEAD_CIRCLE is gone, matching the reference image's own lack of a face outline", () => {
  assert.doesNotMatch(faceMeshSrc, /HEAD_CIRCLE/);
  assert.doesNotMatch(src, /HEAD_CIRCLE|face-outline/);
});

test("no decorative sparks/scanline layer — this generation simplifies, it doesn't add ambient decoration on top of the face", () => {
  assert.doesNotMatch(src, /mesh-spark|sarana-face-scanline/);
  assert.doesNotMatch(css, /mesh-spark|sarana-face-scanline/);
});

test("cheeks (blush) are present at a non-zero baseline opacity, not opacity:0 by default — the reference shows blush constantly, not only on a 'happy' expression", () => {
  const rule = css.match(/\.face-cheek\s*\{[\s\S]*?\}/);
  assert.ok(rule, ".face-cheek rule not found");
  const opacityMatch = rule[0].match(/opacity:\s*([\d.]+)/);
  assert.ok(opacityMatch, "no baseline opacity declared on .face-cheek");
  assert.ok(Number(opacityMatch[1]) > 0, "cheeks must not default to fully invisible");
});

test("geometry is static, imported data — never recomputed per-render", () => {
  // Every geometry constant (paths, iris/highlight specs, the head
  // circle) is imported directly from lib/faceMesh.js and referenced as
  // plain data in JSX — there is no build*() call to recompute, so this
  // just guards against a future regression reintroducing one inside the
  // component body.
  const componentBody = src.slice(src.indexOf("export default function SaranaFace"));
  assert.doesNotMatch(componentBody, /function build[A-Z]/, "geometry must stay static imported data, not a recomputed build*() call");
});

test("has all nine anatomical groups referenced (cheeks, brows, eyes, pupils, mouth)", () => {
  for (const group of ["cheekL", "cheekR", "browL", "browR", "eyeL", "eyeR", "pupilL", "pupilR", "mouth"]) {
    assert.match(faceMeshSrc, new RegExp(`"${group}"`), `group "${group}" missing from faceMesh.js`);
  }
});

test("cheeks render first in FACE_GROUPS so the blush glow paints BEHIND the eyes/brows/mouth, not on top of them", () => {
  assert.match(faceMeshSrc, /FACE_GROUPS = \[\s*"cheekL",\s*"cheekR"/, "cheekL/cheekR must be the first two entries in FACE_GROUPS");
});

test("delegates ALL status/override->expression logic to lib/faceExpressions.js's resolveExpression() — never reimplements the mapping or the override-priority rules inline", () => {
  assert.match(src, /from ["']\.\.\/lib\/faceExpressions["']/);
  assert.match(src, /resolveExpression/);
  // None of the expression vocabulary words should be hardcoded as a
  // literal in this file — the component must only ever obtain them
  // dynamically via resolveExpression(status, override, now), never
  // duplicate the lookup table or the override-priority logic itself.
  for (const word of ["neutral", "listening", "thinking", "speaking", "concerned", "empathetic", "surprised", "calm", "focused"]) {
    assert.doesNotMatch(src, new RegExp(`["']${word}["']`), `"${word}" must not be hardcoded in SaranaFace.jsx`);
  }
});

test("passes a real Date.now() as resolveExpression's `now` — never a stale/cached timestamp", () => {
  assert.match(src, /resolveExpression\([^)]*Date\.now\(\)/);
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

test("accepts every prop Orb does ({ status, assistantName }), plus one optional expressionOverride Orb doesn't need — interchangeable in the same slot either way", () => {
  assert.match(src, /\{\s*status\s*,\s*assistantName\s*,\s*expressionOverride\s*\}/);
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

test("App.jsx renders SaranaFace in the normal-identity branch, exactly once, wired to state.expressionOverride", () => {
  assert.match(appSrc, /import SaranaFace from ["']\.\/components\/SaranaFace["']/);
  const usages = appSrc.match(/<SaranaFace\b/g) || [];
  assert.equal(usages.length, 1, "SaranaFace must be rendered from exactly one place in App.jsx");
  const tagMatch = appSrc.match(/<SaranaFace\b[^>]*\/>/);
  assert.ok(tagMatch, "could not locate the <SaranaFace ... /> element");
  assert.match(tagMatch[0], /expressionOverride=\{state\.expressionOverride\}/);
});

// ── SARANA Face UI: set_expression tool override (real bug fix — a user
// directly asked SARANA to "show a sad expression" and was told it
// couldn't be done) ─────────────────────────────────────────────────────

test("App.jsx handles the backend's expression_override WS message and dispatches EXPRESSION_OVERRIDE with a real Date.now()-based `until`", () => {
  assert.match(appSrc, /case "expression_override":/);
  const caseBlock = appSrc.match(/case "expression_override":[\s\S]{0,700}/)[0];
  assert.match(caseBlock, /type:\s*"EXPRESSION_OVERRIDE"/);
  assert.match(caseBlock, /expression:\s*msg\.expression/);
  assert.match(caseBlock, /Date\.now\(\)\s*\+/, "until must be computed from the real clock, not hardcoded");
});

test("an active override clears itself via a real scheduled timer (not a polling interval), cleaned up on change/unmount", () => {
  const effectBlock = appSrc.match(/if \(!state\.expressionOverride\) return undefined;[\s\S]{0,500}/)[0];
  assert.match(effectBlock, /setTimeout/);
  assert.doesNotMatch(effectBlock, /setInterval/);
  assert.match(effectBlock, /return \(\) => clearTimeout/);
  assert.match(effectBlock, /dispatch\(\{\s*type:\s*"EXPRESSION_OVERRIDE"\s*,\s*expression:\s*null/);
});

test("AssistantContext.jsx's reducer treats expression:null as an explicit clear, and resets expressionOverride on logout via ...initialState", () => {
  const contextSrc = fs.readFileSync(path.join(__dirname, "..", "state", "AssistantContext.jsx"), "utf8");
  assert.match(contextSrc, /expressionOverride:\s*null/);
  assert.match(contextSrc, /case "EXPRESSION_OVERRIDE":/);
  const caseBlock = contextSrc.match(/case "EXPRESSION_OVERRIDE":[\s\S]{0,300}/)[0];
  assert.match(caseBlock, /action\.expression\s*\?/, "must branch on action.expression to support an explicit null-clear");
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
