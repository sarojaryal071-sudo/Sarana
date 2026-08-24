// src/lib/api.js — thin REST client for the existing dashboard backend.
//
// Every route here already exists (Phase 3/4 of the migration, plus the
// original PIN/QR login flow) — this file adds no new backend surface,
// it only calls what's already there from a browser origin different from
// the backend's own (hence VITE_JARVIS_BACKEND_URL instead of relative
// paths, which is what dashboard/static/app.html can get away with since
// it's served BY the backend itself).

export const BACKEND_URL = (
  import.meta.env.VITE_JARVIS_BACKEND_URL || "http://localhost:8000"
).replace(/\/+$/, "");

export function wsBaseUrl() {
  // Mirrors dashboard/static/app.html's own ws/wss selection, just anchored
  // to the configured backend origin instead of location.host.
  const u = new URL(BACKEND_URL);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.toString().replace(/\/+$/, "");
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function asJson(resp) {
  let body = null;
  try {
    body = await resp.json();
  } catch {
    /* no body / not JSON */
  }
  if (!resp.ok) {
    throw new ApiError(body?.error || `Request failed (${resp.status})`, resp.status);
  }
  return body;
}

/** GET /api/session — unauthenticated, tells us assistant_name/tools/desktop_connected. */
export async function fetchSession() {
  const resp = await fetch(`${BACKEND_URL}/api/session`);
  return asJson(resp);
}

/** POST /login — existing PIN pairing flow. Returns {ok, token}. */
export async function loginWithPin(pin) {
  const resp = await fetch(`${BACKEND_URL}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin }),
  });
  return asJson(resp);
}

/** POST /login/username — Phase 8: lightweight username IDENTIFICATION,
 * not authentication (no password, no registration). Returns {ok, token,
 * username}. Distinct from loginWithPin: this never implies control of a
 * particular physical desktop. */
export async function loginWithUsername(username) {
  const resp = await fetch(`${BACKEND_URL}/login/username`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  return asJson(resp);
}

/** POST /api/device-login — reuses a persisted device_token from a previous pairing. */
export async function loginWithDeviceToken(deviceToken) {
  const resp = await fetch(`${BACKEND_URL}/api/device-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_token: deviceToken }),
  });
  return asJson(resp);
}

/** POST /api/command — send a plaintext user command. Auth required. */
export async function sendCommand(token, text) {
  const resp = await fetch(`${BACKEND_URL}/api/command`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ text }),
  });
  return asJson(resp);
}

/** POST /api/wake — nudge the assistant awake without a command. */
export async function sendWake(token) {
  const resp = await fetch(`${BACKEND_URL}/api/wake`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  return asJson(resp);
}

export { ApiError };
