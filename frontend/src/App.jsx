import { useCallback, useEffect, useRef, useState } from "react";
import { useAssistantDispatch, useAssistantState } from "./state/AssistantContext";
import { fetchSession, sendCommand, sendInterrupt, sendLocation, logout, ApiError } from "./lib/api";
import { getCurrentLocation } from "./lib/geolocation";
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

// Location foundation/refresh: the one place a browser location fix is
// actually requested-and-sent — used both by the once-per-login effect
// below and by the backend-initiated "location_refresh_request" /ws
// message (see main.py's _get_current_location()). A denial/timeout/
// unsupported-browser/unavailable-position all resolve identically: the
// backend just never receives an update, and continues honestly
// reporting location as unavailable — never surfaced as an error here,
// never retried automatically by this function itself (each call site
// decides its own retry policy, if any).
function requestAndSendLocation(token) {
  return getCurrentLocation()
    .then((fix) => sendLocation(token, fix))
    .catch(() => {
      /* denied / unavailable / timeout / unsupported — nothing to send */
    });
}

export default function App() {
  const state = useAssistantState();
  const dispatch = useAssistantDispatch();

  const [sessionError, setSessionError] = useState(null);
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const socketRef = useRef(null);
  const audioRef = useRef(null);
  const micRef = useRef(null);
  const micAutoStartedRef = useRef(false); // item 1: auto-start mic once per login, not per utterance
  const locationRequestedRef = useRef(false); // location foundation: one browser location attempt per login

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
          case "location_refresh_request":
            // Backend-initiated refresh (see main.py's
            // _get_current_location()) — reuses the browser's already-
            // granted permission silently; a denial/timeout resolves the
            // same honest way the initial request does (see
            // requestAndSendLocation()). Not gated by locationRequestedRef
            // — that ref only limits the automatic once-per-login attempt,
            // not an explicit backend-requested refresh.
            requestAndSendLocation(state.token);
            break;
          default:
            break; // unknown type — never crash, matches backend's own tolerance
        }
      },
    });
    socket.connect();
    socketRef.current = socket;

    // Web UI state fix: assistantStatus (SPEAKING/LISTENING/etc.) comes
    // ONLY from the backend's authoritative "status" broadcasts now (see
    // AssistantContext.jsx's STATUS_MESSAGE case / main.py's
    // _push_state()) — audio-out activity is no longer used to infer it.
    // AudioOutPlayer's onState is still wired for AUDIO_STATE, which
    // tracks the /ws/audio-out socket's OWN connection health
    // (idle|connecting|open|error) — a genuinely different, legitimate
    // concern from "is SARANA speaking".
    const audio = new AudioOutPlayer(
      state.token,
      (s) => dispatch({ type: "AUDIO_STATE", value: s }),
    );
    audio.connect();
    audioRef.current = audio;

    return () => {
      socket.close();
      audio.close();
      socketRef.current = null;
      audioRef.current = null;
      // Session is ending (logout / token change / auth failure) — the mic
      // shouldn't keep streaming against a dead token, and the next login
      // gets its own fresh auto-start (see the effect below).
      micRef.current?.stop();
      micRef.current = null;
      micAutoStartedRef.current = false;
      // Location foundation: the next login (even the same account
      // logging back in) gets its own fresh browser location attempt —
      // mirrors micAutoStartedRef's reset above for the same reason.
      locationRequestedRef.current = false;
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

  // Location foundation: request the browser's native one-shot location
  // permission once per authenticated session — never on the login/PIN
  // screen, never before authentication (gated on authenticationState
  // itself, which only becomes "authenticated" after a real login or a
  // restored valid session — see the two effects above). Deliberately
  // independent of connectionState (unlike the mic effect above): sending
  // a location fix only needs the auth token, not an open /ws socket, so
  // there's no reason to make it wait on one.
  //
  // A denial/timeout/unsupported-browser/unavailable-position all resolve
  // the exact same way here: nothing is sent, login continues completely
  // normally, and — critically — this is never retried automatically
  // within the same login (guarded by the ref, reset only on the next
  // fresh login/logout above), so the browser's permission prompt is
  // never spammed.
  useEffect(() => {
    if (state.authenticationState !== "authenticated" || !state.token) return;
    if (locationRequestedRef.current) return;
    locationRequestedRef.current = true;

    requestAndSendLocation(state.token);
  }, [state.authenticationState, state.token]);

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

  // Sidebar "Logout": tells the backend first (POST /api/logout — removes
  // this token/session bookkeeping server-side, see dashboard/server.py's
  // _forget_token()) so it isn't left to the passive TTL sweep, THEN
  // clears the stored token (same helper LoginScreen's session-expiry
  // path already uses) and resets to unauthenticated via the existing
  // RESET_FOR_LOGOUT action — one dispatch does both, since its target
  // state already IS "unauthenticated" (see AssistantContext.jsx). That
  // state flip alone unwinds everything else through mechanisms that
  // already exist: the /ws + /ws/audio-out effect tears itself down and
  // stops the mic (see its cleanup above), the activity log/username/
  // profile-derived labels clear with it, and the login overlay reappears
  // because `authenticated` below goes false. No previous user's activity
  // or profile is left visible. The local state is cleared in `finally`
  // regardless of whether the backend call succeeds — an unreachable
  // backend must never trap the user in a "logged in" UI they can't
  // actually use; the stale token is still safely cleaned up eventually
  // by the server's own TTL sweep either way.
  async function handleLogout() {
    const token = state.token;
    setMenuOpen(false);
    try {
      if (token) await logout(token);
    } catch {
      /* backend unreachable/erroring — local state still clears below */
    } finally {
      clearStoredToken();
      dispatch({ type: "RESET_FOR_LOGOUT" });
    }
  }

  // Item 2: web equivalent of the desktop INTERRUPT button — stops the
  // backend mid-speech via the existing interrupt() mechanism (POST
  // /api/interrupt) AND clears whatever's already scheduled in the
  // browser's own audio queue, so it's a real interrupt, not just a paused
  // animation. main.py's interrupt() calls set_speaking(False), which
  // _push_state()s the real LISTENING transition over the SAME /ws
  // connection the POST response arrives alongside — no separate,
  // optimistic client-side guess needed (see AssistantContext.jsx's
  // STATUS_MESSAGE case).
  function handleInterrupt() {
    audioRef.current?.stopPlayback();
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

  // Web UI state fix: MUTED is the one displayed state the backend can't
  // know for a web session — see AssistantContext.jsx's assistantStatus
  // comment. It's a genuinely authoritative CLIENT-side fact (the mic
  // stream really is off), not a guess, computed here the same way
  // ui.py's own desktop mute button is entirely local to the UI layer and
  // never round-trips through JarvisLive either. Only overlays LISTENING
  // — SARANA already being SPEAKING/THINKING/SLEEPING is never
  // reinterpreted as MUTED just because the mic happens to be off.
  const micActive = state.microphoneState === "streaming";
  const displayStatus = !authenticated
    ? "SLEEPING"
    : state.assistantStatus === "LISTENING" && !micActive
      ? "MUTED"
      : state.assistantStatus;

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
          <Orb status={displayStatus} assistantName={state.assistantName} />
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
