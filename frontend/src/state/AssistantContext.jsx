// src/state/AssistantContext.jsx — single centralized state model, per the
// Phase 6 spec's "state management" requirement. Every WebSocket message
// and REST response is funneled through this reducer instead of scattering
// connection state across components.
import { createContext, useContext, useReducer } from "react";

// Resource-cleanup fix: `messages` (the Activity Log's data — see
// SidePanel.jsx/LogPanel.jsx) used to grow for the entire life of a
// session with no cap, in both the array itself and its per-message
// re-render cost whenever the log panel is open. 300 mirrors the
// backend's own _history cap in dashboard/server.py — plenty for a
// single session's actual activity log, bounded either way.
const MAX_MESSAGES = 300;

function appendMessage(messages, message) {
  const next = [...messages, message];
  return next.length > MAX_MESSAGES ? next.slice(-MAX_MESSAGES) : next;
}

const initialState = {
  connectionState: "disconnected", // disconnected | connecting | connected | reconnecting | error
  authenticationState: "unauthenticated", // unauthenticated | authenticating | authenticated | error
  authError: null,
  token: null,
  // Phase 8: which login path established this session, and (for username
  // logins only) the identified name. Purely descriptive/UI-facing — the
  // backend is the actual source of truth (dashboard/server.py's
  // _session_auth_mode/_session_usernames), this just mirrors what the
  // login response returned.
  authMode: null, // "username" | "remote" | null
  username: null,

  assistantName: "SARANA",
  tools: [],
  desktopConnected: false,

  // Web UI state fix: set DIRECTLY from the backend's own authoritative
  // "status" broadcasts (main.py's _push_state() — the exact same call
  // sites ui.py's desktop HUD reacts to), never inferred from side
  // effects like audio-out packets arriving. "MUTED" is NOT a value this
  // ever holds — muting is a purely client-side fact (the browser mic
  // stream on/off) the backend has no way to know for a web session; see
  // App.jsx's displayStatus, which overlays that on top of this.
  assistantStatus: "SLEEPING", // SLEEPING | LISTENING | THINKING | SPEAKING

  // JARVIS Mode: purely a reflection of main.py's self._jarvis_mode (see
  // its own docstring) — the backend OWNS this; the frontend never
  // toggles it independently, only renders whatever the last
  // "jarvis_mode_changed" WS message (or RESET_FOR_LOGOUT, below) said.
  // Session-scoped like everything else in this reducer: a fresh login
  // (RESET_FOR_LOGOUT resets to initialState) always starts back at
  // false, exactly mirroring the backend's own reconnect reset.
  jarvisMode: false,

  // SARANA Face UI: a temporary, explicit mood override from main.py's
  // set_expression tool (see App.jsx's "expression_override" WS case) —
  // {expression, until} | null. `until` is a Date.now()-comparable
  // timestamp computed by the CALLER at dispatch time (same precedent as
  // LOG_MESSAGE/SYS_MESSAGE's own `ts` below), not inside this reducer.
  // Only ever read via lib/faceExpressions.js's resolveExpression(), which
  // additionally never lets it override the mechanical speaking/thinking/
  // muted states — see that function's own header.
  expressionOverride: null,

  messages: [], // {speaker: "user"|"jarvis"|"sys", text, ts}
  // Track 3 (Presentation Engine): `presentation` is the optional
  // structured payload (`{type, data}`) PresentationSurface renders —
  // see dashboard/server.py's broadcast_content(). Backward compatible:
  // ordinary plain-text content (presentation undefined/null) renders
  // exactly as it always has, via ContentPanel's own plain fallback.
  content: null, // {title, text, presentation?: {type, data}} | null

  // Universal Information Surface: expand/persistent are lifted OUT of
  // content (deliberately NOT nested inside it) so an in-place update to
  // the SAME surface (e.g. "what about tomorrow?" replacing the weather
  // payload) never resets them — only an explicit user action
  // (expand/collapse button, "keep this on screen", dismiss) changes
  // these two fields. Both the local UI controls AND main.py's
  // presentation_control tool (see App.jsx's "presentation_control" WS
  // case) dispatch the SAME two actions below — one shared surface,
  // controllable from either the user's click or their voice/text.
  presentationExpanded: false,
  // Persistence is deliberately separate from task completion (see
  // main.py's presentation_control tool docstring) — a task finishing
  // never implicitly dismisses its own presentation; only an explicit
  // dismiss does. This flag only marks whether the user asked to
  // deliberately keep it on screen (surfaced as a small "kept" indicator
  // — see PresentationSurface.jsx) — it does not currently change
  // whether anything auto-dismisses (nothing does), it is honest,
  // visible state for a real, explicit request.
  presentationPersistent: false,

  audioState: "idle", // idle | connecting | open | playing | error
  microphoneState: "idle", // idle | requesting | denied | unsupported | streaming | error

  // Track 3/4: output-only speech mute (main.py's speech_mute tool) —
  // backend-authoritative, mirrors jarvisMode's own reflect-only pattern
  // above. Never confused with microphoneState (input) or a future SLEEP
  // mode (this project's own docs draw the same distinction).
  speechMuted: false,
};

function reducer(state, action) {
  switch (action.type) {
    case "SESSION_LOADED":
      return {
        ...state,
        assistantName: action.assistantName,
        tools: action.tools,
        desktopConnected: action.desktopConnected,
      };
    case "DESKTOP_CONNECTED_CHANGED":
      return { ...state, desktopConnected: action.value };
    case "AUTH_STATE":
      return {
        ...state,
        authenticationState: action.value,
        authError: action.error ?? null,
        token: action.token ?? state.token,
        authMode: action.value === "unauthenticated" ? null : (action.authMode ?? state.authMode),
        username: action.value === "unauthenticated" ? null : (action.username ?? state.username),
      };
    case "CONNECTION_STATE":
      return { ...state, connectionState: action.value };
    case "AUDIO_STATE":
      return { ...state, audioState: action.value };
    case "MIC_STATE":
      return { ...state, microphoneState: action.value };
    case "JARVIS_MODE":
      return { ...state, jarvisMode: !!action.value };
    case "EXPRESSION_OVERRIDE":
      // action.expression is null to explicitly CLEAR the override (see
      // App.jsx's own expiry timer) — a real expression name to set one.
      return {
        ...state,
        expressionOverride: action.expression ? { expression: action.expression, until: action.until } : null,
      };
    case "STATUS_MESSAGE": {
      // Web UI state fix: msg.state is now main.py's real, granular state
      // string (LISTENING | THINKING | SPEAKING | SLEEPING) — see
      // _push_state(). Anything unrecognized is ignored rather than
      // guessed at, so a future/typo'd state string can never corrupt the
      // UI into a nonsense label.
      const KNOWN = new Set(["SLEEPING", "LISTENING", "THINKING", "SPEAKING"]);
      return KNOWN.has(action.state) ? { ...state, assistantStatus: action.state } : state;
    }
    case "LOG_MESSAGE":
      return {
        ...state,
        messages: appendMessage(state.messages, { speaker: action.speaker, text: action.text, ts: action.ts }),
      };
    case "SYS_MESSAGE":
      return {
        ...state,
        messages: appendMessage(state.messages, { speaker: "sys", text: action.text, ts: action.ts }),
      };
    case "CONTENT_MESSAGE":
      // Deliberately does NOT touch presentationExpanded/presentationPersistent
      // — an in-place update to the same surface (a follow-up like "what
      // about tomorrow?") must not silently collapse an expanded view or
      // forget the user asked to keep it on screen.
      return { ...state, content: { title: action.title, text: action.text, presentation: action.presentation ?? null } };
    case "DISMISS_CONTENT":
      return { ...state, content: null, presentationExpanded: false, presentationPersistent: false };
    case "PRESENTATION_EXPANDED":
      return { ...state, presentationExpanded: !!action.value };
    case "PRESENTATION_PERSISTENT":
      return { ...state, presentationPersistent: !!action.value };
    case "SPEECH_MUTE":
      return { ...state, speechMuted: !!action.value };
    case "RESET_FOR_LOGOUT":
      // Item 8: also doubles as "start a fresh session" on a new login, not
      // just logout — clears messages (activity log) and per-connection
      // state while keeping what GET /api/session already told us (no
      // refetch triggered by a login) so the header doesn't flash stale
      // placeholders.
      return {
        ...initialState,
        assistantName: state.assistantName,
        tools: state.tools,
        desktopConnected: state.desktopConnected,
      };
    default:
      return state;
  }
}

const AssistantStateContext = createContext(null);
const AssistantDispatchContext = createContext(null);

export function AssistantProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <AssistantStateContext.Provider value={state}>
      <AssistantDispatchContext.Provider value={dispatch}>
        {children}
      </AssistantDispatchContext.Provider>
    </AssistantStateContext.Provider>
  );
}

export function useAssistantState() {
  const ctx = useContext(AssistantStateContext);
  if (!ctx) throw new Error("useAssistantState must be used within AssistantProvider");
  return ctx;
}

export function useAssistantDispatch() {
  const ctx = useContext(AssistantDispatchContext);
  if (!ctx) throw new Error("useAssistantDispatch must be used within AssistantProvider");
  return ctx;
}
