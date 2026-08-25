// src/components/Controls.jsx — only surfaces controls the EXISTING protocol
// actually supports remotely: text command and mic streaming
// (/ws/phone-audio). No WAKE button — as of Phase 9, logging in (POST
// /login/username) is itself the start signal (see dashboard/server.py's
// /login/username route), so there is nothing left for a WAKE button to do
// in the normal flow. The desktop's interrupt/mute buttons call local
// JarvisLive callbacks (on_interrupt, muted) that have no WebSocket/REST
// equivalent today — inventing one would mean wiring new dashboard->
// JarvisLive control paths, out of scope here (see Phase 6 report,
// "problems discovered"). Better to omit than to ship a button that
// silently does nothing.
import { useState } from "react";

const MIC_LABELS = {
  idle: "🎤 MIC",
  requesting: "🎤 REQUESTING…",
  streaming: "🎤 LISTENING (TAP TO STOP)",
  denied: "🎤 MIC DENIED",
  unsupported: "🎤 UNSUPPORTED",
  error: "🎤 MIC ERROR",
};

export default function Controls({
  onSend,
  micState,
  onToggleMic,
  disabled,
  assistantStatus,
  onInterrupt,
}) {
  const [text, setText] = useState("");

  function submit(e) {
    e.preventDefault();
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
  }

  // Item 2: only shown while SARANA is actually speaking — hidden/inactive
  // otherwise, per spec. Driven by the same assistantStatus the Orb itself
  // renders from (AssistantContext's AUDIO_ACTIVITY/AUDIO_IDLE_TIMEOUT),
  // not a separate control system.
  const speaking = assistantStatus === "SPEAKING";

  return (
    <div className="controls">
      <form className="text-input-row" onSubmit={submit}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type a command…"
          disabled={disabled}
        />
        <button className="btn" type="submit" disabled={disabled || !text.trim()}>
          ▸
        </button>
      </form>
      <div className="row">
        <button
          className={`btn primary ${micState === "streaming" ? "active" : ""}`}
          onClick={onToggleMic}
          disabled={disabled || micState === "requesting"}
          title={
            micState === "denied"
              ? "Microphone permission denied — allow it in your browser's site settings"
              : micState === "unsupported"
                ? "This browser does not support microphone capture"
                : undefined
          }
        >
          {MIC_LABELS[micState] || MIC_LABELS.idle}
        </button>
        {speaking && (
          <button
            className="btn danger interrupt-btn"
            onClick={onInterrupt}
            disabled={disabled}
            title="Stop SARANA and return to listening"
          >
            ✋ INTERRUPT
          </button>
        )}
      </div>
    </div>
  );
}
