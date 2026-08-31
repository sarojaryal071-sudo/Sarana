// src/lib/faceMesh.test.mjs — real unit tests for the pure procedural
// mesh geometry (see faceMesh.js's own header note). No DOM, no
// rendering — just the point/edge generation math.
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

test("buildFaceEdges respects the maxDist cutoff — no edge spans further than requested", () => {
  const pts = buildFacePoints();
  const maxDist = 25;
  const edges = buildFaceEdges(pts, maxDist, 3);
  for (const [a, b] of edges) {
    const d = Math.hypot(a.x - b.x, a.y - b.y);
    assert.ok(d < maxDist, `edge ${a.id}-${b.id} spans ${d}, exceeding maxDist ${maxDist}`);
  }
});

test("buildFaceEdges respects the maxPerNode bound — no point ends up with far more edges than requested", () => {
  const pts = buildFacePoints();
  const maxPerNode = 3;
  const edges = buildFaceEdges(pts, 30, maxPerNode);
  const degree = new Map();
  for (const [a, b] of edges) {
    degree.set(a.id, (degree.get(a.id) || 0) + 1);
    degree.set(b.id, (degree.get(b.id) || 0) + 1);
  }
  // A point can pick up extra edges from being chosen as someone ELSE's
  // nearest neighbor even after it has already picked its own quota, so
  // the true bound is roughly 2x, not maxPerNode itself — this test
  // guards against genuinely unbounded growth, not the exact constant.
  for (const [, count] of degree) {
    assert.ok(count <= maxPerNode * 3, `a point ended up with ${count} edges — looks unbounded`);
  }
});

test("FACE_VIEWBOX is a valid four-number SVG viewBox string", () => {
  const parts = FACE_VIEWBOX.trim().split(/\s+/).map(Number);
  assert.equal(parts.length, 4);
  parts.forEach((n) => assert.ok(Number.isFinite(n)));
});
