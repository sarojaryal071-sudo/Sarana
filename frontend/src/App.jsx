import { useCallback, useEffect, useRef, useState } from "react";
import { useAssistantDispatch, useAssistantState } from "./state/AssistantContext";
import { fetchSession, sendCommand, sendInterrupt, ApiError } from "./lib/api";
import { JarvisSocket } from "./lib/websocket";
import { AudioOutPlayer } from "./lib/audioOut";
import { MicStreamer } from "./lib/mic";
import LoginScreen, { readStoredSession, clearStoredToken } from "./components/LoginScreen";
import Header from "./components/Header";
import Orb from "./components/Orb";
import SidePanel from "./components/SidePanel";
import ContentPanel from "./components/ContentPanel";
import Controls from "./components/Controls";
import ConnectionBanner from "./components/ConnectionBanner";
import ToolsRail from "./components/ToolsRail";

const SESSION_RETRY_MS = 4000;

export default function App() {
  const state = useAssistantState();
  const dispatch = useAssistantDispatch();

  const [sessionError, setSessionError] = useState(null);
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const socketRef = useRef(null);
  const audioRef = useRef(null);
  const micRef = useRef(null);
  const audioIdleTimer = useRef(null);
  const micAutoStartedRef = useRef(false); // item 1: auto-start mic once per login, not per utterance

  // ── GET /api/session — unauthenticated, works before any login ─────────
  const loadSession = useCallback(async () => {
    try {
      const s = await fetchSession();
      dispatch({
        type: "SESSION_LOADED",
        assistantName: s.assistant_name,
        tools: s.tools || [],
        desktopConnected: !!s.desktop_connected,
      });
      setSessionError(null);
      setSessionLoaded(true);
      return true;
    } catch (e) {
      setSessionError(
        e instanceof ApiError ? e.message : "Cannot reach the SARANA backend"
      );
      return false;
    }
  }, [dispatch]);

  useEffect(() => {
    let cancelled = false;
    let timer;
    async function attempt() {
      const ok = await loadSession();
      if (!cancelled && !ok) timer = setTimeout(attempt, SESSION_RETRY_MS);
    }
    attempt();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [loadSession]);

  // Try a previously-stored token on first load, same-tab convenience only
  // (sessionStorage — see LoginScreen.jsx). A bad/expired token just falls
  // back to the login screen once /ws rejects it.
  useEffect(() => {
    const session = readStoredSession();
    if (session?.token) {
      dispatch({
        type: "AUTH_STATE",
        value: "authenticated",
        token: session.token,
        authMode: session.authMode,
        username: session.username,
      });
    }
  }, [dispatch]);

  // ── wire up /ws + /ws/audio-out once authenticated ──────────────────────
  useEffect(() => {
    if (state.authenticationState !== "authenticated" || !state.token) return;

    const socket = new JarvisSocket(state.token, {
      onState: (s) => {
        const mapped = s === "open" ? "connected" : s === "connecting" ? "connecting" : "reconnecting";
        dispatch({ type: "CONNECTION_STATE", value: mapped });
      },
      onAuthFailure: () => {
        clearStoredToken();
        dispatch({ type: "AUTH_STATE", value: "unauthenticated", error: "Session expired — please pair again." });
      },
      onMessage: (msg) => {
        switch (msg.type) {
          case "log":
            dispatch({ type: "LOG_MESSAGE", speaker: msg.speaker, text: msg.text, ts: msg.ts });
            break;
          case "sys":
            dispatch({ type: "SYS_MESSAGE", text: msg.text, ts: msg.ts });
            break;
          case "status":
            dispatch({ type: "STATUS_MESSAGE", state: msg.state });
            break;
          case "content":
            dispatch({ type: "CONTENT_MESSAGE", title: msg.title, text: msg.text });
            break;
          case "file_received":
            dispatch({ type: "SYS_MESSAGE", text: `File received: ${msg.name}`, ts: null });
            break;
          case "device_action":
            // Reserved for Phase 6's desktop-agent dispatch — nothing sends
            // this yet (see dashboard/server.py). Logged, not acted on.
            console.info("[Sarana] device_action received (not yet actionable):", msg);
            break;
          default:
            break; // unknown type — never crash, matches backend's own tolerance
        }
      },
    });
    socket.connect();
    socketRef.current = socket;

    // Infer SPEAKING from actual audio-out activity — the backend doesn't
    // broadcast granular THINKING/LISTENING/SPEAKING transitions today
    // (see AssistantContext.jsx and the Phase 6 report's "problems
    // discovered" section), so real playback is the only live signal we have.
    const audio = new AudioOutPlayer(
      state.token,
      (s) => dispatch({ type: "AUDIO_STATE", value: s }),
      () => {
        dispatch({ type: "AUDIO_ACTIVITY" });
        clearTimeout(audioIdleTimer.current);
        audioIdleTimer.current = setTimeout(() => dispatch({ type: "AUDIO_IDLE_TIMEOUT" }), 900);
      }
    );
    audio.connect();
    audioRef.current = audio;

    return () => {
      socket.close();
      audio.close();
      clearTimeout(audioIdleTimer.current);
      socketRef.current = null;
      audioRef.current = null;
      // Session is ending (logout / token change / auth failure) — the mic
      // shouldn't keep streaming against a dead token, and the next login
      // gets its own fresh auto-start (see the effect below).
      micRef.current?.stop();
      micRef.current = null;
      micAutoStartedRef.current = false;
    };
  }, [state.authenticationState, state.token, dispatch]);

  // Item 1: once the WS is actually open, start the mic automatically —
  // reuses handleToggleMic()/MicStreamer unchanged, just calls it for the
  // user instead of waiting for a click. This still runs inside the async
  // continuation of the login button's own click (well within every
  // browser's transient-activation window for getUserMedia), so the one
  // permission prompt this can trigger is the same "first browser
  // permission request" the spec accepts. If the browser denies/blocks it
  // anyway, MicStreamer already reports "denied"/"unsupported" and
  // Controls.jsx surfaces that honestly — no fake workaround. Runs once per
  // login (guarded by the ref, reset on logout above); a manual mic stop
  // afterward is respected — this never force-restarts it.
  useEffect(() => {
    if (state.authenticationState !== "authenticated") return;
    if (state.connectionState !== "connected") return;
    if (micAutoStartedRef.current) return;
    micAutoStartedRef.current = true;
    handleToggleMic();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.authenticationState, state.connectionState]);

  function handleAuthenticated(session) {
    // Item 8: activity log = current session's UI history, not persistent
    // memory (that's the backend's memory system, untouched here) — a new
    // login starts a fresh log instead of appending to whatever a previous
    // login in this tab already accumulated. RESET_FOR_LOGOUT already
    // existed in the reducer; this just wires it in.
    dispatch({ type: "RESET_FOR_LOGOUT" });
    dispatch({
      type: "AUTH_STATE",
      value: "authenticated",
      token: session.token,
      authMode: session.authMode,
      username: session.username,
    });
  }

  // Sidebar "Logout": clears the stored token (same helper LoginScreen's
  // session-expiry path already uses) and resets to unauthenticated via
  // the existing RESET_FOR_LOGOUT action — one dispatch does both, since
  // its target state already IS "unauthenticated" (see AssistantContext.jsx).
  // That state flip alone unwinds everything else through mechanisms that
  // already exist: the /ws + /ws/audio-out effect tears itself down and
  // stops the mic (see its cleanup above), the activity log/username/
  // profile-derived labels clear with it, and the login overlay reappears
  // because `authenticated` below goes false. No previous user's activity
  // or profile is left visible.
  function handleLogout() {
    clearStoredToken();
    setMenuOpen(false);
    dispatch({ type: "RESET_FOR_LOGOUT" });
  }

  // Item 2: web equivalent of the desktop INTERRUPT button — stops the
  // backend mid-speech via the existing interrupt() mechanism (POST
  // /api/interrupt) AND clears whatever's already scheduled in the
  // browser's own audio queue, so it's a real interrupt, not just a paused
  // animation. Returns to LISTENING via the same STATUS_MESSAGE the backend
  // already sends after interrupt() runs (main.py's ui.write_log path) —
  // no separate control system.
  function handleInterrupt() {
    audioRef.current?.stopPlayback();
    clearTimeout(audioIdleTimer.current);
    // Reuses the exact SPEAKING→LISTENING transition AUDIO_IDLE_TIMEOUT
    // already performs (see AssistantContext.jsx) instead of a new action —
    // fired immediately here since playback was just force-stopped, not
    // left to the normal 900ms idle wait.
    dispatch({ type: "AUDIO_IDLE_TIMEOUT" });
    if (state.token) {
      sendInterrupt(state.token).catch(() => {
        dispatch({ type: "SYS_MESSAGE", text: "Interrupt failed to send.", ts: null });
      });
    }
  }

  function handleSend(text) {
    dispatch({ type: "LOG_MESSAGE", speaker: "user", text, ts: new Date().toISOString() });
    if (!socketRef.current?.sendCommand(text)) {
      sendCommand(state.token, text).catch(() => {
        dispatch({ type: "SYS_MESSAGE", text: "Command failed to send.", ts: null });
      });
    }
  }

  function handleToggleMic() {
    if (!micRef.current) {
      micRef.current = new MicStreamer(state.token, (s) =>
        dispatch({ type: "MIC_STATE", value: s })
      );
    }
    if (micRef.current.active) {
      micRef.current.stop();
    } else {
      micRef.current.start();
    }
  }

  useEffect(() => () => micRef.current?.stop(), []);

  // Phase 9: the main shell renders regardless of auth state (matching the
  // desktop app's own layout — the HUD/panels are always there, an overlay
  // gates access on top of it), with the login card as a dimmed overlay
  // instead of a full-screen replacement. sessionError still short-circuits
  // to a minimal message since the shell can't do much without /api/session
  // ever having loaded (no assistant name, no tool list).
  if (sessionError && !sessionLoaded) {
    return (
      <div className="login-overlay">
        <div className="login-card">
          <h1>◈ SARANA</h1>
          <p>{sessionError}</p>
          <p>Make sure the backend is running: <code>python server_main.py</code></p>
        </div>
      </div>
    );
  }

  const authenticated = state.authenticationState === "authenticated";
  const disabled = !authenticated || state.connectionState !== "connected";

  return (
    <div className="app-shell">
      <Header
        assistantName={state.assistantName}
        connectionState={authenticated ? state.connectionState : "disconnected"}
        desktopConnected={state.desktopConnected}
        username={state.username}
        authMode={state.authMode}
        onMenuClick={() => setMenuOpen(true)}
      />
      {authenticated && <ConnectionBanner connectionState={state.connectionState} />}
      <div className="app-body">
        <div className="panel-left">
          <ToolsRail tools={state.tools} />
        </div>
        <div className="panel-center">
          <Orb status={authenticated ? state.assistantStatus : "SLEEPING"} assistantName={state.assistantName} />
          <ContentPanel content={state.content} onDismiss={() => dispatch({ type: "DISMISS_CONTENT" })} />
          <Controls
            onSend={handleSend}
            micState={state.microphoneState}
            onToggleMic={handleToggleMic}
            disabled={disabled}
            onInterrupt={handleInterrupt}
          />
        </div>
      </div>
      <SidePanel
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        messages={state.messages}
        onLogout={handleLogout}
      />
      {!authenticated && (
        <LoginScreen
          assistantName={state.assistantName}
          onAuthenticated={handleAuthenticated}
          error={state.authError}
        />
      )}
    </div>
  );
}
