// src/lib/faceMesh.js — SARANA's visual identity geometry: a simple, bold,
// FILLED-shape face (two round glowing eyes with a highlight, two thick
// brow strokes, one mouth curve, two soft cheek-glow circles) — no outer
// head-ring frame, no dense mesh, no thin wireframe lines. This is a
// direct, deliberate match to the user's own reference image (a cute,
// minimal, high-contrast cartoon face: solid eyes + a white catch-light,
// thick simple brows, an open smiling mouth, blush circles), recolored
// into SARANA's existing glow palette instead of the reference's literal
// black/white/red/peach — see index.css's own header note on that
// translation. Replaces the earlier thin-outline "eyes + mouth only"
// generation (git history has it) — JARVIS's Orb.jsx is untouched either
// way, same as every previous generation of this file.
//
// Hand-authored SVG path/shape data (bezier curves + circle specs), not
// procedural math or a mesh extraction — pure, static, deterministic, no
// DOM/rendering concerns. SaranaFace.jsx turns this data into markup;
// index.css's [data-expression] rules move whole <g> groups via CSS
// transforms, exactly like every earlier generation.

// Controllable anatomical groups. "pupilL"/"pupilR" (the bright center +
// catch-light) stay separate from "eyeL"/"eyeR" (the bold outer eye
// shape) so idle gaze-drift can move just the highlight, independently of
// the eye's own outline — same idea every earlier generation used.
// "cheekL"/"cheekR" are new: the reference's blush is a real, load-bearing
// part of what makes the face read as warm/friendly, not a decoration to
// drop for "less detail" — it's the ONE new element added here, and
// everything else this pass touches is a simplification, not an addition.
// Cheeks come FIRST so they paint behind everything else (SVG paints in
// document order) — a blush glow sitting under the eyes/mouth, not
// floating on top of them.
export const FACE_GROUPS = [
  "cheekL", "cheekR", "browL", "browR", "eyeL", "eyeR", "pupilL", "pupilR", "mouth",
];

export const FACE_VIEWBOX = "0 0 200 240";

// Bold, rounded, filled eye shapes — a proper closed oval (4-bezier
// ellipse approximation, kappa≈0.5523), not the earlier generation's
// slim almond outline. Large enough to be the face's dominant feature,
// matching the reference's own big solid eyes.
export const EYE_PATHS = {
  eyeL: "M 50,108 C 50,91.43 60.75,78 74,78 C 87.25,78 98,91.43 98,108 C 98,124.57 87.25,138 74,138 C 60.75,138 50,124.57 50,108 Z",
  eyeR: "M 102,108 C 102,91.43 112.75,78 126,78 C 139.25,78 150,91.43 150,108 C 150,124.57 139.25,138 126,138 C 112.75,138 102,124.57 102,108 Z",
};

// Center glow + catch-light highlight, both inside each eye shape above.
// The highlight is deliberately large/prominent (matching the reference's
// own bright white dot, not a faint accent) — it's a big part of why the
// reference reads as "alive" rather than a flat glowing disc.
export const IRIS = {
  pupilL: { irisCx: 74, irisCy: 108, irisR: 11, hlCx: 68, hlCy: 100, hlR: 3.2 },
  pupilR: { irisCx: 126, irisCy: 108, irisR: 11, hlCx: 120, hlCy: 100, hlR: 3.2 },
};

// Thick, simple, gently-arced brow strokes — rendered with a bold
// round-capped stroke in CSS (not a thin line), matching the reference's
// solid comma-shaped brows without needing a hand-tapered fill path. A
// real visible gap sits between them (92 to 108) — an earlier version of
// these paths overlapped in the middle and, combined with both strokes'
// round linecaps, rendered as a fused unibrow with a stray dot at the
// seam (caught via an offscreen render during implementation, not left
// for someone else to spot).
export const BROW_PATHS = {
  browL: "M 44,66 Q 72,48 92,60",
  browR: "M 108,60 Q 128,48 156,66",
};

// A single glowing smile curve — SVG auto-closes an open path with a
// straight chord when filled, so this ONE path is a thin bold stroke at
// rest (closed-mouth expressions) and becomes a solid smile-wedge fill
// for open-mouth expressions (speaking/happy/excited/surprised) purely
// via a CSS `fill` toggle — see index.css's own note on this. No second
// path needed for "open" vs "closed".
export const MOUTH_PATH = "M 68,164 Q 100,184 132,164";

// Soft blush glow circles — the reference's own cheek color, translated
// to a low-opacity glow rather than a flat peach disc (see index.css:
// off/faint at idle, warms up for positive expressions, tints toward the
// alert color for concerned — the same "role color, not a literal
// reference color" translation the rest of this palette already uses).
export const CHEEKS = {
  cheekL: { cx: 36, cy: 132, r: 20 },
  cheekR: { cx: 164, cy: 132, r: 20 },
};
