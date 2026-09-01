// src/lib/audioOut.js — consumer for the existing /ws/audio-out endpoint
// (Phase 4). The backend streams raw PCM16 mono @ 24000 Hz — the exact
// same bytes main.py's _play_audio() writes to the local speaker, fanned
// out unchanged (see dashboard/server.py's broadcast_audio()). This class
// only turns those bytes into sound in the browser; it never talks back
// to _play_audio() or affects local playback in any way.

import { wsBaseUrl } from "./api";

const SAMPLE_RATE = 24000; // matches main.py's RECEIVE_SAMPLE_RATE
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export class AudioOutPlayer {
  /**
   * @param {string} token
   * @param {(state: "connecting"|"open"|"closed"|"error") => void} onState
   * @param {(level: number) => void} [onActivity] - called once per chunk
   *   scheduled for playback with that chunk's own RMS amplitude (0..1,
   *   computed from the same PCM samples about to play — real playback
   *   amplitude, not a synthetic/random value), and once more with 0 when
   *   the queue drains or playback is stopped. A caller can use either the
   *   fact of a call ("currently speaking") or the level itself (e.g. to
   *   drive SaranaFace's mouth via lib/mouthLevel.js — see App.jsx).
   */
  constructor(token, onState, onActivity) {
    this._token = token;
    this._onState = onState;
    this._onActivity = onActivity;
    this._ws = null;
    this._closedByUser = false;
    this._attempt = 0;
    this._reconnectTimer = null;

    this._ctx = null;
    this._nextStartTime = 0;
    this._activeSources = []; // scheduled/playing AudioBufferSourceNodes — see stopPlayback()
  }

  connect() {
    this._closedByUser = false;
    this._open();
  }

  _ensureContext() {
    if (!this._ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this._ctx = new Ctx({ sampleRate: SAMPLE_RATE });
      this._nextStartTime = this._ctx.currentTime;
    }
    if (this._ctx.state === "suspended") {
      this._ctx.resume().catch(() => {});
    }
    return this._ctx;
  }

  _open() {
    this._onState?.("connecting");
    const url = `${wsBaseUrl()}/ws/audio-out?token=${encodeURIComponent(this._token)}`;
    let ws;
    try {
      ws = new WebSocket(url);
    } catch {
      this._onState?.("error");
      this._scheduleReconnect();
      return;
    }
    ws.binaryType = "arraybuffer";
    this._ws = ws;

    ws.onopen = () => {
      this._attempt = 0;
      this._onState?.("open");
    };
    ws.onclose = () => {
      this._onState?.("closed");
      if (!this._closedByUser) this._scheduleReconnect();
    };
    ws.onerror = () => this._onState?.("error");
    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) this._playChunk(event.data);
    };
  }

  _playChunk(buf) {
    const ctx = this._ensureContext();

    const int16 = new Int16Array(buf);
    const float32 = new Float32Array(int16.length);
    let sumSq = 0;
    for (let i = 0; i < int16.length; i++) {
      const s = int16[i] / 32768;
      float32[i] = s;
      sumSq += s * s;
    }
    // Real playback amplitude for this exact chunk (RMS of the samples
    // about to play, not a smoothed/predictive estimate) — cheap, no FFT,
    // computed once per chunk rather than polled. clamp01(rms * gain): PCM
    // speech RMS sits well under 1.0, so a modest gain keeps normal speech
    // in a visually useful 0..1 range without needing per-user calibration.
    const rms = Math.sqrt(sumSq / int16.length);
    const level = Math.min(1, rms * 3.2);
    // Requires a user gesture to have unlocked audio in most browsers —
    // the mic/interrupt buttons and the "Connect" action both satisfy this
    // before any audio actually needs to play.
    this._onActivity?.(level);

    const audioBuffer = ctx.createBuffer(1, float32.length, SAMPLE_RATE);
    audioBuffer.copyToChannel(float32, 0);

    const src = ctx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(ctx.destination);
    src.onended = () => {
      const i = this._activeSources.indexOf(src);
      if (i !== -1) this._activeSources.splice(i, 1);
      // Queue fully drained (nothing left scheduled/playing) — report
      // silence so the mouth returns to resting rather than holding
      // whatever amplitude the last chunk happened to end on.
      if (this._activeSources.length === 0) this._onActivity?.(0);
    };
    this._activeSources.push(src);

    // Schedule chunks back-to-back so gaps between WS messages don't turn
    // into audible gaps in speech, but never schedule into the past.
    const startAt = Math.max(this._nextStartTime, ctx.currentTime);
    src.start(startAt);
    this._nextStartTime = startAt + audioBuffer.duration;
  }

  /** Item 2 (interrupt control): stop everything already scheduled/playing
   * in the browser and drop the schedule cursor back to "now" — the
   * server-side interrupt (see api.js's sendInterrupt) stops new audio at
   * the source, but any chunks already sent to the browser before that
   * lands would otherwise keep playing out. Does not close the socket —
   * playback of the *next* response still works normally afterward. */
  stopPlayback() {
    for (const src of this._activeSources) {
      try {
        src.stop();
      } catch {
        /* already stopped/ended */
      }
    }
    this._activeSources = [];
    if (this._ctx) this._nextStartTime = this._ctx.currentTime;
    this._onActivity?.(0); // interrupted mid-speech — mouth returns to resting now, not at the next chunk
  }

  _scheduleReconnect() {
    if (this._closedByUser) return;
    clearTimeout(this._reconnectTimer);
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this._attempt, RECONNECT_MAX_MS);
    this._attempt += 1;
    this._reconnectTimer = setTimeout(() => this._open(), delay);
  }

  close() {
    this._closedByUser = true;
    clearTimeout(this._reconnectTimer);
    this._ws?.close();
  }
}
