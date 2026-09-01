// src/lib/faceMesh.js — SARANA's visual identity geometry: a minimal
// "eyes + brows + mouth only" glowing outline face inside a circular
// frame, matching the reference look the brief asked for directly
// (attractive almond eyes with a glowing iris + catch-light, thin brow
// arcs, a simple glowing smile curve — no dense mesh, no nose/cheek/jaw
// geometry at all).
//
// Hand-authored SVG path data (cubic/quadratic bezier curves), not
// procedural math or a mesh extraction — there is no "mesh" left to
// generate; this is intentionally simple vector line art, the same
// spirit as the reference image. Still a pure, static, deterministic
// module with no DOM/rendering concerns — SaranaFace.jsx turns this data
// into markup, index.css's [data-expression] rules move whole <g> groups
// via CSS transforms, exactly like before.

// Controllable anatomical groups. "pupilL"/"pupilR" (iris + catch-light)
// are separate from "eyeL"/"eyeR" (the outline) so gaze drift can move
// just the iris, independently of the outline shape — same idea as the
// wireframe version's separate pupil dots.
export const FACE_GROUPS = ["browL", "browR", "eyeL", "eyeR", "pupilL", "pupilR", "mouth"];

export const FACE_VIEWBOX = "0 0 200 240";

// The big circular frame the face sits inside — drawn once, not part of
// FACE_GROUPS (never individually expression-transformed).
export const HEAD_CIRCLE = { cx: 100, cy: 118, r: 92 };

// Eye outline: a simple almond/leaf shape via two cubic beziers.
export const EYE_PATHS = {
  eyeL: "M 54,108 C 58,94 86,94 90,108 C 86,120 58,120 54,108 Z",
  eyeR: "M 110,108 C 114,94 142,94 146,108 C 142,120 114,120 110,108 Z",
};

// Iris (glowing circle) + catch-light (small bright highlight dot),
// centered inside each eye outline above.
export const IRIS = {
  pupilL: { irisCx: 72, irisCy: 108, irisR: 8, hlCx: 69, hlCy: 104.5, hlR: 1.8 },
  pupilR: { irisCx: 128, irisCy: 108, irisR: 8, hlCx: 125, hlCy: 104.5, hlR: 1.8 },
};

// Thin, gently-arced brow strokes above each eye.
export const BROW_PATHS = {
  browL: "M 50,83 Q 72,72 94,81",
  browR: "M 106,81 Q 128,72 150,83",
};

// A single glowing smile curve — no separate upper/lower lip groups
// (there's nothing to seal/part here, unlike the old mesh mouth); real
// playback amplitude scales this one group vertically instead (see
// index.css's [data-expression="speaking"] rule and --mouth-open).
export const MOUTH_PATH = "M 74,160 Q 100,174 126,160";
