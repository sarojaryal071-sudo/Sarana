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

  messages: [], // {speaker: "user"|"jarvis"|"sys", text, ts}
  content: null, // {title, text} | null

  audioState: "idle", // idle | connecting | open | playing | error
  microphoneState: "idle", // idle | requesting | denied | unsupported | streaming | error
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
      return { ...state, content: { title: action.title, text: action.text } };
    case "DISMISS_CONTENT":
      return { ...state, content: null };
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
