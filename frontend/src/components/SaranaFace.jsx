// src/components/SaranaFace.jsx — SARANA's visual identity: a minimal
// "eyes + brows + mouth only" glowing outline face inside a circular
// frame (per the task's reference image) — not a dense wireframe mesh.
// Replaces the earlier low-poly wireframe-mesh version this file used to
// render (git history has that generation) — JARVIS's Orb.jsx is
// untouched either way.
//
// Renders into the exact same "orb-stage" slot Orb.jsx and VisionStage.jsx
// already occupy (see App.jsx, which mounts exactly one of the three at a
// time, crossfading between them — see App.jsx's own transition wrapper).
//
// The geometry (path data for each facial feature) is pure, static data
// from lib/faceMesh.js — hand-authored SVG paths, not procedural math or
// a mesh extraction (see that module's own header note on why: there is
// no mesh in this design, just a handful of glowing curves). This
// component's only job is turning that fixed geometry + the current
// expression into SVG markup and letting index.css's
// [data-expression="..."] rules move whole <g> groups around with CSS
// transforms — no per-frame JS animation loop anywhere in this file.
//
// Expression mapping lives in lib/faceExpressions.js (a pure, separately
// unit-tested function) — this component only turns "what expression" into
// "what shows on screen"; it never invents its own status logic. The
// vocabulary is wider than what real app state can honestly reach (see
// that module's own comment) — this component doesn't know or care which
// expressions are "reachable", it just renders whatever
// mapStatusToExpression() returns.
//
// Purely presentational: aria-hidden on the visual face itself, no
// interactive elements. State is still announced via the same
// role="status"/aria-live text label the earlier generations had — the
// face must never be the ONLY indication of state.
import { useEffect, useRef, useState } from "react";
import { mapStatusToExpression } from "../lib/faceExpressions";
import { FACE_GROUPS, FACE_VIEWBOX, HEAD_CIRCLE, EYE_PATHS, IRIS, BROW_PATHS, MOUTH_PATH } from "../lib/faceMesh";
import { subscribeMouthLevel } from "../lib/mouthLevel";

// A handful of fixed positions for the ambient sparks near the circle rim
// (restrained particles, matching the reference's own couple of small
// accent dots) — plain SVG circles + CSS keyframes, not a particle
// system. Deliberately NOT random-per-render — fixed, computed once, same
// reasoning as the geometry constants themselves.
const SPARKS = [
  { cx: 40, cy: 60 },
  { cx: 172, cy: 80 },
  { cx: 28, cy: 160 },
  { cx: 178, cy: 150 },
  { cx: 100, cy: 24 },
];

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

// Natural, non-mechanical blinking: a randomized delay between blinks
// rather than a fixed-period CSS `infinite` loop, so it never reads as a
// metronome. Cheap either way (one pending timer, not a per-frame loop),
// and the hidden-tab check means a backgrounded tab doesn't bother
// flipping the class at all.
const BLINK_MIN_MS = 2400;
const BLINK_MAX_MS = 5600;
const BLINK_DURATION_MS = 160;

export default function SaranaFace({ status, assistantName }) {
  const expression = mapStatusToExpression(status);
  const [blinking, setBlinking] = useState(false);
  const timerRef = useRef(null);
  const faceRef = useRef(null);

  // Real playback-amplitude mouth: see lib/mouthLevel.js's own header
  // note on why this is a subscription, not a per-frame polling loop —
  // SaranaFace stays a pure, event-driven renderer,
  // CSS still carries the actual motion via the --mouth-open custom
  // property (see index.css's speaking-expression mouth rule). Writes
  // the DOM property directly (never React state) so a chunk arriving
  // mid-speech doesn't force a full component re-render.
  useEffect(() => {
    return subscribeMouthLevel((level) => {
      faceRef.current?.style.setProperty("--mouth-open", String(level));
    });
  }, []);

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
      <div className="sarana-face-scanline" aria-hidden="true" />
      <svg
        ref={faceRef}
        className={`sarana-face-mesh${blinking ? " sarana-face-blink" : ""}`}
        viewBox={FACE_VIEWBOX}
        data-expression={expression}
        aria-hidden="true"
      >
        <g className="mesh-rings">
          <circle className="face-outline" cx={HEAD_CIRCLE.cx} cy={HEAD_CIRCLE.cy} r={HEAD_CIRCLE.r} />
        </g>
        <g className="mesh-sparks">
          {SPARKS.map((s, i) => (
            <circle key={i} className="mesh-spark" cx={s.cx} cy={s.cy} r="1" />
          ))}
        </g>
        {FACE_GROUPS.map((group) => (
          <g key={group} className="mesh-group" data-group={group}>
            {group === "browL" && <path d={BROW_PATHS.browL} className="face-line" />}
            {group === "browR" && <path d={BROW_PATHS.browR} className="face-line" />}
            {group === "eyeL" && <path d={EYE_PATHS.eyeL} className="face-line" />}
            {group === "eyeR" && <path d={EYE_PATHS.eyeR} className="face-line" />}
            {group === "mouth" && <path d={MOUTH_PATH} className="face-line" />}
            {(group === "pupilL" || group === "pupilR") && (
              <>
                <circle
                  className="face-iris"
                  cx={IRIS[group].irisCx}
                  cy={IRIS[group].irisCy}
                  r={IRIS[group].irisR}
                />
                <circle
                  className="face-highlight"
                  cx={IRIS[group].hlCx}
                  cy={IRIS[group].hlCy}
                  r={IRIS[group].hlR}
                />
              </>
            )}
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
