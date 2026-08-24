// src/state/AssistantContext.jsx — single centralized state model, per the
// Phase 6 spec's "state management" requirement. Every WebSocket message
// and REST response is funneled through this reducer instead of scattering
// connection state across components.
import { createContext, useContext, useReducer } from "react";

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

  // Derived from "status" (session active/sleeping) + live audio-out
  // activity — the backend does not broadcast granular THINKING/LISTENING/
  // SPEAKING transitions today (see Phase 6 report, "problems discovered").
  assistantStatus: "SLEEPING", // SLEEPING | LISTENING | SPEAKING

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
      const sleeping = action.state !== "active";
      return {
        ...state,
        assistantStatus: sleeping
          ? "SLEEPING"
          : state.assistantStatus === "SPEAKING"
            ? "SPEAKING"
            : "LISTENING",
      };
    }
    case "AUDIO_ACTIVITY":
      // A chunk just arrived on /ws/audio-out — infer SPEAKING while active
      // (a session that's SLEEPING stays SLEEPING regardless).
      return state.assistantStatus === "SLEEPING"
        ? state
        : { ...state, assistantStatus: "SPEAKING" };
    case "AUDIO_IDLE_TIMEOUT":
      return state.assistantStatus === "SPEAKING"
        ? { ...state, assistantStatus: "LISTENING" }
        : state;
    case "LOG_MESSAGE":
      return {
        ...state,
        messages: [...state.messages, { speaker: action.speaker, text: action.text, ts: action.ts }],
      };
    case "SYS_MESSAGE":
      return {
        ...state,
        messages: [...state.messages, { speaker: "sys", text: action.text, ts: action.ts }],
      };
    case "CONTENT_MESSAGE":
      return { ...state, content: { title: action.title, text: action.text } };
    case "DISMISS_CONTENT":
      return { ...state, content: null };
    case "RESET_FOR_LOGOUT":
      return { ...initialState, assistantName: state.assistantName, tools: state.tools };
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
