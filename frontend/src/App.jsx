import { useCallback, useEffect, useRef, useState } from "react";
import { useAssistantDispatch, useAssistantState } from "./state/AssistantContext";
import {
  fetchSession, sendCommand, sendInterrupt, sendLocation, logout, ApiError,
  googleCalendarConnectUrl, fetchCalendarStatus, disconnectCalendar,
  sendCapabilities,
} from "./lib/api";
import { getCurrentLocation } from "./lib/geolocation";
import { prepareImageForUpload, readFileAsBase64 } from "./lib/image";
import { permissionManager } from "./lib/permissions";
import { JarvisSocket } from "./lib/websocket";
import { AudioOutPlayer } from "./lib/audioOut";
import { setMouthLevel } from "./lib/mouthLevel";
import { MicStreamer } from "./lib/mic";
import { stopCameraVision } from "./lib/cameraVision";
import { stopScreenVision } from "./lib/screenVision";
import LoginScreen, { readStoredSession, clearStoredToken } from "./components/LoginScreen";
import Header from "./components/Header";
import SaranaFace from "./components/SaranaFace";
import IdentityTransition from "./components/IdentityTransition";
import Orb from "./components/Orb";
import SidePanel from "./components/SidePanel";
import ContentPanel from "./components/ContentPanel";
import Controls from "./components/Controls";
import ConnectionBanner from "./components/ConnectionBanner";
import ToolsRail from "./components/ToolsRail";
import VisionStage from "./components/VisionStage";

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
  // Web visual context (Phases 1-5): {source: "camera"|"screen",
  // requestId, facing} | null — mirrors the backend's own
  // self._web_vision_session (main.py), set by the "camera_vision_request"/
  // "screen_vision_request" WS messages and cleared by
  // "camera_vision_stop"/"screen_vision_stop" or the existing INTERRUPT
  // control (see handleInterrupt) — no separate Stop button exists for
  // this. Deliberately separate state from `pendingImage` (Controls.jsx)
  // — an entirely different feature. While non-null, VisionStage renders
  // in place of SaranaFace (see the render below) — the two are never
  // both mounted at once.
  const [visionRequest, setVisionRequest] = useState(null);

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
          case "image_error":
            // Web visual intelligence: dashboard/server.py rejected a
            // "image_command" (bad type/too large/not a real image) —
            // sent directly back to this socket, not broadcast. Reuses the
            // existing SYS_MESSAGE display, no new UI needed.
            dispatch({ type: "SYS_MESSAGE", text: msg.error || "That image couldn't be sent.", ts: null });
            break;
          case "vision_error":
            // Web visual context (camera or screen — see VisionStage.jsx):
            // dashboard/server.py rejected a "vision_frame" — same
            // treatment as image_error. A single rejected sampled frame
            // isn't fatal to the session (more frames follow shortly), so
            // this is surfaced but doesn't itself stop capture.
            dispatch({ type: "SYS_MESSAGE", text: msg.error || "A frame couldn't be sent.", ts: null });
            break;
          case "camera_vision_request":
            // Backend (main.py's web_camera_vision tool) wants to look
            // through the browser's camera — mounts VisionStage, which
            // owns the actual getUserMedia lifecycle (see
            // lib/cameraVision.js). A second request while one is already
            // active (Gemini asking for "another look") just replaces the
            // facing/requestId — VisionStage's own effect re-arms sampling.
            setVisionRequest({ source: "camera", requestId: msg.request_id, facing: msg.facing || "environment" });
            break;
          case "camera_vision_stop":
            // The backend's own observation session ended (answered,
            // timed out, or nothing arrived) — stop and unmount, but only
            // if this is actually the CURRENTLY active request (never let
            // a stale stop for an old request tear down a newer one).
            setVisionRequest((cur) => (cur && cur.source === "camera" && cur.requestId === msg.request_id ? null : cur));
            break;
          case "screen_vision_request":
            // Phase 4 — backend (main.py's web_screen_vision tool) wants
            // to see the user's screen — mounts the SAME VisionStage
            // component with source="screen", which owns the
            // getDisplayMedia lifecycle instead (see lib/screenVision.js).
            // Never confused with camera_vision_request — a completely
            // separate capability/browser API, just sharing the same thin
            // UI shell (see VisionStage.jsx's own header note).
            setVisionRequest({ source: "screen", requestId: msg.request_id });
            break;
          case "screen_vision_stop":
            setVisionRequest((cur) => (cur && cur.source === "screen" && cur.requestId === msg.request_id ? null : cur));
            break;
          case "jarvis_mode_changed":
            // JARVIS Mode: backend is the authoritative source of
            // self._jarvis_mode (see main.py's jarvis_mode tool) — this
            // frontend never toggles the mode itself, only reflects
            // whatever the backend just broadcast. Drives the orb-stage
            // conditional below (Orb replaces SaranaFace while active).
            dispatch({ type: "JARVIS_MODE", value: msg.active });
            break;
          case "expression_override":
            // SARANA Face UI: main.py's set_expression tool call (see
            // that tool's own docstring) — `until` is computed HERE, not
            // in the reducer (same precedent as LOG_MESSAGE/SYS_MESSAGE's
            // own `ts`, see AssistantContext.jsx), so the reducer stays a
            // pure function of its arguments. The actual expiry/revert is
            // handled by the effect below, not here.
            dispatch({
              type: "EXPRESSION_OVERRIDE",
              expression: msg.expression,
              until: Date.now() + (Number(msg.duration_ms) || 6000),
            });
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
      // Real per-chunk playback amplitude -> SaranaFace's mouth (see
      // lib/mouthLevel.js). Deliberately NOT a dispatch()/state update:
      // this fires several times a second while SARANA speaks, and
      // routing it through the reducer would re-render the whole app
      // tree that often for a value only the mesh's mouth group cares
      // about — the pub/sub module updates just that DOM node instead.
      setMouthLevel,
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
      // Web visual context: a session ending (logout/token change) must
      // never leave a camera or screen share running unattended — stop
      // both directly (VisionStage's own unmount effect would also catch
      // this, but it only unmounts on the NEXT render; this is immediate)
      // and clear the request so it doesn't try to reuse a stale
      // requestId if a new login starts. Calling the "wrong" source's
      // stop is always a harmless no-op (see each lib's own idempotent
      // stop function).
      stopCameraVision();
      stopScreenVision();
      setVisionRequest(null);
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
    // Web visual context: the existing INTERRUPT control doubles as the
    // camera/screen-share stop control too — no new/separate button was
    // added for this (see VisionStage.jsx, which renders no Stop button
    // of its own). A no-op when no vision request is active.
    if (visionRequest) {
      handleVisionStopped("user_interrupt");
    }
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

  // Web visual intelligence: reuses the SAME authenticated WS connection
  // and the SAME live Gemini session as ordinary text commands — see
  // websocket.js's sendImage() / main.py's
  // _process_dashboard_image_commands(). Client-side downscale
  // (prepareImageForUpload) is an optimization only; the backend's own
  // compression is authoritative either way, so a decode failure here
  // falls back to the file's raw bytes rather than blocking the send.
  // Unlike handleSend() above, there is deliberately no HTTP fallback —
  // /api/command's plaintext fallback predates this feature and exists
  // for a different reason (text still working across a dropped socket);
  // an image is large enough, and a live WS connection routine enough
  // here, that adding a parallel HTTP upload path isn't worth the
  // duplication for this milestone.
  async function handleSendImage(file, text) {
    const caption = text?.trim() || "What's in this image?";
    dispatch({ type: "LOG_MESSAGE", speaker: "user", text: caption, ts: new Date().toISOString() });

    let base64, mimeType;
    try {
      ({ base64, mimeType } = await prepareImageForUpload(file));
    } catch {
      try {
        ({ base64, mimeType } = await readFileAsBase64(file));
      } catch {
        dispatch({ type: "SYS_MESSAGE", text: "Could not read that image.", ts: null });
        return;
      }
    }

    if (!socketRef.current?.sendImage(base64, mimeType, caption)) {
      dispatch({ type: "SYS_MESSAGE", text: "Image failed to send — check your connection.", ts: null });
    }
  }

  // Web visual context: forwards one sampled frame from VisionStage
  // (lib/cameraVision.js or lib/screenVision.js, whichever source is
  // active) over the same authenticated WS as everything else — see
  // websocket.js's sendVisionFrame() / main.py's
  // _process_web_vision_frames(). Deliberately no dispatch/log entry per
  // frame (several arrive per second-ish; that would flood the Activity
  // Log) — the conversation itself is what SARANA says once it evaluates
  // a batch, which arrives as an ordinary "log" message like any other.
  function handleVisionFrame(base64, mimeType, seq) {
    if (!visionRequest) return;
    socketRef.current?.sendVisionFrame(visionRequest.requestId, base64, mimeType, seq);
  }

  // Fired when the user presses INTERRUPT while active, the tab is
  // backgrounded, or camera/screen capture fails mid-stream — tells the
  // backend so it can end self._web_vision_session immediately instead
  // of waiting out its own grace/timeout window, then clears local state.
  function handleVisionStopped(reason) {
    if (visionRequest) {
      socketRef.current?.sendVisionControl(visionRequest.requestId, "stop", reason);
    }
    setVisionRequest(null);
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

  // SARANA <-> JARVIS visual identity crossfade (Human-Orb UI task): the
  // orb-stage conditional below used to hard-swap SaranaFace/Orb the
  // instant state.jarvisMode flipped — an abrupt component replacement,
  // not the "smooth transition... intentional and polished" the brief
  // asks for. `identity` tracks the CURRENTLY DISPLAYED component
  // (deliberately one render behind state.jarvisMode during a switch);
  // `identityFading` drives a brief fade-out/fade-in via CSS (see
  // .identity-stage in index.css) around the actual swap, which happens
  // at the fade's midpoint so only ONE of Orb's canvas loop / SaranaFace's
  // SVG animation is ever mounted and animating at a time — a true
  // simultaneous crossfade would briefly run both, which is exactly the
  // kind of unnecessary extra animation cost this project's own
  // performance requirements (here and in SaranaFace's own history) have
  // consistently avoided. VisionStage is untouched by this — it already
  // "always wins" instantly and isn't part of the identity switch.
  const targetIdentity = state.jarvisMode ? "jarvis" : "sarana";
  const [identity, setIdentity] = useState(targetIdentity);
  const [identityPhase, setIdentityPhase] = useState(null); // null | "deconstruct" | "rebuild"
  const identityTimerRef = useRef(null);
  // "not a plain cinematic transition... like in an AI technological
  // movie where the current UI goes through a fast rebuild of another UI
  // with moving neons and a cinematic building process" — a real,
  // direct request. DECONSTRUCT_MS mirrors what .identity-stage's own
  // CSS transition duration below now uses (the REAL Orb/SaranaFace
  // fade), REBUILD_MS is purely the decorative IdentityTransition
  // overlay continuing its "HUD constructing" flourish a bit longer on
  // top, after the real content has already settled in — see
  // components/IdentityTransition.jsx's own header for the full design.
  const DECONSTRUCT_MS = 500;
  const REBUILD_MS = 900;

  useEffect(() => {
    if (targetIdentity === identity) return undefined;
    setIdentityPhase("deconstruct");
    identityTimerRef.current = setTimeout(() => {
      setIdentity(targetIdentity);
      setIdentityPhase("rebuild");
      identityTimerRef.current = setTimeout(() => {
        setIdentityPhase(null);
      }, REBUILD_MS);
    }, DECONSTRUCT_MS);
    return () => clearTimeout(identityTimerRef.current);
  }, [targetIdentity, identity]);

  const identityFading = identityPhase === "deconstruct";

  // SARANA Face UI: an active expression_override (see the WS handler
  // above) clears itself on a real timer rather than being silently
  // "expired" by resolveExpression() alone — resolveExpression is a pure
  // function of whatever `now` it's given, so without this effect
  // nothing would ever RE-RENDER SaranaFace once `until` passes if no
  // other state happened to change around the same moment. One
  // scheduled dispatch per override set, cleaned up on the next one
  // (or unmount) — never a polling interval.
  useEffect(() => {
    if (!state.expressionOverride) return undefined;
    const msLeft = state.expressionOverride.until - Date.now();
    if (msLeft <= 0) {
      dispatch({ type: "EXPRESSION_OVERRIDE", expression: null });
      return undefined;
    }
    const t = setTimeout(() => {
      dispatch({ type: "EXPRESSION_OVERRIDE", expression: null });
    }, msLeft);
    return () => clearTimeout(t);
  }, [state.expressionOverride, dispatch]);

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
          {/* Central visual stage: exactly ONE of VisionStage / Orb /
              SaranaFace is ever mounted here, in that priority order —
              never a second, separate floating element (see
              VisionStage.jsx, which renders into the identical
              "orb-stage" area both Orb.jsx and SaranaFace.jsx use).
              Camera/screen vision always wins regardless of mode (a live
              observation in progress is always shown, instantly — no
              crossfade). Otherwise: `identity` (not state.jarvisMode
              directly — see the crossfade effect above) picks Orb
              (JARVIS) or SaranaFace (normal SARANA); .identity-stage's
              fade class wraps whichever one is mounted so the switch
              reads as one intentional transformation instead of an
              abrupt swap, without ever animating both at once. */}
          {authenticated && visionRequest ? (
            <VisionStage
              source={visionRequest.source}
              requestId={visionRequest.requestId}
              facing={visionRequest.facing}
              onFrame={handleVisionFrame}
              onStopped={handleVisionStopped}
            />
          ) : (
            <div className={`identity-stage${identityFading ? " identity-stage-fading" : ""}`}>
              {identity === "jarvis" ? (
                <Orb status={displayStatus} assistantName={state.assistantName} />
              ) : (
                <SaranaFace status={displayStatus} assistantName={state.assistantName} expressionOverride={state.expressionOverride} />
              )}
              {identityPhase && <IdentityTransition phase={identityPhase} targetIdentity={targetIdentity} />}
            </div>
          )}
          <ContentPanel content={state.content} onDismiss={() => dispatch({ type: "DISMISS_CONTENT" })} />
          <Controls
            onSend={handleSend}
            onSendImage={handleSendImage}
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
