// src/components/SaranaFace.jsx — SARANA's visual identity: a low-poly
// wireframe digital face (glowing nodes + connecting edges) inside a
// luminous orb — "Human-Orb UI" (see the project's own design brief for
// that name). Replaces the Stage-1 "two floating shapes" face this file
// used to render (git history has that version) — this is the second
// generation, not the first; JARVIS's Orb.jsx is untouched either way.
//
// Renders into the exact same "orb-stage" slot Orb.jsx and VisionStage.jsx
// already occupy (see App.jsx, which mounts exactly one of the three at a
// time, now crossfading between them rather than hard-swapping — see
// App.jsx's own transition wrapper).
//
// The geometry (which points exist, how they connect) is pure, testable
// data from lib/faceMesh.js — computed ONCE at module load (deterministic,
// no randomness — see that module), never per-render. This component's
// only job is turning that fixed geometry + the current expression into
// SVG markup and letting index.css's [data-expression="..."] rules move
// whole <g> groups around with CSS transforms — no per-frame JS animation
// loop anywhere in this file.
//
// Expression mapping lives in lib/faceExpressions.js (a pure, separately
// unit-tested function) — this component only turns "what expression" into
// "what shows on screen"; it never invents its own status logic. The
// vocabulary is wider than what real app state can honestly reach (see
// that module's own comment) — this component doesn't know or care which
// expressions are "reachable", it just renders whatever
// mapStatusToExpression() returns.
//
// Purely presentational: aria-hidden on the visual mesh itself, no
// interactive elements. State is still announced via the same
// role="status"/aria-live text label the Stage-1 face already had — the
// mesh must never be the ONLY indication of state.
import { useEffect, useRef, useState } from "react";
import { mapStatusToExpression } from "../lib/faceExpressions";
import { buildFacePoints, buildFaceEdges, FACE_GROUPS, FACE_VIEWBOX } from "../lib/faceMesh";

// Mirrors Orb.jsx's own status-label glyphs/wording exactly (see its
// header comment on matching ui.py's HudCanvas precedence) — same
// information, just rendered as real DOM text instead of canvas pixels.
const STATUS_LABELS = {
  MUTED: "⊘  MUTED",
  SPEAKING: "●  SPEAKING",
  THINKING: "◈  THINKING",
  SLEEPING: "○  SLEEPING",
  LISTENING: "●  LISTENING",
};

// Geometry is fixed/deterministic (lib/faceMesh.js is a pure function of
// no arguments) — computed once here, shared by every render and every
// instance, never recomputed. Cross-group edges are kept separate from
// same-group edges so a CSS transform on one facial group (e.g. a brow
// lifting) never has to guess how to also move a line whose OTHER end
// belongs to a different group — those connecting lines stay put,
// reading as the mesh's connective tissue flexing slightly, which is the
// intended effect, not a bug (see index.css's own note on this).
const POINTS = buildFacePoints();
const EDGES = buildFaceEdges(POINTS);
const CROSS_GROUP_EDGES = EDGES.filter(([a, b]) => a.group !== b.group);
const SAME_GROUP_EDGES = Object.fromEntries(
  FACE_GROUPS.map((group) => [group, EDGES.filter(([a, b]) => a.group === group && b.group === group)])
);
const POINTS_BY_GROUP = Object.fromEntries(
  FACE_GROUPS.map((group) => [group, POINTS.filter((p) => p.group === group)])
);

// Natural, non-mechanical blinking (unchanged from Stage 1): a randomized
// delay between blinks rather than a fixed-period CSS `infinite` loop, so
// it never reads as a metronome. Cheap either way (one pending timer, not
// a per-frame loop), and the hidden-tab check means a backgrounded tab
// doesn't bother flipping the class at all.
const BLINK_MIN_MS = 2400;
const BLINK_MAX_MS = 5600;
const BLINK_DURATION_MS = 160;

export default function SaranaFace({ status, assistantName }) {
  const expression = mapStatusToExpression(status);
  const [blinking, setBlinking] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    function scheduleNextBlink() {
      const delay = BLINK_MIN_MS + Math.random() * (BLINK_MAX_MS - BLINK_MIN_MS);
      timerRef.current = setTimeout(() => {
        if (cancelled) return;
        if (document.hidden) {
          // Not visible right now — no point animating a blink nobody
          // sees; just check again later instead of stacking up timers.
          scheduleNextBlink();
          return;
        }
        setBlinking(true);
        timerRef.current = setTimeout(() => {
          if (cancelled) return;
          setBlinking(false);
          scheduleNextBlink();
        }, BLINK_DURATION_MS);
      }, delay);
    }

    scheduleNextBlink();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
    };
  }, []);

  const label = STATUS_LABELS[status] || STATUS_LABELS.SLEEPING;

  return (
    <div className="orb-stage">
      <div className="sarana-face-glow" aria-hidden="true" />
      <svg
        className={`sarana-face-mesh${blinking ? " sarana-face-blink" : ""}`}
        viewBox={FACE_VIEWBOX}
        data-expression={expression}
        aria-hidden="true"
      >
        <g className="mesh-rings">
          <circle className="mesh-ring" cx="100" cy="122" r="96" />
          <circle className="mesh-ring" cx="100" cy="122" r="105" />
          <circle className="mesh-ring" cx="100" cy="122" r="114" />
        </g>
        <g className="mesh-cross-edges">
          {CROSS_GROUP_EDGES.map(([a, b], i) => (
            <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="mesh-edge" />
          ))}
        </g>
        {FACE_GROUPS.map((group) => (
          <g key={group} className="mesh-group" data-group={group}>
            {SAME_GROUP_EDGES[group].map(([a, b], i) => (
              <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="mesh-edge" />
            ))}
            {POINTS_BY_GROUP[group].map((p) => (
              <circle
                key={p.id}
                cx={p.x}
                cy={p.y}
                r={group.startsWith("pupil") ? 2.4 : 1.5}
                className="mesh-node"
              />
            ))}
          </g>
        ))}
      </svg>
      <div className="sarana-face-status" role="status" aria-live="polite">
        {label}
      </div>
      {/* assistantName intentionally unused visually here — Header.jsx already shows it */}
    </div>
  );
}
