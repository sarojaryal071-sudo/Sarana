// src/lib/mouthLevel.js — a tiny, dependency-free pub/sub so real playback
// amplitude (computed once per audio chunk in lib/audioOut.js — see its own
// header note) can reach SaranaFace's mouth without either (a) routing it
// through React state, which would re-render the whole app tree once per
// chunk during every response, or (b) SaranaFace polling for it with a
// requestAnimationFrame/setInterval loop, which the component's own tests
// forbid on purpose (CSS carries the motion, not a per-frame JS loop — see
// SaranaFace.test.mjs). This module is the deliberately boring alternative:
// one setter, one subscribe, no timers, no DOM, no framework.
let level = 0;
const subscribers = new Set();

/** Called by audioOut.js's onActivity callback wiring (see App.jsx) once
 * per chunk actually scheduled for playback, and once more with 0 when
 * playback drains/stops — never polled, never guessed. */
export function setMouthLevel(v) {
  level = Math.max(0, Math.min(1, v));
  for (const fn of subscribers) fn(level);
}

export function getMouthLevel() {
  return level;
}

/** Returns an unsubscribe function. Fires once immediately with the
 * current level so a late subscriber (e.g. SaranaFace mounting mid-speech
 * after a SARANA<->JARVIS switch) isn't stuck at a stale 0. */
export function subscribeMouthLevel(fn) {
  subscribers.add(fn);
  fn(level);
  return () => subscribers.delete(fn);
}
