// src/components/SaranaFace.jsx — SARANA's normal-mode visual identity: a
// minimal, expressive AI face (two eyes + a mouth) replacing the animated
// technical Orb in that role (see core/prompt.txt "Master Task — Replace
// the SARANA Orb with a Minimal Expressive AI Face").
//
// Renders into the exact same "orb-stage" slot Orb.jsx and VisionStage.jsx
// already occupy (see App.jsx, which mounts exactly one of the three at a
// time) — the visual centerpiece, never a second/separate element.
//
// JARVIS-mode compatibility (spec section 13): Orb.jsx is deliberately
// left completely untouched and still exists as its own component — it is
// simply no longer the thing App.jsx renders in the normal-mode branch of
// its orb-stage conditional. The intended future shape is:
//   orb-stage slot -> SARANA mode: SaranaFace | JARVIS mode: Orb | vision: VisionStage
// Stage 1 does not add the mode switch itself, only preserves the
// architecture for it.
//
// Same prop shape as Orb ({ status, assistantName }) so either can occupy
// the slot interchangeably. `assistantName` is accepted for that
// interchangeability but deliberately not rendered on the face itself
// (Header.jsx already shows it) — kept minimal, per the spec's own design
// requirement.
//
// Expression mapping lives in lib/faceExpressions.js (a pure, separately
// unit-tested function) — this component only turns "what expression" into
// "what shows on screen"; it never invents its own status logic.
//
// Purely presentational: aria-hidden, no interactive elements. The one
// pre-existing accessibility/status requirement (spec section 16 — the
// face must never be the ONLY indication of state) is met by the small
// text label below it, which also gets a role="status"/aria-live region a
// screen reader can announce — something the canvas-only Orb never had.
import { useEffect, useRef, useState } from "react";
import { mapStatusToExpression } from "../lib/faceExpressions";

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

// Natural, non-mechanical blinking (spec section 11): a randomized delay
// between blinks rather than a fixed-period CSS `infinite` loop, so it
// never reads as a metronome. Cheap either way (one pending timer, not a
// per-frame loop — spec section 17), but the hidden-tab check below also
// means a backgrounded tab doesn't bother flipping the class at all.
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
      <div
        className={`sarana-face${blinking ? " sarana-face-blink" : ""}`}
        data-expression={expression}
        aria-hidden="true"
      >
        <div className="face-eyes">
          <span className="face-eye face-eye-left" />
          <span className="face-eye face-eye-right" />
        </div>
        <span className="face-mouth" />
      </div>
      <div className="sarana-face-status" role="status" aria-live="polite">
        {label}
      </div>
      {/* assistantName intentionally unused visually here — see header note */}
    </div>
  );
}
