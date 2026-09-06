// src/components/presentation/PresentationSurface.jsx — Track 3's ONE
// reusable glass information surface. ContentPanel.jsx is the only
// mount point (see that file) — every structured presentation (weather/
// calendar/table/search_results/generic_information/status) and every
// ordinary plain-text `content` message both render through this same
// component, never a second modal/panel implementation per type
// (section 4's own explicit requirement).
//
// Lifecycle (section 7): HIDDEN is simply "not mounted" (ContentPanel
// renders nothing when `content` is null — no separate hidden DOM to
// manage). While mounted, this component tracks its own local phase:
//   materializing -> active -> [updating -> active]* -> dismissing
// `materializing`/`dismissing` are brief, CSS-driven transitions (see
// index.css's own .pw-surface-* rules); `updating` is a short pulse
// when the SAME surface receives new content (a different title/text/
// presentation while already mounted) — not a remount, not a fresh
// materialize, just an honest "this just changed" cue. Expand/collapse
// is a simple local toggle (`expanded`), not a phase — it doesn't affect
// whether data is fresh, only how much of it is currently shown.
import { useEffect, useRef, useState } from "react";
import { resolvePresentationComponent, isValidPresentation } from "./registry";

const MATERIALIZE_MS = 260;
const UPDATE_PULSE_MS = 420;
const DISMISS_MS = 200;

export default function PresentationSurface({ content, theme, onDismiss }) {
  const [phase, setPhase] = useState("materializing");
  const [expanded, setExpanded] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const prevContentRef = useRef(content);
  const timerRef = useRef(null);

  // Fresh mount (ContentPanel only renders this component while
  // `content` is non-null, so a NEW PresentationSurface instance always
  // starts here) -- materialize once, then settle to active.
  useEffect(() => {
    setPhase("materializing");
    timerRef.current = setTimeout(() => setPhase("active"), MATERIALIZE_MS);
    return () => clearTimeout(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Content changed WHILE this same surface stays mounted (a later
  // weather/calendar/etc. broadcast arriving) -- a brief "updating"
  // pulse, never a re-materialize (the surface itself never leaves the
  // DOM for this; see ContentPanel.jsx's own key-less rendering).
  useEffect(() => {
    if (prevContentRef.current === content) return undefined;
    prevContentRef.current = content;
    if (phase === "materializing") return undefined; // already animating in; don't also pulse
    setPhase("updating");
    const t = setTimeout(() => setPhase("active"), UPDATE_PULSE_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content]);

  function handleDismiss() {
    setPhase("dismissing");
    setDismissed(true);
    setTimeout(onDismiss, DISMISS_MS);
  }

  const presentation = content?.presentation;
  const valid = isValidPresentation(presentation);
  const TypedRenderer = valid ? resolvePresentationComponent(presentation.type) : null;

  return (
    <div
      className={`pw-surface pw-surface-${phase} pw-surface-theme-${theme} ${expanded ? "pw-surface-expanded" : ""}`.trim()}
      aria-live="polite"
    >
      <div className="pw-surface-hdr">
        <span className="pw-surface-title">{content.title}</span>
        <div className="pw-surface-spacer" />
        <button
          className="pw-surface-btn"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? "▾" : "▸"}
        </button>
        <button className="pw-surface-btn" onClick={handleDismiss} disabled={dismissed} aria-label="Dismiss">
          ✕
        </button>
      </div>
      <div className="pw-surface-body">
        {TypedRenderer ? <TypedRenderer data={presentation.data} /> : <div className="pw-surface-plain">{content.text}</div>}
      </div>
    </div>
  );
}
