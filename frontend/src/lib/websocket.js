// src/lib/websocket.js — manager for the existing /ws contract.
//
// Protocol is NOT invented here — it's exactly dashboard/server.py's /ws
// route (Phase 3), the same one dashboard/static/app.html already speaks:
//   client -> server: {"type": "command", "text": "..."}
//                      {"type": "device_action_result", ...}  (not sent by
//                        this frontend — that's a Phase 6 desktop-agent
//                        message, reserved but not implemented anywhere yet)
//   server -> client: {"type": "log", "speaker", "text", "ts"}
//                      {"type": "status", "state"}
//                      {"type": "sys", "text"}
//                      {"type": "file_received", ...}
//                      {"type": "content", "title", "text"}
//                      {"type": "device_action", ...}  (reserved, unsent today)
//
// This class only connects/reconnects/dispatches. It never crashes the app
// on an unrecognized message type — same tolerance the backend itself has
// for unrecognized client->server types (see dashboard/server.py's /ws
// receive loop).

import { wsBaseUrl } from "./api";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export class JarvisSocket {
  /**
   * @param {string} token - bearer token from /login or /api/device-login
   * @param {object} handlers
   * @param {(state: "connecting"|"open"|"closed"|"error") => void} handlers.onState
   * @param {(msg: object) => void} handlers.onMessage
   */
  constructor(token, handlers = {}) {
    this._token = token;
    this._handlers = handlers;
    this._ws = null;
    this._closedByUser = false;
    this._attempt = 0;
    this._reconnectTimer = null;
    this._everOpened = false;
  }

  connect() {
    this._closedByUser = false;
    this._open();
  }

  _open() {
    this._setState("connecting");
    const url = `${wsBaseUrl()}/ws?token=${encodeURIComponent(this._token)}`;
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      this._setState("error");
      this._scheduleReconnect();
      return;
    }
    this._ws = ws;

    ws.onopen = () => {
      this._attempt = 0;
      this._everOpened = true;
      this._setState("open");
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return; // malformed message — ignore, never crash the UI
      }
      try {
        this._handlers.onMessage?.(msg);
      } catch (e) {
        console.error("[JarvisSocket] handler error", e);
      }
    };

    ws.onerror = () => {
      this._setState("error");
    };

    ws.onclose = () => {
      this._setState("closed");
      if (this._closedByUser) return;
      // The backend closes with 4001 immediately (never reaching onopen)
      // when the token is missing/invalid — an auth problem, not a
      // transient network blip, so don't loop reconnecting forever.
      if (!this._everOpened) {
        this._handlers.onAuthFailure?.();
        return;
      }
      this._scheduleReconnect();
    };
  }

  _scheduleReconnect() {
    if (this._closedByUser) return;
    clearTimeout(this._reconnectTimer);
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this._attempt, RECONNECT_MAX_MS);
    this._attempt += 1;
    this._reconnectTimer = setTimeout(() => this._open(), delay);
  }

  _setState(state) {
    this._handlers.onState?.(state);
  }

  /** Send a plaintext command over the socket — same shape the existing
   * dashboard frontend and the desktop UI's own command path use. */
  sendCommand(text) {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({ type: "command", text }));
      return true;
    }
    return false;
  }

  close() {
    this._closedByUser = true;
    clearTimeout(this._reconnectTimer);
    this._ws?.close();
  }
}
