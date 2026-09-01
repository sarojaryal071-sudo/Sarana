// src/lib/mouthLevel.test.mjs — real unit tests for the tiny pub/sub that
// carries real per-chunk playback amplitude from audioOut.js to
// SaranaFace's mouth (see mouthLevel.js's own header note on why this
// exists instead of React state or a polling loop).
//
// Run with:
//   cd frontend && node --test src/lib/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { setMouthLevel, getMouthLevel, subscribeMouthLevel } from "./mouthLevel.js";

test("setMouthLevel updates getMouthLevel", () => {
  setMouthLevel(0.42);
  assert.equal(getMouthLevel(), 0.42);
});

test("setMouthLevel clamps to 0..1", () => {
  setMouthLevel(-5);
  assert.equal(getMouthLevel(), 0);
  setMouthLevel(5);
  assert.equal(getMouthLevel(), 1);
});

test("subscribeMouthLevel fires immediately with the current level", () => {
  setMouthLevel(0.3);
  const seen = [];
  const unsubscribe = subscribeMouthLevel((v) => seen.push(v));
  assert.deepEqual(seen, [0.3]);
  unsubscribe();
});

test("subscribers are notified on every subsequent setMouthLevel call", () => {
  setMouthLevel(0);
  const seen = [];
  const unsubscribe = subscribeMouthLevel((v) => seen.push(v));
  setMouthLevel(0.5);
  setMouthLevel(0.9);
  assert.deepEqual(seen, [0, 0.5, 0.9]);
  unsubscribe();
});

test("unsubscribe stops further notifications", () => {
  setMouthLevel(0);
  const seen = [];
  const unsubscribe = subscribeMouthLevel((v) => seen.push(v));
  unsubscribe();
  setMouthLevel(0.7);
  assert.deepEqual(seen, [0], "only the immediate fire-on-subscribe call should have landed");
});

test("multiple subscribers are independent", () => {
  setMouthLevel(0);
  const a = [];
  const b = [];
  const unsubA = subscribeMouthLevel((v) => a.push(v));
  const unsubB = subscribeMouthLevel((v) => b.push(v));
  setMouthLevel(0.6);
  unsubA();
  setMouthLevel(0.1);
  assert.deepEqual(a, [0, 0.6]);
  assert.deepEqual(b, [0, 0.6, 0.1]);
  unsubB();
});
