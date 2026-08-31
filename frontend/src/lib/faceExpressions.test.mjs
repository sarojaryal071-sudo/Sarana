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
import { FACE_EXPRESSIONS, mapStatusToExpression } from "./faceExpressions.js";

test("the fourteen-expression vocabulary matches the spec exactly", () => {
  assert.deepEqual(
    [...FACE_EXPRESSIONS].sort(),
    [
      "concerned", "confused", "curious", "happy", "listening",
      "neutral", "reassuring", "sad", "speaking", "thinking",
      "empathetic", "surprised", "calm", "focused",
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
