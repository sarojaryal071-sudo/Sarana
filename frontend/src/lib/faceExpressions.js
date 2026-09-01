// src/lib/faceExpressions.js — the ONE deterministic mapping from SARANA's
// existing, already-authoritative UI status (App.jsx's `displayStatus`,
// itself derived from AssistantContext's assistantStatus plus the
// client-only MUTED overlay — see App.jsx's own comment on that) to a
// SaranaFace expression (components/SaranaFace.jsx). Kept in its own tiny
// pure module — no React, no timers, no DOM — so the mapping is trivially
// unit-testable and the component itself stays a thin renderer.
//
// Stage 1 scope (see core/prompt.txt "Master Task — Replace the SARANA Orb
// with a Minimal Expressive AI Face", sections 8-9): the full expression
// vocabulary the face SUPPORTS is deliberately wider than what this mapping
// can currently REACH. happy/sad/curious/confused/reassuring have no
// existing backend/UI signal to honestly drive them from yet — building
// one (an LLM emotion classifier, new Gemini prompt metadata, a new
// WebSocket message type) is explicitly out of scope for this stage. Those
// five are fully implemented in SaranaFace's CSS so the architecture stays
// extensible for a later stage, but mapStatusToExpression() below only
// ever returns one of the five states real, already-existing app state can
// honestly justify — never randomly, never guessed.

export const FACE_EXPRESSIONS = Object.freeze([
  "neutral",
  "listening",
  "thinking",
  "speaking",
  "happy",
  "concerned",
  "sad",
  "curious",
  "confused",
  "reassuring",
  // Added for the wireframe-face system (Human-Orb UI task) — same rule
  // as the five above: fully implemented in CSS so the vocabulary stays
  // extensible, but mapStatusToExpression() below still only ever
  // returns one of the five states real, already-existing app state can
  // honestly justify. No emotion classifier exists in this app; these
  // four are not yet reachable from any real signal.
  "empathetic",
  "surprised",
  "calm",
  "focused",
  // Face Cloner-inspired pass (SARANA Face UI task): excited completes
  // the task brief's explicit minimum expression list. Same rule as every
  // entry above — fully implemented in CSS so the vocabulary stays
  // extensible, but mapStatusToExpression() below still only ever returns
  // a state real, already-existing app state can honestly justify.
  "excited",
]);

// Mirrors the exact status precedence Orb.jsx itself already renders
// (muted > speaking > thinking > sleeping > listening — see Orb.jsx's own
// header comment on why that precedence matches ui.py's HudCanvas), so
// switching the rendered component never changes what a given backend
// state visually means.
const STATUS_TO_EXPRESSION = {
  MUTED: "concerned", // mic is off — the one existing "can't hear you" signal
  SPEAKING: "speaking",
  THINKING: "thinking",
  SLEEPING: "neutral",
  LISTENING: "listening",
};

export function mapStatusToExpression(status) {
  return STATUS_TO_EXPRESSION[status] || "neutral";
}
