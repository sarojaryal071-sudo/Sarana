// src/components/LoginScreen.jsx — an OVERLAY (see App.jsx: the main shell
// always renders behind it, matching the desktop app's own layout) with
// two entry points into the same session mechanism, both reusing existing/
// added backend routes unchanged:
//
//   Username + PIN (default/primary) — POST /login/username, authenticated
//   against the backend's local SQLite profile store (users/user_db.py) —
//   a fixed, hand-seeded set of known profiles, not open registration.
//   Logging in also starts Jarvis automatically (Phase 9 — see dashboard/
//   server.py's /login/username) — there is no separate WAKE step in this
//   flow.
//
//   Remote Access (secondary, tucked behind a small link) — POST /login
//   (Phase 3, the original PIN pairing flow), completely unchanged. This is
//   a DIFFERENT thing from username login: it's how a specific desktop's
//   Remote Control PIN is redeemed, and implies nothing about which
//   desktop a username login is talking to (there may be none — a
//   headless backend has no physical desktop at all).
import { useState } from "react";
import { loginWithPin, loginWithUsername, ApiError } from "../lib/api";

const SESSION_KEY = "sarana_web_session"; // {token, username, authMode}

export function readStoredToken() {
  const s = readStoredSession();
  return s?.token || null;
}

export function readStoredSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function storeSession(session) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    /* sessionStorage unavailable (private mode etc.) — token stays in-memory only */
  }
}

export function clearStoredToken() {
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* ignore */
  }
}

function UsernameForm({ assistantName, onAuthenticated, error }) {
  const [username, setUsername] = useState("");
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    const trimmed = username.trim();
    if (!trimmed || !pin.trim() || busy) return;
    setBusy(true);
    setLocalError(null);
    try {
      const res = await loginWithUsername(trimmed, pin.trim());
      if (res?.ok && res.token) {
        const session = { token: res.token, username: res.username, authMode: "username" };
        storeSession(session);
        onAuthenticated(session);
      } else {
        // Backend deliberately returns one generic message for both an
        // unknown username and a wrong PIN — see dashboard/server.py's
        // /login/username — so nothing more specific is ever shown here.
        setLocalError(res?.error || "Could not start a session");
      }
    } catch (e) {
      setLocalError(e instanceof ApiError ? e.message : "Could not reach the backend");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="login-form">
      <p className="login-label">YOUR NAME</p>
      <input
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        placeholder={`e.g. Sarana`}
        maxLength={40}
        autoFocus
        disabled={busy}
        className="login-name-input"
      />
      <p className="login-label">PIN</p>
      <input
        value={pin}
        onChange={(e) => setPin(e.target.value)}
        placeholder="••••"
        maxLength={12}
        type="password"
        inputMode="numeric"
        disabled={busy}
      />
      {(localError || error) && <div className="error">{localError || error}</div>}
      <button className="btn primary" type="submit" disabled={busy || !username.trim() || !pin.trim()}>
        {busy ? "LOGGING IN…" : "▸ LOGIN"}
      </button>
    </form>
  );
}

function RemoteAccessForm({ onAuthenticated, error }) {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    if (!pin.trim() || busy) return;
    setBusy(true);
    setLocalError(null);
    try {
      const res = await loginWithPin(pin.trim());
      if (res?.ok && res.token) {
        const session = { token: res.token, username: null, authMode: "remote" };
        storeSession(session);
        onAuthenticated(session);
      } else {
        setLocalError(res?.error || "Invalid or expired key");
      }
    } catch (e) {
      setLocalError(e instanceof ApiError ? e.message : "Could not reach the backend");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="login-form">
      <p className="login-hint">
        Enter the temporary pairing PIN shown by the desktop app's "Remote Control"
        button (or printed to the backend console on startup).
      </p>
      <input
        value={pin}
        onChange={(e) => setPin(e.target.value.toUpperCase())}
        placeholder="XXXXXX"
        maxLength={6}
        autoFocus
        disabled={busy}
      />
      {(localError || error) && <div className="error">{localError || error}</div>}
      <button className="btn primary" type="submit" disabled={busy || !pin.trim()}>
        {busy ? "CONNECTING…" : "▸ CONNECT"}
      </button>
    </form>
  );
}

export default function LoginScreen({ assistantName, onAuthenticated, error }) {
  const [mode, setMode] = useState("username"); // "username" | "remote" — username is the default/primary path

  return (
    <div className="login-overlay">
      <div className="login-card">
        <h1>◈ {mode === "username" ? "USER LOGIN" : "REMOTE ACCESS"}</h1>
        {mode === "username" && (
          <>
            <p>Enter your name and PIN to start {assistantName || "Sarana"}.</p>
            <UsernameForm assistantName={assistantName} onAuthenticated={onAuthenticated} error={error} />
            <button type="button" className="login-alt-link" onClick={() => setMode("remote")}>
              ◉ Remote Access — connect to a specific desktop instead
            </button>
          </>
        )}
        {mode === "remote" && (
          <>
            <RemoteAccessForm onAuthenticated={onAuthenticated} error={error} />
            <button type="button" className="login-alt-link" onClick={() => setMode("username")}>
              ← Back to username login
            </button>
          </>
        )}
      </div>
      {/* Outside .login-card, still inside .login-overlay (see App.jsx —
          this whole component only renders while unauthenticated), so
          this line can never appear in the authenticated app/sidebar. A
          plain, non-interactive line of text, deliberately not a link or
          nav item. */}
      <p className="login-footer">Developed by Saroj</p>
    </div>
  );
}
