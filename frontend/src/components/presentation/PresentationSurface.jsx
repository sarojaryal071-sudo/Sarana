// src/components/presentation/PresentationSurface.jsx — Track 3's ONE
// reusable glass information surface, now the JARVIS Information Surface
// (see App.jsx's own header for how it's positioned as the primary
// visual stage). ContentPanel.jsx is the only mount point (see that
// file) — every structured presentation (weather/calendar/table/
// search_results/generic_information/status) and every ordinary
// plain-text `content` message both render through this same component,
// never a second modal/panel implementation per type.
//
// Lifecycle: HIDDEN is simply "not mounted" (ContentPanel renders
// nothing when `content` is null). While mounted, this component tracks
// its own local phase:
//   materializing -> active -> [updating -> active]* -> dismissing
// `materializing`/`dismissing` are brief, CSS-driven transitions (see
// index.css's own .pw-surface-* rules); `updating` is a short pulse when
// the SAME surface receives new content (a different title/text/
// presentation while already mounted, e.g. a "what about tomorrow?"
// follow-up) — not a remount, not a fresh materialize, just an honest
// "this just changed" cue.
//
// `expanded`/`persistent` are NOT local state — they're lifted to
// AssistantContext (see that file's own PRESENTATION_EXPANDED/
// PRESENTATION_PERSISTENT actions) so BOTH the local expand button AND
// main.py's presentation_control tool (a real voice/text command:
// "expand that", "keep this on screen") control the exact same state —
// one surface, controllable from either input. Compact vs. standard
// sizing is not a separate literal mode: it falls out naturally from
// how much a given renderer chooses to show at `expanded=false` — see
// each renderer's own use of the `expanded` prop for what additional
// data appears only when the user actually asks for more.
import { useEffect, useRef, useState } from "react";
import { resolvePresentationComponent, isValidPresentation } from "./registry";
import { playAudioFx } from "../../lib/audioFx";

const MATERIALIZE_MS = 260;
const UPDATE_PULSE_MS = 420;
const DISMISS_MS = 220;

export default function PresentationSurface({ content, theme, expanded, persistent, onDismiss, onSetExpanded }) {
  const [phase, setPhase] = useState("materializing");
  const [dismissed, setDismissed] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const prevContentRef = useRef(content);
  const timerRef = useRef(null);
  const dismissTimerRef = useRef(null);

  // Production-polish fix (real resource-cleanup gap, found via audit):
  // handleDismiss()'s own setTimeout was previously untracked, so an
  // unmount for any OTHER reason before it fired (e.g. the parent
  // replacing `content` entirely) left it dangling. Tracked and cleared
  // on unmount now, exactly like the other two timers in this component.
  useEffect(() => () => clearTimeout(dismissTimerRef.current), []);

  // HUD chrome clock (visual brief's reference cards each show a live
  // timestamp in the header) — a real ticking clock, not a fabricated
  // data feed, and entirely local to this component; nothing else reads
  // `now`, so it needs no new shared state.
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Fresh mount (ContentPanel only renders this component while
  // `content` is non-null, so a NEW PresentationSurface instance always
  // starts here) -- materialize once, then settle to active. The reveal
  // cue fires ONCE per materialize wave here, not per individual
  // `.pw-reveal-item` inside whatever renderer mounts (see index.css) —
  // a six-row forecast must not fire six sounds.
  useEffect(() => {
    setPhase("materializing");
    playAudioFx("presentation_materializing");
    timerRef.current = setTimeout(() => {
      setPhase("active");
      playAudioFx("presentation_reveal");
    }, MATERIALIZE_MS);
    return () => clearTimeout(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Content changed WHILE this same surface stays mounted (a later
  // weather/calendar/etc. broadcast arriving, e.g. a "what about
  // tomorrow?" follow-up) -- a brief "updating" pulse, never a
  // re-materialize (the surface itself never leaves the DOM for this;
  // see ContentPanel.jsx's own key-less rendering) and never resets
  // expanded/persistent (see AssistantContext.jsx's own CONTENT_MESSAGE
  // case).
  useEffect(() => {
    if (prevContentRef.current === content) return undefined;
    prevContentRef.current = content;
    if (phase === "materializing") return undefined; // already animating in; don't also pulse
    setPhase("updating");
    playAudioFx("presentation_update");
    const t = setTimeout(() => {
      setPhase("active");
      playAudioFx("presentation_reveal");
    }, UPDATE_PULSE_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content]);

  function handleDismiss() {
    setPhase("dismissing");
    setDismissed(true);
    playAudioFx("presentation_dismiss");
    dismissTimerRef.current = setTimeout(onDismiss, DISMISS_MS);
  }

  function handleToggleExpand() {
    playAudioFx(expanded ? "presentation_collapse" : "presentation_expand");
    onSetExpanded(!expanded);
  }

  const presentation = content?.presentation;
  const valid = isValidPresentation(presentation);
  const TypedRenderer = valid ? resolvePresentationComponent(presentation.type) : null;
  const clockLabel = now.toLocaleTimeString(undefined, { hour12: false });

  return (
    <div
      className={`pw-surface pw-surface-${phase} pw-surface-theme-${theme} ${expanded ? "pw-surface-expanded" : ""}`.trim()}
      aria-live="polite"
    >
      {/* HUD targeting-frame corner marks (visual brief's reference
          cards) — four real DOM elements, not pseudo-elements, so they
          work identically whether `.pw-surface` is the web overlay
          (position: absolute) or desktop's fill-mode override (position:
          relative — see index.css). Purely decorative, aria-hidden. */}
      <span className="pw-surface-corner pw-surface-corner-tl" aria-hidden="true" />
      <span className="pw-surface-corner pw-surface-corner-tr" aria-hidden="true" />
      <span className="pw-surface-corner pw-surface-corner-bl" aria-hidden="true" />
      <span className="pw-surface-corner pw-surface-corner-br" aria-hidden="true" />
      <div className="pw-surface-hdr">
        <span className="pw-surface-title">{content.title}</span>
        {persistent && (
          <span className="pw-surface-pin" title="Kept on screen" aria-label="Kept on screen" />
        )}
        <div className="pw-surface-spacer" />
        <span className="pw-surface-clock" aria-hidden="true">{clockLabel}</span>
        <button
          className="pw-surface-btn"
          onClick={handleToggleExpand}
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
        {TypedRenderer ? (
          <TypedRenderer data={presentation.data} expanded={expanded} />
        ) : (
          <div className="pw-surface-plain">{content.text}</div>
        )}
      </div>
      {/* Footer telemetry strip (visual brief's reference cards) — every
          value here is real, already-known internal state (the active
          presentation's own `type`, this surface's own lifecycle
          `phase`) — never an invented number, matching this project's
          "no fabricated data" rule everywhere else. */}
      <div className="pw-surface-footer" aria-hidden="true">
        JARVIS SURFACE // {(presentation?.type || "text").toUpperCase()} // {phase.toUpperCase()}
      </div>
    </div>
  );
}
