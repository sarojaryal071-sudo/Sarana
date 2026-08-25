// src/lib/mic.js — microphone capture over the existing /ws/phone-audio
// endpoint. This is NOT a new input channel: it's the exact same one
// dashboard/static/app.html's doMic() already streams to (16 kHz mono
// PCM16, via an AudioWorklet), reused here rather than duplicated with a
// different shape. main.py's _relay_phone_audio() forwards these chunks
// into the same Gemini Live session the desktop mic feeds — one brain,
// two mic sources, never both live at once (see _phone_active in main.py).

import { wsBaseUrl } from "./api";

const TARGET_RATE = 16000; // matches main.py's SEND_SAMPLE_RATE
const WORKLET_SRC =
  "class J extends AudioWorkletProcessor{process(i){const c=i[0]?.[0];if(c)this.port.postMessage(c.slice());return true;}}registerProcessor('j',J);";

function floatToPcm16(f32, srcRate) {
  let s = f32;
  if (srcRate !== TARGET_RATE) {
    const ratio = srcRate / TARGET_RATE;
    const len = Math.round(f32.length / ratio);
    s = new Float32Array(len);
    for (let i = 0; i < len; i++) {
      s[i] = f32[Math.min(Math.round(i * ratio), f32.length - 1)];
    }
  }
  const out = new Int16Array(s.length);
  for (let i = 0; i < s.length; i++) {
    out[i] = Math.max(-32768, Math.min(32767, Math.round(s[i] * 32768)));
  }
  return out;
}

export class MicStreamer {
  /**
   * @param {string} token
   * @param {(state: "idle"|"requesting"|"denied"|"unsupported"|"streaming"|"error") => void} onState
   */
  constructor(token, onState) {
    this._token = token;
    this._onState = onState;
    this._ws = null;
    this._ctx = null;
    this._stream = null;
    this._node = null;
    this._active = false;
  }

  get active() {
    return this._active;
  }

  async start() {
    if (this._active) return;

    if (!navigator.mediaDevices?.getUserMedia) {
      this._onState?.("unsupported");
      return;
    }

    this._onState?.("requesting");
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (e) {
      this._onState?.(e?.name === "NotAllowedError" ? "denied" : "error");
      return;
    }
    this._stream = stream;

    let ctx;
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_RATE });
    } catch {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    this._ctx = ctx;

    const url = `${wsBaseUrl()}/ws/phone-audio?token=${encodeURIComponent(this._token)}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this._ws = ws;

    ws.onclose = () => this.stop();
    ws.onerror = () => this._onState?.("error");

    ws.onopen = async () => {
      const rate = ctx.sampleRate;
      const src = ctx.createMediaStreamSource(stream);

      try {
        const blobUrl = URL.createObjectURL(
          new Blob([WORKLET_SRC], { type: "application/javascript" })
        );
        await ctx.audioWorklet.addModule(blobUrl);
        URL.revokeObjectURL(blobUrl);
        const node = new AudioWorkletNode(ctx, "j");

        // Buffer to ~1024 samples (64 ms @ 16 kHz) before sending, matching
        // the desktop mic's own chunk size and avoiding server-side flooding.
        //
        // Perf audit (item 5): re-checked whether this can safely be
        // lowered. main.py's own local mic uses sd.InputStream(blocksize=
        // CHUNK_SIZE) with CHUNK_SIZE=1024 at SEND_SAMPLE_RATE=16000 —
        // i.e. desktop's own native microphone chunking is ALSO 64ms.
        // This value isn't a web-specific inefficiency to shrink; it's
        // already matched to the same granularity the rest of the
        // pipeline (out_queue → _send_realtime() → Gemini) already
        // operates at on desktop. Shrinking it here would only add more,
        // smaller WebSocket frames without reducing the actual floor
        // latency anywhere downstream — left unchanged.
        let pending = [];
        let pendingLen = 0;
        node.port.onmessage = (e) => {
          const chunk = floatToPcm16(e.data, rate);
          pending.push(chunk);
          pendingLen += chunk.length;
          if (pendingLen >= 1024) {
            const out = new Int16Array(pendingLen);
            let off = 0;
            for (const c of pending) {
              out.set(c, off);
              off += c.length;
            }
            if (ws.readyState === WebSocket.OPEN) ws.send(out.buffer);
            pending = [];
            pendingLen = 0;
          }
        };
        src.connect(node);
        this._node = node;
      } catch {
        // ScriptProcessor fallback — deprecated but universally supported.
        const sp = ctx.createScriptProcessor(4096, 1, 1);
        sp.onaudioprocess = (e) => {
          const chunk = floatToPcm16(e.inputBuffer.getChannelData(0), rate);
          if (ws.readyState === WebSocket.OPEN) ws.send(chunk.buffer);
        };
        src.connect(sp);
        sp.connect(ctx.destination); // required by some browsers to keep the node alive
        this._node = sp;
      }

      this._active = true;
      this._onState?.("streaming");
    };
  }

  stop() {
    this._active = false;
    try {
      this._node?.disconnect();
    } catch {
      /* already disconnected */
    }
    this._node = null;
    try {
      this._ws?.close();
    } catch {
      /* already closed */
    }
    this._ws = null;
    this._stream?.getTracks().forEach((t) => t.stop());
    this._stream = null;
    try {
      this._ctx?.close();
    } catch {
      /* already closed */
    }
    this._ctx = null;
    this._onState?.("idle");
  }
}
