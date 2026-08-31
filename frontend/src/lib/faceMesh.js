// src/lib/faceMesh.js — procedural geometry for SARANA's new visual
// identity: a low-poly wireframe face (glowing nodes + connecting edges)
// inside a luminous orb, replacing the Stage-1 "two floating shapes" face
// (see components/SaranaFace.jsx's own header note on that history).
//
// Deliberately NOT a set of hand-authored SVG paths or an image asset —
// every point is generated from simple ellipse/arc math, and every edge
// from a cheap nearest-neighbor pass over those points. This keeps the
// whole face reusable/parameterized (the brief's own requirement) and
// trivially unit-testable as pure data, with no rendering concerns here
// at all — this module never touches the DOM.
//
// Points are grouped (head/browL/browR/eyeL/eyeR/pupilL/pupilR/nose/
// cheekL/cheekR/mouthTop/mouthBottom/jaw) so the component can wrap each
// group in its own SVG <g> and let CSS transform whole groups at once —
// that grouping is what makes the expression system in index.css
// possible (one transform per group, not per point).

function ellipsePoints(cx, cy, rx, ry, count, startDeg, endDeg, group) {
  const pts = [];
  for (let i = 0; i < count; i++) {
    const t = count === 1 ? 0 : i / (count - 1);
    const deg = startDeg + t * (endDeg - startDeg);
    const rad = (deg * Math.PI) / 180;
    pts.push({ x: cx + rx * Math.cos(rad), y: cy + ry * Math.sin(rad), group });
  }
  return pts;
}

// A 200×240 viewBox, face centered around x=100. Proportions (head width
// vs. height, eye spacing, mouth width) are ordinary frontal-portrait
// ratios — simplified and deliberately geometric (per the brief's own
// "clearly artificial rather than photographic" requirement), not traced
// from a photo.
export function buildFacePoints() {
  const pts = [];
  ellipsePoints(100, 122, 54, 90, 22, 0, 337.5, "head").forEach((p) => pts.push(p));
  pts.push({ x: 78, y: 52, group: "head" }, { x: 122, y: 52, group: "head" });
  ellipsePoints(78, 92, 16, 5, 4, 200, 340, "browL").forEach((p) => pts.push(p));
  ellipsePoints(122, 92, 16, 5, 4, 200, 340, "browR").forEach((p) => pts.push(p));
  ellipsePoints(78, 108, 13, 8, 8, 0, 315, "eyeL").forEach((p) => pts.push(p));
  ellipsePoints(122, 108, 13, 8, 8, 0, 315, "eyeR").forEach((p) => pts.push(p));
  pts.push({ x: 78, y: 108, group: "pupilL" });
  pts.push({ x: 122, y: 108, group: "pupilR" });
  pts.push(
    { x: 100, y: 110, group: "nose" },
    { x: 98, y: 128, group: "nose" },
    { x: 100, y: 138, group: "nose" },
    { x: 103, y: 128, group: "nose" }
  );
  pts.push({ x: 58, y: 135, group: "cheekL" }, { x: 142, y: 135, group: "cheekR" });
  pts.push({ x: 54, y: 112, group: "cheekL" }, { x: 146, y: 112, group: "cheekR" });
  ellipsePoints(100, 160, 22, 9, 7, 200, 340, "mouthTop").forEach((p) => pts.push(p));
  ellipsePoints(100, 166, 18, 7, 5, 20, 160, "mouthBottom").forEach((p) => pts.push(p));
  pts.push({ x: 76, y: 190, group: "jaw" }, { x: 100, y: 200, group: "jaw" }, { x: 124, y: 190, group: "jaw" });
  pts.push({ x: 60, y: 170, group: "jaw" }, { x: 140, y: 170, group: "jaw" });
  pts.forEach((p, i) => {
    p.id = i;
  });
  return pts;
}

// Nearest-neighbor mesh edges — NOT a true Delaunay triangulation (would
// need a real geometry library, against the brief's "avoid unnecessary
// dependencies" instruction), just each point connected to its closest
// few neighbors within a distance cutoff. Cheap (O(n²) over ~70 points,
// once, not per-frame) and visually reads as a triangulated wireframe
// mesh without needing one to be mathematically exact.
export function buildFaceEdges(points, maxDist = 30, maxPerNode = 3) {
  const edges = [];
  const seen = new Set();
  points.forEach((a) => {
    const nearest = points
      .filter((b) => b.id !== a.id)
      .map((b) => ({ b, d: Math.hypot(a.x - b.x, a.y - b.y) }))
      .filter((x) => x.d < maxDist)
      .sort((x, y) => x.d - y.d)
      .slice(0, maxPerNode);
    nearest.forEach(({ b }) => {
      const key = a.id < b.id ? `${a.id}-${b.id}` : `${b.id}-${a.id}`;
      if (!seen.has(key)) {
        seen.add(key);
        edges.push([a, b]);
      }
    });
  });
  return edges;
}

// The ordered list of expression-controllable groups — used by the
// component to know which <g> wrappers to create. "head"/"nose"/"jaw"
// are structural (never individually transformed by an expression) but
// still get their own group for consistent same-group-edge handling.
export const FACE_GROUPS = [
  "head", "browL", "browR", "eyeL", "eyeR", "pupilL", "pupilR",
  "nose", "cheekL", "cheekR", "mouthTop", "mouthBottom", "jaw",
];

export const FACE_VIEWBOX = "0 0 200 240";
