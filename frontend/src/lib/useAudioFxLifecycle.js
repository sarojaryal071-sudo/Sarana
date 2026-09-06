// src/lib/useAudioFxLifecycle.js — extracted from App.jsx (Track 4) so the
// desktop-embedded Presentation Engine entry (desktop-presentation-main.jsx)
// can react to the SAME authoritative assistantStatus/theme state the
// browser already does, through the literal same code, instead of each
// surface re-deriving wake/sleep/listening/thinking cues and ducking
// independently. Both surfaces receive assistantStatus from the same
// ultimate origin (main.py's _push_state()) — the browser over /ws's
// "status" message, desktop over the local bridge that mirrors it — so
// this hook is the one place that turns "assistantStatus just changed"
// into a cue, for either.
import { useEffect, useRef } from "react";
import { playAudioFx, setAudioFxTheme, setSpeaking as setAudioFxSpeaking } from "./audioFx.js";

/**
 * @param {string} assistantStatus - "SLEEPING" | "LISTENING" | "THINKING" | "SPEAKING"
 * @param {"jarvis"|"sarana"} identity - which cue set (SaranaFace/Orb) is currently shown
 */
export function useAudioFxLifecycle(assistantStatus, identity) {
  useEffect(() => { setAudioFxTheme(identity); }, [identity]);

  const prevStatusRef = useRef(assistantStatus);
  useEffect(() => {
    const prev = prevStatusRef.current;
    const next = assistantStatus;
    prevStatusRef.current = next;
    setAudioFxSpeaking(next === "SPEAKING");
    if (prev === next) return;
    if (next === "SLEEPING") playAudioFx("assistant_sleep");
    else if (prev === "SLEEPING") playAudioFx("assistant_wake");
    else if (next === "LISTENING") playAudioFx("assistant_listening");
    else if (next === "THINKING") playAudioFx("assistant_thinking");
  }, [assistantStatus]);
}
