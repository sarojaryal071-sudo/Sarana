// src/components/IdentityTransition.jsx — the SARANA<->JARVIS switch used
// to be a brief 260ms blur/opacity crossfade (see App.jsx's own git
// history) — a real request directly asked for something with more
// weight: "not a plain cinematic transition... like in an AI
// technological movie where the current UI goes through a fast rebuild
// of another UI with moving neons and a cinematic building process."
//
// This is a purely decorative OVERLAY, absolutely positioned on top of
// whichever of Orb.jsx/SaranaFace.jsx is mid-crossfade underneath it (see
// App.jsx's own .identity-stage wrapper, still doing the base opacity/
// blur/scale work) — it never touches either of those components, never
// reads their state, and unmounts completely once the transition ends
// (App.jsx only renders this while `identityPhase` is non-null). JARVIS's
// Orb.jsx and SARANA's SaranaFace.jsx are exactly as untouched by this as
// by every earlier phase of this project's own "keep JARVIS orb intact"
// requirement.
//
// Built entirely from SVG line-draw/stroke animations + CSS keyframes —
// no canvas, no WebGL, no particle-physics library, no new dependency.
// The "movie AI HUD constructing itself" look comes from three cheap,
// well-known techniques layered together:
//   1. A flickering grid (glitch-style opacity steps()) during the
//      DECONSTRUCT phase — the outgoing identity's HUD breaking apart.
//   2. Two concentric rings + eight reticle spokes that DRAW themselves
//      via stroke-dashoffset (the classic "SVG line draw" trick) during
//      the REBUILD phase — the incoming identity's HUD assembling.
//   3. A small fixed ring of particles that burst outward on deconstruct
//      and converge back inward on rebuild, plus two sweeping scanline
//      bars and one brief center flash at the handoff moment.
// All geometry below is plain computed data (pure trig, no randomness),
// exactly the same "compute once, render as data" convention
// lib/faceMesh.js already established for SaranaFace's own geometry.
const RING_R1 = 62;
const RING_R2 = 78;
const SPOKE_COUNT = 8;
const SPOKES = Array.from({ length: SPOKE_COUNT }, (_, i) => {
  const angle = (i / SPOKE_COUNT) * 2 * Math.PI;
  const inner = RING_R2 + 4;
  const outer = RING_R2 + 14;
  return {
    x1: 100 + inner * Math.cos(angle), y1: 100 + inner * Math.sin(angle),
    x2: 100 + outer * Math.cos(angle), y2: 100 + outer * Math.sin(angle),
  };
});

const PARTICLE_COUNT = 10;
const PARTICLE_R = 92;
const PARTICLES = Array.from({ length: PARTICLE_COUNT }, (_, i) => {
  const angle = (i / PARTICLE_COUNT) * 2 * Math.PI;
  return { cx: 100 + PARTICLE_R * Math.cos(angle), cy: 100 + PARTICLE_R * Math.sin(angle) };
});

const GRID_STEP = 25;
const GRID_LINES = [];
for (let x = GRID_STEP; x < 200; x += GRID_STEP) GRID_LINES.push({ x1: x, y1: 0, x2: x, y2: 200 });
for (let y = GRID_STEP; y < 200; y += GRID_STEP) GRID_LINES.push({ x1: 0, y1: y, x2: 200, y2: y });

export default function IdentityTransition({ phase, targetIdentity }) {
  return (
    <div
      className={`identity-transition identity-transition-${phase} identity-transition-${targetIdentity}`}
      aria-hidden="true"
    >
      <div className="it-scanline it-scanline-a" />
      <div className="it-scanline it-scanline-b" />
      <div className="it-flash" />
      <svg className="it-svg" viewBox="0 0 200 200">
        <g className="it-grid">
          {GRID_LINES.map((l, i) => (
            <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} />
          ))}
        </g>
        <g className="it-particles">
          {PARTICLES.map((p, i) => (
            <circle key={i} cx={p.cx} cy={p.cy} r="1.6" />
          ))}
        </g>
        <g className="it-frame">
          <circle className="it-ring it-ring-1" cx="100" cy="100" r={RING_R1} />
          <circle className="it-ring it-ring-2" cx="100" cy="100" r={RING_R2} />
          {SPOKES.map((s, i) => (
            <line key={i} className="it-spoke" x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} />
          ))}
        </g>
      </svg>
    </div>
  );
}
