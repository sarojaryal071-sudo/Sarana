import { useEffect, useState } from "react";

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

export default function Header({
  assistantName,
  connectionState,
  desktopConnected,
  username,
  authMode,
  speechMuted,
  onMenuClick,
}) {
  const now = useClock();
  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const date = now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });

  const connected = connectionState === "connected" || connectionState === "open";
  const reconnecting = connectionState === "reconnecting" || connectionState === "connecting";

  // Phase 8: purely a UI label — the backend, not this string, is the
  // actual source of truth for who's identified in the current session.
  const subLabel =
    authMode === "username" && username
      ? `Welcome, ${username}`
      : authMode === "remote"
        ? "Remote Access"
        : "Web Surface";

  return (
    <header className="app-header">
      <button
        type="button"
        className="hamburger-btn"
        onClick={onMenuClick}
        aria-label="Open menu"
      >
        ☰
      </button>
      <div className="brand">
        <span className="name">{assistantName}</span>
        <span className="sub">{subLabel}</span>
      </div>
      <div className="spacer" />
      <div className={`pill ${connected ? "on" : reconnecting ? "" : "warn"}`}>
        <span className="dot" />
        {connected ? "Connected" : reconnecting ? "Connecting…" : "Disconnected"}
      </div>
      <div className={`pill ${desktopConnected ? "on" : ""}`}>
        <span className="dot" />
        Desktop {desktopConnected ? "Online" : "Offline"}
      </div>
      {/* Production polish (real gap, found via audit): main.py's
          speech_mute tool / speech_mute_changed WS message / this
          reducer's own speechMuted field all worked already — nothing
          ever actually DISPLAYED the state. Only shown while true
          (restrained — an "unmuted" pill has no information value the
          rest of the UI doesn't already convey). Reuses the existing
          .pill.warn class (the same --muted-c token SaranaFace's own
          concerned expression already uses), not a new style. */}
      {speechMuted && (
        <div className="pill warn">
          <span className="dot" />
          Voice Muted
        </div>
      )}
      <div className="clock">
        <div>{time}</div>
        <div className="date">{date}</div>
      </div>
    </header>
  );
}
