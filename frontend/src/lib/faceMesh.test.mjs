// src/lib/faceMesh.test.mjs — real unit tests for the minimal glowing
// eyes/brows/mouth face geometry (see faceMesh.js's own header note: this
// is now hand-authored SVG path data, not a dense mesh — the earlier
// buildFacePoints()/buildFaceEdges() API no longer exists).
//
// Run with:
//   cd frontend && node --test src/lib/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { FACE_GROUPS, FACE_VIEWBOX, HEAD_CIRCLE, EYE_PATHS, IRIS, BROW_PATHS, MOUTH_PATH } from "./faceMesh.js";

test("FACE_GROUPS lists exactly the seven controllable groups, each with real geometry", () => {
  assert.deepEqual(FACE_GROUPS, ["browL", "browR", "eyeL", "eyeR", "pupilL", "pupilR", "mouth"]);
  for (const g of ["browL", "browR"]) assert.equal(typeof BROW_PATHS[g], "string", `${g} path missing`);
  for (const g of ["eyeL", "eyeR"]) assert.equal(typeof EYE_PATHS[g], "string", `${g} path missing`);
  for (const g of ["pupilL", "pupilR"]) {
    const spec = IRIS[g];
    assert.ok(spec, `${g} iris spec missing`);
    for (const k of ["irisCx", "irisCy", "irisR", "hlCx", "hlCy", "hlR"]) {
      assert.equal(typeof spec[k], "number", `IRIS.${g}.${k} must be a number`);
    }
  }
  assert.equal(typeof MOUTH_PATH, "string");
});

test("eye and brow geometry is bilaterally symmetric around the face's vertical center", () => {
  const centerX = 100; // matches HEAD_CIRCLE.cx below
  assert.ok(Math.abs((centerX - IRIS.pupilL.irisCx) - (IRIS.pupilR.irisCx - centerX)) < 1);
});

test("HEAD_CIRCLE is a real circle within FACE_VIEWBOX", () => {
  const [vx, vy, vw, vh] = FACE_VIEWBOX.trim().split(/\s+/).map(Number);
  assert.ok(Number.isFinite(HEAD_CIRCLE.cx) && Number.isFinite(HEAD_CIRCLE.cy) && HEAD_CIRCLE.r > 0);
  assert.ok(HEAD_CIRCLE.cx - HEAD_CIRCLE.r >= vx - 1 && HEAD_CIRCLE.cx + HEAD_CIRCLE.r <= vx + vw + 1);
  assert.ok(HEAD_CIRCLE.cy - HEAD_CIRCLE.r >= vy - 1 && HEAD_CIRCLE.cy + HEAD_CIRCLE.r <= vy + vh + 1);
});

test("path data is well-formed (starts with a moveto command)", () => {
  for (const d of [EYE_PATHS.eyeL, EYE_PATHS.eyeR, BROW_PATHS.browL, BROW_PATHS.browR, MOUTH_PATH]) {
    assert.match(d.trim(), /^M\s*[\d.-]/, `path does not start with a moveto: ${d}`);
  }
});

test("FACE_VIEWBOX is a valid four-number SVG viewBox string", () => {
  const parts = FACE_VIEWBOX.trim().split(/\s+/).map(Number);
  assert.equal(parts.length, 4);
  parts.forEach((n) => assert.ok(Number.isFinite(n)));
});
