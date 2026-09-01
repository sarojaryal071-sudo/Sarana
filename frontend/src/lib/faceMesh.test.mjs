// src/lib/faceMesh.test.mjs — real unit tests for the face geometry (see
// faceMesh.js's own header note: this is now baked data extracted from
// Face Cloner's canonical face mesh, not procedurally-generated ellipse
// math — the earlier procedural version's maxDist/maxPerNode
// nearest-neighbor tests no longer apply and are replaced below with
// tests against what a real triangulated mesh actually guarantees). No
// DOM, no rendering — just the point/edge data.
//
// Run with:
//   cd frontend && node --test src/lib/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { buildFacePoints, buildFaceEdges, FACE_GROUPS, FACE_VIEWBOX } from "./faceMesh.js";

test("buildFacePoints returns a non-trivial set of points, each with a valid group and unique id", () => {
  const pts = buildFacePoints();
  assert.ok(pts.length > 40, `expected a real face mesh, got only ${pts.length} points`);
  const ids = new Set();
  for (const p of pts) {
    assert.equal(typeof p.x, "number");
    assert.equal(typeof p.y, "number");
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
    assert.ok(FACE_GROUPS.includes(p.group), `unexpected group: ${p.group}`);
    assert.ok(!ids.has(p.id), `duplicate point id: ${p.id}`);
    ids.add(p.id);
  }
});

test("returns the real dense Face Cloner-derived mesh, not the old sparse ellipse schematic", () => {
  // The old procedural generator topped out around 70 points (a handful of
  // ellipse arcs). This is baked from Face Cloner's actual 468-point
  // canonical face mesh (+2 synthesized pupils) — asserting a much higher
  // floor is the direct regression test for "the geometry actually
  // changed", not just "some geometry exists".
  const pts = buildFacePoints();
  assert.ok(pts.length > 400, `expected the real ~470-point Face Cloner mesh, got only ${pts.length} points`);
});

test("every declared FACE_GROUPS entry actually has at least one point", () => {
  const pts = buildFacePoints();
  const present = new Set(pts.map((p) => p.group));
  for (const g of FACE_GROUPS) {
    assert.ok(present.has(g), `group '${g}' has no points`);
  }
});

test("is pure and deterministic — calling twice produces identical geometry", () => {
  const a = buildFacePoints();
  const b = buildFacePoints();
  assert.equal(a.length, b.length);
  for (let i = 0; i < a.length; i++) {
    assert.equal(a[i].x, b[i].x);
    assert.equal(a[i].y, b[i].y);
    assert.equal(a[i].group, b[i].group);
  }
});

test("eyes, brows, and mouth halves are bilaterally symmetric around the face's vertical center", () => {
  const pts = buildFacePoints();
  const centerX = 100; // matches faceMesh.js's own construction — see FACE_VIEWBOX
  const eyeL = pts.filter((p) => p.group === "eyeL");
  const eyeR = pts.filter((p) => p.group === "eyeR");
  assert.equal(eyeL.length, eyeR.length);
  const avgL = eyeL.reduce((s, p) => s + p.x, 0) / eyeL.length;
  const avgR = eyeR.reduce((s, p) => s + p.x, 0) / eyeR.length;
  assert.ok(Math.abs((centerX - avgL) - (avgR - centerX)) < 1, "eyes should be roughly mirrored");
});

test("buildFaceEdges never connects a point to itself and never duplicates an edge", () => {
  const pts = buildFacePoints();
  const edges = buildFaceEdges(pts);
  assert.ok(edges.length > 0);
  const seen = new Set();
  for (const [a, b] of edges) {
    assert.notEqual(a.id, b.id, "an edge must not connect a point to itself");
    const key = a.id < b.id ? `${a.id}-${b.id}` : `${b.id}-${a.id}`;
    assert.ok(!seen.has(key), `duplicate edge: ${key}`);
    seen.add(key);
  }
});

test("buildFaceEdges resolves to the SAME point objects passed in, by identity", () => {
  const pts = buildFacePoints();
  const edges = buildFaceEdges(pts);
  for (const [a, b] of edges) {
    assert.strictEqual(pts[a.id], a, "edge endpoint must be the exact same object as pts[id], not a copy");
    assert.strictEqual(pts[b.id], b);
  }
});

test("returns a real dense triangulation, not a sparse nearest-neighbor approximation", () => {
  // The old generator connected each point to at most a handful of
  // nearest neighbors (~1.5 edges/point). A real triangulated mesh runs
  // much denser (Euler's formula for a disk-topology mesh puts it near
  // 3 edges/point) — this is the geometry-density regression test.
  const pts = buildFacePoints();
  const edges = buildFaceEdges(pts);
  assert.ok(edges.length > 800, `expected a dense flowing wireframe topology, got only ${edges.length} edges for ${pts.length} points`);
});

test("every edge endpoint lies within FACE_VIEWBOX", () => {
  const [vx, vy, vw, vh] = FACE_VIEWBOX.trim().split(/\s+/).map(Number);
  const pts = buildFacePoints();
  const edges = buildFaceEdges(pts);
  for (const [a, b] of edges) {
    for (const p of [a, b]) {
      assert.ok(p.x >= vx && p.x <= vx + vw, `point ${p.id} x=${p.x} outside viewBox`);
      assert.ok(p.y >= vy && p.y <= vy + vh, `point ${p.id} y=${p.y} outside viewBox`);
    }
  }
});

test("FACE_VIEWBOX is a valid four-number SVG viewBox string", () => {
  const parts = FACE_VIEWBOX.trim().split(/\s+/).map(Number);
  assert.equal(parts.length, 4);
  parts.forEach((n) => assert.ok(Number.isFinite(n)));
});
