// src/components/IdentityTransition.test.mjs — the SARANA<->JARVIS "HUD
// rebuild" transition overlay (see that component's own header for the
// full design brief: "not a plain cinematic transition... like in an AI
// technological movie where the current UI goes through a fast rebuild
// of another UI with moving neons and a cinematic building process").
//
// Source-inspection style, same convention as every other component test
// in this project (see SaranaFace.test.mjs's own header note on why —
// this project renders no components in tests).
//
// Run with:
//   cd frontend && node --test src/components/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "IdentityTransition.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "index.css"), "utf8");
const orbSrc = fs.readFileSync(path.join(__dirname, "Orb.jsx"), "utf8");
const saranaFaceSrc = fs.readFileSync(path.join(__dirname, "SaranaFace.jsx"), "utf8");

// ── component structure ──────────────────────────────────────────────────

test("is purely presentational (aria-hidden), no interactive elements, no state/effects of its own", () => {
  assert.match(src, /aria-hidden="true"/);
  assert.doesNotMatch(src, /<button/i);
  assert.doesNotMatch(src, /onClick/);
  assert.doesNotMatch(src, /useState|useEffect|useRef/, "this is a pure function of its props — no internal state/timers");
});

test("no canvas, WebGL, or particle-physics library — SVG + CSS keyframes only, no new dependency", () => {
  assert.doesNotMatch(src, /<canvas/i);
  assert.doesNotMatch(src, /three\.js|WebGLRenderer/i);
  assert.doesNotMatch(src, /from ["'](?!\.\.?\/)/m, "every import must be a relative project module, never a new package");
});

test("geometry (rings, spokes, particles, grid) is computed ONCE at module load from pure trig — no randomness, no per-render recomputation", () => {
  const componentStart = src.indexOf("export default function IdentityTransition");
  for (const name of ["SPOKES", "PARTICLES", "GRID_LINES"]) {
    const idx = src.indexOf(`const ${name}`);
    assert.ok(idx > -1 && idx < componentStart, `${name} must be computed at module scope, before the component`);
  }
  assert.doesNotMatch(src, /Math\.random/);
});

test("accepts exactly the two props it needs (phase, targetIdentity) — no status/assistantName reach-in, it never needs to know app state", () => {
  assert.match(src, /\{\s*phase\s*,\s*targetIdentity\s*\}/);
});

// ── phase system (deconstruct -> swap -> rebuild -> gone) ─────────────────

test("App.jsx drives two distinct phases (deconstruct, rebuild) around the actual component swap, not a single fade class", () => {
  const appSrc = fs.readFileSync(path.join(__dirname, "..", "App.jsx"), "utf8");
  assert.match(appSrc, /setIdentityPhase\("deconstruct"\)/);
  assert.match(appSrc, /setIdentityPhase\("rebuild"\)/);
  assert.match(appSrc, /setIdentityPhase\(null\)/);
  // The real component swap (setIdentity) must happen BETWEEN the two
  // phases, not before deconstruct starts or after rebuild ends.
  const effectBlock = appSrc.match(/targetIdentity === identity[\s\S]{0,600}/)[0];
  const deconstructIdx = effectBlock.indexOf('setIdentityPhase("deconstruct")');
  const swapIdx = effectBlock.indexOf("setIdentity(targetIdentity)");
  const rebuildIdx = effectBlock.indexOf('setIdentityPhase("rebuild")');
  assert.ok(deconstructIdx < swapIdx && swapIdx < rebuildIdx, "order must be: deconstruct starts, THEN the real swap, THEN rebuild starts");
});

test("IdentityTransition only mounts while a phase is active — it fully unmounts once the transition ends, never lingers", () => {
  const appSrc = fs.readFileSync(path.join(__dirname, "..", "App.jsx"), "utf8");
  assert.match(appSrc, /\{identityPhase && <IdentityTransition phase=\{identityPhase\} targetIdentity=\{targetIdentity\} \/>\}/);
});

test("both transition timers are cleaned up on unmount/re-trigger (no leaked setTimeout)", () => {
  const appSrc = fs.readFileSync(path.join(__dirname, "..", "App.jsx"), "utf8");
  const effectBlock = appSrc.match(/targetIdentity === identity[\s\S]{0,700}/)[0];
  assert.match(effectBlock, /return \(\) => clearTimeout\(identityTimerRef\.current\)/);
  // Only ONE ref is used for both nested timers — the second timeout's id
  // must overwrite the same ref, not a second untracked one.
  const refAssignments = effectBlock.match(/identityTimerRef\.current = setTimeout/g) || [];
  assert.equal(refAssignments.length, 2, "both the deconstruct->swap and rebuild->end timers must go through the same ref");
});

// ── JARVIS-orb protection (still applies to this new component too) ─────

test("Orb.jsx is completely untouched by this — no reference to it or its internals anywhere in IdentityTransition's actual code", () => {
  // Scoped to the component function body, not the module's own
  // docstring (which legitimately mentions "Orb.jsx" in prose — see
  // this project's established precedent for this exact false-positive
  // shape, e.g. SaranaFace.test.mjs's own fist/imshow test notes).
  const componentBody = src.slice(src.indexOf("export default function IdentityTransition"));
  assert.doesNotMatch(componentBody, /\bOrb\b/);
  assert.match(orbSrc, /export default function Orb\(/, "sanity check: Orb.jsx itself still exports normally");
});

test("SaranaFace.jsx has no reference to IdentityTransition — the overlay is composed in App.jsx, never reached into by either identity component", () => {
  assert.doesNotMatch(saranaFaceSrc, /IdentityTransition/);
});

// ── visual techniques actually present (not just claimed) ───────────────

test("rings/spokes DRAW themselves via stroke-dasharray/dashoffset (the SVG line-draw technique) — the 'cinematic building' part of the brief, not just an opacity fade", () => {
  assert.match(css, /\.identity-transition-rebuild \.it-ring \{[\s\S]*?stroke-dasharray:[\s\S]*?stroke-dashoffset:/);
  assert.match(css, /@keyframes it-ring-draw[\s\S]*?stroke-dashoffset:\s*0/);
});

test("scanline sweeps exist for both phases (down while deconstructing, up while rebuilding) — the 'moving neons' part of the brief", () => {
  assert.match(css, /it-scan-sweep-down/);
  assert.match(css, /it-scan-sweep-up/);
  assert.match(src, /it-scanline-a/);
  assert.match(src, /it-scanline-b/);
});

test("a particle burst/converge effect exists on a fixed, deterministic ring of points — not a spawn/kill particle system", () => {
  assert.match(src, /PARTICLES/);
  assert.match(css, /it-particle-burst/);
  assert.match(css, /it-particle-converge/);
});

test("each target identity contributes its own accent color via a CSS custom property, not a hardcoded color", () => {
  assert.match(css, /\.identity-transition-jarvis \{ --it-accent: var\(--acc\); \}/);
  assert.match(css, /\.identity-transition-sarana \{ --it-accent: var\(--face-glow\); \}/);
});

test("respects prefers-reduced-motion — the whole overlay is suppressed, not just slowed down", () => {
  const reducedMotionBlocks = css.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n\}/g) || [];
  const covering = reducedMotionBlocks.some((b) => b.includes(".identity-transition") && b.includes("display: none"));
  assert.ok(covering, "expected a reduced-motion block that sets .identity-transition { display: none; }");
});

test("the overlay is pointer-events:none and purely decorative — it must never intercept clicks meant for the real UI underneath", () => {
  const rule = css.match(/\.identity-transition\s*\{[\s\S]*?\}/);
  assert.ok(rule);
  assert.match(rule[0], /pointer-events:\s*none/);
});
