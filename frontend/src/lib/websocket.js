// src/lib/websocket.js — manager for the existing /ws contract.
//
// Protocol is NOT invented here — it's exactly dashboard/server.py's /ws
// route (Phase 3), the same one dashboard/static/app.html already speaks:
//   client -> server: {"type": "command", "text": "..."}
//                      {"type": "image_command", "data": <base64, no data:
//                        URI prefix>, "mime_type": "image/jpeg", "text": "..."}
//                        — web visual intelligence: a browser-submitted
//                        photo, injected into the SAME live Gemini session
//                        as the desktop screen_process tool already uses
//                        (see main.py's _process_dashboard_image_commands()).
//                      {"type": "device_action_result", ...}  (not sent by
//                        this frontend — that's a Phase 6 desktop-agent
//                        message, reserved but not implemented anywhere yet)
//                      {"type": "ping", "t": <ms>}  (item 3 audit — RTT probe,
//                        see _startPinging()/_onPong() below)
//   server -> client: {"type": "log", "speaker", "text", "ts"}
//                      {"type": "status", "state"}
//                      {"type": "sys", "text"}
//                      {"type": "file_received", ...}
//                      {"type": "content", "title", "text"}
//                      {"type": "image_error", "error"}  — a rejected
//                        image_command (bad type/too large/not a real
//                        image); forwarded to onMessage like any other type
//                      {"type": "device_action", ...}  (reserved, unsent today)
//                      {"type": "pong", "t": <the ping's own timestamp>}
//                        (handled internally, never forwarded to onMessage)
//
// This class only connects/reconnects/dispatches. It never crashes the app
// on an unrecognized message type — same tolerance the backend itself has
// for unrecognized client->server types (see dashboard/server.py's /ws
// receive loop).

import { wsBaseUrl } from "./api";
import { LatencyStats } from "./latencyStats";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

// Item 3 audit (transport latency): a lightweight browser<->Render RTT
// probe over the EXISTING /ws JSON channel — deliberately NOT touching
// /ws/phone-audio or /ws/audio-out's binary audio protocol at all, so
// there is zero risk to audio reliability from adding this. This is the
// one number backend-only instrumentation can't produce on its own: the
// actual physical network cost between this browser and Render.
const PING_INTERVAL_MS = 20000;
// Console only, and only every Nth pong — never spams on every RTT sample.
const PING_LOG_EVERY_N = 5;

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
    this._pingTimer = null;
    this._rtt = new LatencyStats();
    this._pongCount = 0;
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
      this._startPinging();
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return; // malformed message — ignore, never crash the UI
      }
      if (msg.type === "pong") {
        this._onPong(msg.t);
        return;   // measurement-only — never forwarded to onMessage/reducer
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
      this._stopPinging();
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

  // ── RTT probe (item 3 audit) ─────────────────────────────────────────
  _startPinging() {
    this._stopPinging();
    this._pingTimer = setInterval(() => {
      if (this._ws?.readyState === WebSocket.OPEN) {
        this._ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
      }
    }, PING_INTERVAL_MS);
  }

  _stopPinging() {
    clearInterval(this._pingTimer);
    this._pingTimer = null;
  }

  _onPong(sentAt) {
    if (typeof sentAt !== "number") return;
    const rtt = Date.now() - sentAt;
    this._rtt.record(rtt);
    this._pongCount += 1;
    if (this._pongCount % PING_LOG_EVERY_N === 0) {
      const s = this._rtt.summary();
      console.info(
        `[Sarana] browser<->server RTT: avg=${s.avg.toFixed(0)}ms ` +
        `p50=${s.p50}ms p95=${s.p95}ms max=${s.max}ms (n=${s.count})`
      );
    }
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

  /** Send a browser-submitted image into the SAME live conversation —
   * the server injects it into the existing Gemini Live session as an
   * inline_data part alongside `text` (see main.py's
   * _process_dashboard_image_commands()). `base64` must NOT include the
   * "data:image/...;base64," prefix — strip it before calling this. */
  sendImage(base64, mimeType, text) {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({
        type: "image_command", data: base64, mime_type: mimeType, text,
      }));
      return true;
    }
    return false;
  }

  close() {
    this._closedByUser = true;
    clearTimeout(this._reconnectTimer);
    this._stopPinging();
    this._ws?.close();
  }
}
