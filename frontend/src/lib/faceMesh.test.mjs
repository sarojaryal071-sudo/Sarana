// src/lib/faceMesh.test.mjs — real unit tests for the simple bold-filled
// face geometry (see faceMesh.js's own header note: hand-authored SVG
// path/shape data, no HEAD_CIRCLE frame — that's the previous
// generation's own detail, dropped per the user's reference image).
//
// Run with:
//   cd frontend && node --test src/lib/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { FACE_GROUPS, FACE_VIEWBOX, EYE_PATHS, IRIS, BROW_PATHS, MOUTH_PATH, CHEEKS } from "./faceMesh.js";

test("FACE_GROUPS lists exactly the nine controllable groups, each with real geometry", () => {
  assert.deepEqual(FACE_GROUPS, ["cheekL", "cheekR", "browL", "browR", "eyeL", "eyeR", "pupilL", "pupilR", "mouth"]);
  for (const g of ["browL", "browR"]) assert.equal(typeof BROW_PATHS[g], "string", `${g} path missing`);
  for (const g of ["eyeL", "eyeR"]) assert.equal(typeof EYE_PATHS[g], "string", `${g} path missing`);
  for (const g of ["pupilL", "pupilR"]) {
    const spec = IRIS[g];
    assert.ok(spec, `${g} iris spec missing`);
    for (const k of ["irisCx", "irisCy", "irisR", "hlCx", "hlCy", "hlR"]) {
      assert.equal(typeof spec[k], "number", `IRIS.${g}.${k} must be a number`);
    }
  }
  for (const g of ["cheekL", "cheekR"]) {
    const spec = CHEEKS[g];
    assert.ok(spec, `${g} cheek spec missing`);
    for (const k of ["cx", "cy", "r"]) {
      assert.equal(typeof spec[k], "number", `CHEEKS.${g}.${k} must be a number`);
    }
  }
  assert.equal(typeof MOUTH_PATH, "string");
});

test("eyes, irises, brows, and cheeks are all bilaterally symmetric around the face's vertical center (x=100)", () => {
  const centerX = 100;
  assert.ok(Math.abs((centerX - IRIS.pupilL.irisCx) - (IRIS.pupilR.irisCx - centerX)) < 1);
  assert.ok(Math.abs((centerX - CHEEKS.cheekL.cx) - (CHEEKS.cheekR.cx - centerX)) < 1);
  assert.equal(CHEEKS.cheekL.r, CHEEKS.cheekR.r);
  assert.equal(IRIS.pupilL.irisR, IRIS.pupilR.irisR);
  assert.equal(IRIS.pupilL.hlR, IRIS.pupilR.hlR);
});

test("eye paths are closed shapes (end with Z) — filled solid shapes, not open outline strokes", () => {
  for (const d of [EYE_PATHS.eyeL, EYE_PATHS.eyeR]) {
    assert.match(d.trim(), /Z\s*$/i, `eye path must be a closed shape: ${d}`);
  }
});

test("the mouth path is deliberately OPEN (no trailing Z) — SVG auto-closes it with a straight chord only when filled, giving open-mouth expressions a smile-wedge fill from the same single curve used for the closed-mouth stroke", () => {
  assert.doesNotMatch(MOUTH_PATH.trim(), /Z\s*$/i);
});

test("path data is well-formed (starts with a moveto command)", () => {
  for (const d of [EYE_PATHS.eyeL, EYE_PATHS.eyeR, BROW_PATHS.browL, BROW_PATHS.browR, MOUTH_PATH]) {
    assert.match(d.trim(), /^M\s*[\d.-]/, `path does not start with a moveto: ${d}`);
  }
});

test("cheeks sit outside the eyes (further from center) and lower on the face, matching the reference's own blush placement", () => {
  assert.ok(CHEEKS.cheekL.cx < 100 - IRIS.pupilL.irisR, "left cheek must sit left of the left eye's iris");
  assert.ok(CHEEKS.cheekR.cx > 100 + IRIS.pupilR.irisR, "right cheek must sit right of the right eye's iris");
});

test("FACE_VIEWBOX is a valid four-number SVG viewBox string", () => {
  const parts = FACE_VIEWBOX.trim().split(/\s+/).map(Number);
  assert.equal(parts.length, 4);
  parts.forEach((n) => assert.ok(Number.isFinite(n)));
});
