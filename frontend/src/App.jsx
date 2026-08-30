import { useCallback, useEffect, useRef, useState } from "react";
import { useAssistantDispatch, useAssistantState } from "./state/AssistantContext";
import {
  fetchSession, sendCommand, sendInterrupt, sendLocation, logout, ApiError,
  googleCalendarConnectUrl, fetchCalendarStatus, disconnectCalendar,
  sendCapabilities,
} from "./lib/api";
import { getCurrentLocation } from "./lib/geolocation";
import { permissionManager } from "./lib/permissions";
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
    .then((fix) => {
      // Permissions foundation: a fix was actually obtained, so the real
      // permission is granted — fold that observation into the shared
      // cache (see handleToggleMic's identical reasoning for microphone).
      permissionManager.reportObserved("location", "granted");
      return sendLocation(token, fix);
    })
    .catch((e) => {
      // "denied"/"unsupported" are genuine permission-outcome
      // observations; "unavailable"/"timeout" are not (the permission
      // itself may still be granted — a fix just didn't come back), so
      // those are left for permissionManager's own query() to determine
      // honestly rather than guessed at here.
      if (e?.code === "denied" || e?.code === "unsupported") {
        permissionManager.reportObserved("location", e.code);
      }
    });
}

export default function App() {
  const state = useAssistantState();
  const dispatch = useAssistantDispatch();

  const [sessionError, setSessionError] = useState(null);
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  // Google Calendar: null while unknown/loading, otherwise {connected, email}
  // — never a token, see lib/api.js's fetchCalendarStatus().
  const [calendarStatus, setCalendarStatus] = useState(null);
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

  // Google Calendar: after returning from Google's consent screen, the
  // backend redirects back here with a ?calendar=connected|error|
  // cancelled query param (see dashboard/server.py's /auth/google/
  // callback) — never a token of any kind, just an outcome flag. This is
  // a fresh page load, so the SARANA auth token itself is restored
  // separately by the effect above (sessionStorage survives the round
  // trip through Google unaffected) — this effect only surfaces a brief
  // message and cleans the URL; the calendar-status effect below already
  // re-fetches status on every fresh authenticated mount, so no separate
  // forced refresh is needed here.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("calendar");
    if (!result) return;

    const messages = {
      connected: "Google Calendar connected.",
      error: "Couldn't connect Google Calendar — please try again.",
      cancelled: "Google Calendar connection was cancelled.",
    };
    if (messages[result]) {
      dispatch({ type: "SYS_MESSAGE", text: messages[result], ts: null });
    }

    params.delete("calendar");
    const rest = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (rest ? `?${rest}` : ""));
  }, [dispatch]);

  // ── wire up /ws + /ws/audio-out once authenticated ──────────────────────
  useEffect(() => {
    if (state.authenticationState !== "authenticated" || !state.token) return;

    // Capability coordinator: the microphone's "consumer" — the thing
    // that actually starts/stops streaming once permissionManager's
    // EFFECTIVE microphone state changes, from any of: the main mic
    // button, the Settings switch, or a live browser permission change
    // (e.g. revoked mid-conversation via the browser's own UI). Neither
    // the button nor Settings decides on/off directly anymore — both
    // call permissionManager.toggle()/enable(), and this is the one
    // place that turns that decision into a real MicStreamer start/stop.
    // Registered fresh per token (this effect re-runs on every login/
    // token change) so a fresh login always drives a MicStreamer bound
    // to ITS OWN token — mirrors this same effect's ownership of
    // micRef's lifecycle below.
    permissionManager.registerConsumer("microphone", {
      onEnable: () => {
        if (!micRef.current) {
          micRef.current = new MicStreamer(state.token, (s) => {
            dispatch({ type: "MIC_STATE", value: s });
            // A real getUserMedia attempt just observed the ACTUAL
            // permission outcome directly — fold it into the shared
            // cache so Settings and the backend both reflect it, even
            // on a browser (e.g. Firefox) where the Permissions API
            // itself can't be queried for "microphone" ahead of time.
            if (s === "denied" || s === "unsupported") {
              permissionManager.reportObserved("microphone", s);
            } else if (s === "streaming") {
              permissionManager.reportObserved("microphone", "granted");
            }
          });
        }
        micRef.current.start();
      },
      onDisable: () => {
        micRef.current?.stop();
      },
    });

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
            //
            // Capability coordinator: unlike the once-per-login attempt
            // below (which always runs at a moment SARANA's own location
            // preference is guaranteed to still be at its session-start
            // default), this can fire LATER in the same session, after
            // the user may have turned Location off in Settings — a
            // silent fetch-and-send at that point would use location
            // behind the user's back even though no dialog would show.
            // Respect the same effective state everything else does.
            if (permissionManager.getEffectiveState("location") === "granted") {
              requestAndSendLocation(state.token);
            }
            break;
          case "audio_stop":
            // Barge-in: Gemini's own server-side VAD detected the user
            // talking over SARANA (see main.py's sc.interrupted handling /
            // dashboard/server.py's broadcast_audio_stop()). Flushes
            // whatever assistant audio the browser already received over
            // /ws/audio-out and may still have scheduled to play, so
            // playback stops immediately instead of finishing out audio
            // that was already in flight before the interruption was
            // detected. Mirrors handleInterrupt()'s own stopPlayback()
            // call — this is the automatic, server-initiated counterpart
            // of that manual control.
            audioRef.current?.stopPlayback();
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
      // The consumer closure above is bound to THIS token — clear it so
      // nothing stale can fire between this teardown and the next
      // login's fresh registration.
      permissionManager.registerConsumer("microphone", undefined);
      // Location foundation: the next login (even the same account
      // logging back in) gets its own fresh browser location attempt —
      // mirrors micAutoStartedRef's reset above for the same reason.
      locationRequestedRef.current = false;
      // Capability coordinator: forgets SARANA's own enable preference
      // for every capability at the exact same point the refs above
      // reset — the next login starts fresh at the default (enabled),
      // mirroring the mic/location auto-request effects' own existing
      // "never remember across a reload/relogin" behavior (see
      // permissionManager.resetSession()'s own docstring for why that
      // precedent, not localStorage, is what this was modeled on).
      permissionManager.resetSession();
    };
  }, [state.authenticationState, state.token, dispatch]);

  // Item 1: once the WS is actually open, start the mic automatically —
  // via permissionManager.enable("microphone") rather than touching
  // MicStreamer directly: if the browser already granted mic access
  // (a returning user), this is a same-tick local enable with no new
  // prompt; if not yet decided, this is what makes the real browser
  // request, still inside the async continuation of the login button's
  // own click (well within every browser's transient-activation window
  // for getUserMedia), so the one permission prompt this can trigger is
  // the same "first browser permission request" the spec accepts. If the
  // browser denies/blocks it anyway, MicStreamer already reports
  // "denied"/"unsupported" and Controls.jsx surfaces that honestly — no
  // fake workaround. Runs once per login (guarded by the ref, reset on
  // logout above); a manual mic stop afterward (via either the main
  // button or Settings) is respected — this never force-restarts it.
  useEffect(() => {
    if (state.authenticationState !== "authenticated") return;
    if (state.connectionState !== "connected") return;
    if (micAutoStartedRef.current) return;
    micAutoStartedRef.current = true;
    permissionManager.enable("microphone");
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

  // Google Calendar: fetch connection status once per authenticated
  // mount (fresh login, restored session, or a fresh mount right after
  // returning from the OAuth redirect above) — this is what actually
  // reflects a just-completed connect/disconnect back into the UI, not a
  // forced refresh from the redirect-handling effect itself. Only ever
  // {"connected": bool, "email": str} — see lib/api.js's
  // fetchCalendarStatus(); never a token.
  useEffect(() => {
    if (state.authenticationState !== "authenticated" || !state.token) {
      setCalendarStatus(null);
      return;
    }
    let cancelled = false;
    fetchCalendarStatus(state.token)
      .then((s) => { if (!cancelled) setCalendarStatus(s); })
      .catch(() => { if (!cancelled) setCalendarStatus({ connected: false, email: "" }); });
    return () => { cancelled = true; };
  }, [state.authenticationState, state.token]);

  // Permissions foundation: mirrors the calendar-status effect above —
  // once authenticated, subscribe to the centralized permission manager
  // (lib/permissions.js) for microphone/location and forward whatever it
  // reports to the backend (POST /api/capabilities), so main.py can
  // explain honestly (not generically) when a permission is the actual
  // reason something isn't working — see main.py's [LOCATION]/
  // [CAPABILITIES] context. subscribe() itself only ever QUERIES the
  // real permission (never prompts) — least privilege, no permission is
  // requested just because the app opened.
  useEffect(() => {
    if (state.authenticationState !== "authenticated" || !state.token) return;
    const token = state.token;
    const unsubMic = permissionManager.subscribe("microphone", (value) => {
      sendCapabilities(token, { microphone: value }).catch(() => {
        /* best-effort — the backend just keeps its previous known state */
      });
    });
    const unsubLoc = permissionManager.subscribe("location", (value) => {
      sendCapabilities(token, { location: value }).catch(() => {});
    });
    return () => {
      unsubMic();
      unsubLoc();
    };
  }, [state.authenticationState, state.token]);

  // "Connect Google Calendar": a real full-page navigation to our own
  // backend (GET /auth/google), which itself redirects to Google's
  // consent screen — never a fetch, never handles a Google token
  // directly in the browser at all (see lib/api.js's
  // googleCalendarConnectUrl()).
  function handleConnectCalendar() {
    if (!state.token) return;
    window.location.href = googleCalendarConnectUrl(state.token);
  }

  async function handleDisconnectCalendar() {
    if (!state.token) return;
    try {
      await disconnectCalendar(state.token);
    } catch {
      /* backend unreachable/erroring -- status is still re-fetched below
         either way, same "don't trap the user" tolerance App.jsx's own
         handleLogout() already uses */
    }
    try {
      setCalendarStatus(await fetchCalendarStatus(state.token));
    } catch {
      setCalendarStatus({ connected: false, email: "" });
    }
  }

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

  // The main mic button no longer decides on/off itself (it used to
  // branch directly on the ref's own streaming flag) — it calls the exact same
  // permissionManager.toggle() the Settings mic switch calls
  // (PermissionsSettings.jsx), so both controls always agree: whichever
  // one is clicked, permissionManager's single "microphone" entry is
  // what actually changes, and the registered consumer above (this
  // effect's onEnable/onDisable) is what turns that into a real
  // MicStreamer start/stop. Neither this button nor Settings keeps an
  // independent on/off boolean of its own.
  function handleToggleMic() {
    permissionManager.toggle("microphone");
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
        calendarStatus={calendarStatus}
        onConnectCalendar={handleConnectCalendar}
        onDisconnectCalendar={handleDisconnectCalendar}
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
