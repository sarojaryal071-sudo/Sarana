// src/components/SaranaFace.jsx — SARANA's visual identity: a simple,
// bold, filled-shape face (two round glowing eyes + highlight, two thick
// brow strokes, one mouth curve, two soft cheek glows) inside the orb's
// own ambient glow — a direct match to the user's own reference image,
// recolored into SARANA's palette (see lib/faceMesh.js's own header for
// the full reasoning). No outer head-ring, no dense mesh, no scanline/
// spark decoration — this generation is a deliberate SIMPLIFICATION from
// the previous one, not an addition to it (git history has every earlier
// generation). JARVIS's Orb.jsx is untouched either way, same as always.
//
// Renders into the exact same "orb-stage" slot Orb.jsx and VisionStage.jsx
// already occupy (see App.jsx, which mounts exactly one of the three at a
// time, crossfading between them — see App.jsx's own transition wrapper).
//
// The geometry (path/shape data for each facial feature) is pure, static
// data from lib/faceMesh.js — hand-authored, not procedural math or a
// mesh extraction. This component's only job is turning that fixed
// geometry + the current expression into SVG markup and letting
// index.css's [data-expression="..."] rules move whole <g> groups around
// with CSS transforms — no per-frame JS animation loop anywhere in this
// file (the mouth's real-audio drive is an event-driven subscription, see
// below, not a polling loop).
//
// Expression logic lives in lib/faceExpressions.js (pure, separately
// unit-tested functions) — this component only turns "what expression"
// into "what shows on screen"; it never invents its own status logic.
// `expressionOverride` (optional, {expression, until} | null/undefined)
// is App.jsx's mirror of main.py's set_expression tool — see that
// module's resolveExpression() for the actual priority rules (mechanical
// speaking/thinking/muted always win over a requested mood).
//
// Purely presentational: aria-hidden on the visual face itself, no
// interactive elements. State is still announced via the same
// role="status"/aria-live text label every earlier generation had — the
// face must never be the ONLY indication of state.
import { useEffect, useRef, useState } from "react";
import { resolveExpression } from "../lib/faceExpressions";
import { FACE_GROUPS, FACE_VIEWBOX, EYE_PATHS, IRIS, BROW_PATHS, MOUTH_PATH, CHEEKS } from "../lib/faceMesh";
import { subscribeMouthLevel } from "../lib/mouthLevel";

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

export default function SaranaFace({ status, assistantName, expressionOverride }) {
  // resolveExpression() itself is a pure function of (status, override,
  // now) — see lib/faceExpressions.js's own header — Date.now() is only
  // ever read HERE, at the one call site that actually needs the real
  // clock; App.jsx's own expiry effect guarantees a re-render happens
  // again the moment an active override's `until` passes, so this stays
  // correct without a per-frame timer of its own.
  const expression = resolveExpression(status, expressionOverride, Date.now());
  const [blinking, setBlinking] = useState(false);
  const timerRef = useRef(null);
  const faceRef = useRef(null);

  // Real playback-amplitude mouth: see lib/mouthLevel.js's own header note
  // on why this is a subscription, not a per-frame polling loop — this
  // component stays a pure, event-driven renderer. Writes the DOM
  // property directly (never React state) so a chunk arriving mid-speech
  // doesn't force a full component re-render.
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
      <svg
        ref={faceRef}
        className={`sarana-face-mesh${blinking ? " sarana-face-blink" : ""}`}
        viewBox={FACE_VIEWBOX}
        data-expression={expression}
        aria-hidden="true"
      >
        {/* Cheeks render FIRST (behind everything else) so the glow sits
            under the eyes/mouth, not on top of them — same reasoning as
            painting a blush before the rest of a face. */}
        {FACE_GROUPS.map((group) => (
          <g key={group} className="mesh-group" data-group={group}>
            {group === "cheekL" && <circle className="face-cheek" cx={CHEEKS.cheekL.cx} cy={CHEEKS.cheekL.cy} r={CHEEKS.cheekL.r} />}
            {group === "cheekR" && <circle className="face-cheek" cx={CHEEKS.cheekR.cx} cy={CHEEKS.cheekR.cy} r={CHEEKS.cheekR.r} />}
            {group === "browL" && <path d={BROW_PATHS.browL} className="face-brow" />}
            {group === "browR" && <path d={BROW_PATHS.browR} className="face-brow" />}
            {group === "eyeL" && <path d={EYE_PATHS.eyeL} className="face-eye" />}
            {group === "eyeR" && <path d={EYE_PATHS.eyeR} className="face-eye" />}
            {group === "mouth" && <path d={MOUTH_PATH} className="face-mouth" />}
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
