// src/lib/faceExpressions.test.mjs — real unit tests for the pure
// status->expression mapping (see faceExpressions.js's own header note on
// why it's deliberately kept as a tiny, dependency-free, directly
// importable/testable module rather than logic inlined into SaranaFace.jsx).
//
// Run with:
//   cd frontend && node --test src/lib/*.test.mjs
// (or `npm test`, see package.json)
import assert from "node:assert/strict";
import { test } from "node:test";
import { FACE_EXPRESSIONS, mapStatusToExpression, resolveExpression } from "./faceExpressions.js";

test("the fifteen-expression vocabulary matches the spec exactly", () => {
  assert.deepEqual(
    [...FACE_EXPRESSIONS].sort(),
    [
      "concerned", "confused", "curious", "happy", "listening",
      "neutral", "reassuring", "sad", "speaking", "thinking",
      "empathetic", "surprised", "calm", "focused", "excited",
    ].sort(),
  );
});

test("the vocabulary array is frozen — no accidental runtime mutation", () => {
  assert.ok(Object.isFrozen(FACE_EXPRESSIONS));
});

test("maps every real existing app status to a deterministic, in-vocabulary expression", () => {
  const cases = {
    SLEEPING: "neutral",
    LISTENING: "listening",
    THINKING: "thinking",
    SPEAKING: "speaking",
    MUTED: "concerned",
  };
  for (const [status, expected] of Object.entries(cases)) {
    const got = mapStatusToExpression(status);
    assert.equal(got, expected, `status ${status}`);
    assert.ok(FACE_EXPRESSIONS.includes(got), `${got} must be a real supported expression`);
  }
});

test("an unknown/unrecognized status falls back to neutral, deterministically — never random, never throws", () => {
  assert.equal(mapStatusToExpression("SOMETHING_NEW"), "neutral");
  assert.equal(mapStatusToExpression(undefined), "neutral");
  assert.equal(mapStatusToExpression(null), "neutral");
  assert.equal(mapStatusToExpression(""), "neutral");
});

test("is pure — same input always produces the same output", () => {
  for (let i = 0; i < 5; i++) {
    assert.equal(mapStatusToExpression("SPEAKING"), "speaking");
  }
});

test("status strings are case-sensitive on purpose — mirrors the exact strings the backend/reducer use, never guesses at casing", () => {
  assert.equal(mapStatusToExpression("speaking"), "neutral");
  assert.equal(mapStatusToExpression("Speaking"), "neutral");
});

// ── resolveExpression — SARANA Face UI (set_expression tool override) ──

test("a live, unexpired override wins while mechanical status is idle (listening/neutral)", () => {
  assert.equal(resolveExpression("LISTENING", { expression: "sad", until: 1000 }, 500), "sad");
  assert.equal(resolveExpression("SLEEPING", { expression: "happy", until: 1000 }, 500), "happy");
});

test("an expired override is ignored — falls back to the mechanical mapping", () => {
  assert.equal(resolveExpression("LISTENING", { expression: "sad", until: 500 }, 999), "listening");
});

test("no override at all falls back to the mechanical mapping, exactly like mapStatusToExpression alone", () => {
  assert.equal(resolveExpression("LISTENING", null, 123), "listening");
  assert.equal(resolveExpression("LISTENING", undefined, 123), "listening");
});

test("speaking/thinking/muted ALWAYS win over an active override — they carry real functional information a cosmetic mood must never hide", () => {
  const override = { expression: "happy", until: Infinity };
  assert.equal(resolveExpression("SPEAKING", override, 0), "speaking");
  assert.equal(resolveExpression("THINKING", override, 0), "thinking");
  assert.equal(resolveExpression("MUTED", override, 0), "concerned");
});

test("the override reapplies the instant mechanical status returns to idle, as long as it hasn't expired", () => {
  const override = { expression: "excited", until: 10_000 };
  assert.equal(resolveExpression("SPEAKING", override, 100), "speaking");
  assert.equal(resolveExpression("LISTENING", override, 200), "excited");
});

test("is pure — never reads the real clock itself (no internal Date.now()), same `now` always produces the same result", () => {
  const override = { expression: "curious", until: 500 };
  for (let i = 0; i < 5; i++) {
    assert.equal(resolveExpression("LISTENING", override, 100), "curious");
  }
});
