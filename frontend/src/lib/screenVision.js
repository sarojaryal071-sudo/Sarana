// src/lib/screenVision.js — SARANA Web Screen Vision (Phase 4):
// getDisplayMedia() lifecycle + adaptive frame sampling for an ACTIVE
// backend screen-vision request (see dashboard/server.py's
// "screen_vision_request"/"screen_vision_stop"/"vision_frame" WS
// messages and main.py's web_screen_vision tool /
// _process_web_vision_frames()).
//
// Deliberately a SEPARATE capability from lib/cameraVision.js — screen
// sharing and the physical camera are different visual sources with
// different purposes (see main.py's web_camera_vision vs
// web_screen_vision tool descriptions) and different browser APIs; this
// file never touches getUserMedia, and cameraVision.js never touches
// getDisplayMedia. Also deliberately separate from desktop's
// screen_process (an entirely different, OS-level mechanism).
//
// The share only ever runs while a request is active — started here
// ONLY from components/VisionStage.jsx, itself only ever mounted by
// App.jsx in direct response to a real "screen_vision_request" from the
// backend. Never idle, never speculative, never started on page load.
//
// IMPORTANT MOBILE LIMITATION: getDisplayMedia() is not available at all
// on iOS Safari (no full-device screen capture API exists there as of
// this writing) — isScreenShareSupported() below is the single, honest
// capability check every caller must use before attempting this; when
// it's false, main.py's own [VISION_UNAVAILABLE]/honest-fallback text is
// what actually reaches the user (see VisionStage.jsx), never a faked
// success.

const SAMPLE_INTERVAL_MS = 1200;  // slower than camera's 900ms — screen content
                                   // changes less often than a hand-held object
const MAX_DIM             = 1280; // screens/text need more resolution than the
                                   // camera's quick-recognition 800px cap
const JPEG_QUALITY         = 0.75;

let _stream = null;
let _video = null;        // detached, never-mounted <video> — sampling source only
let _canvas = null;
let _sampleTimer = null;
let _seq = 0;
let _activeRequestId = null;
let _onFrame = null;      // (base64, mimeType, seq) => void
let _onStatus = null;     // (status) => void — "starting"|"streaming"|"denied"|"unavailable"|"stopped"

function _setStatus(s) {
  try { _onStatus?.(s); } catch { /* a UI callback failing must never break capture */ }
}

/** Honest capability check — callers must gate on this before attempting
 * startScreenVision(), rather than letting an unsupported browser fail
 * silently or fake success. */
export function isScreenShareSupported() {
  return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getDisplayMedia;
}

function _captureFrame() {
  if (!_video || !_canvas || !_activeRequestId) return;
  const vw = _video.videoWidth, vh = _video.videoHeight;
  if (!vw || !vh) return;

  const scale = Math.min(1, MAX_DIM / Math.max(vw, vh));
  const w = Math.max(1, Math.round(vw * scale));
  const h = Math.max(1, Math.round(vh * scale));
  _canvas.width = w;
  _canvas.height = h;
  const ctx = _canvas.getContext("2d");
  if (!ctx) return;
  ctx.drawImage(_video, 0, 0, w, h);

  _canvas.toBlob(
    (blob) => {
      if (!blob || !_activeRequestId) return;
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = String(reader.result || "");
        const base64 = dataUrl.split(",")[1] || "";
        if (base64) _onFrame?.(base64, "image/jpeg", _seq++);
      };
      reader.readAsDataURL(blob);
    },
    "image/jpeg",
    JPEG_QUALITY,
  );
}

/**
 * Starts screen sharing and begins sampling for `requestId`. Must only
 * be called from inside a real user gesture (browsers require this for
 * getDisplayMedia unconditionally, not just on iOS) — VisionStage.jsx
 * gates every call behind an explicit "Share Screen" tap; there is no
 * "already granted, start silently" path for screen sharing the way
 * there is for camera/mic (browsers do not persist display-capture
 * consent — the native picker appears every time, by design, for
 * privacy). `onFrame`/`onStatus` behave identically to
 * cameraVision.js's startCameraVision(). Resolves true once actually
 * streaming, false on any failure or when unsupported.
 */
export async function startScreenVision(requestId, { onFrame, onStatus } = {}) {
  if (_activeRequestId === requestId && _stream) return true;
  if (_activeRequestId) stopScreenVision();

  _onFrame = onFrame || null;
  _onStatus = onStatus || null;
  _setStatus("starting");

  if (!isScreenShareSupported()) {
    _setStatus("unavailable");
    return false;
  }

  try {
    _stream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: false,   // never capture system/tab audio — visual observation only
    });
  } catch (e) {
    // NotAllowedError: user dismissed the native picker or denied it.
    // Anything else: treat as a genuine unavailability rather than guess.
    _setStatus(e?.name === "NotAllowedError" ? "denied" : "unavailable");
    return false;
  }

  _activeRequestId = requestId;
  _seq = 0;

  _video = document.createElement("video");
  _video.playsInline = true;
  _video.muted = true;
  _video.srcObject = _stream;
  try { await _video.play(); } catch { /* autoplay quirks — sampling still starts once frames flow */ }

  _canvas = document.createElement("canvas");
  _sampleTimer = setInterval(_captureFrame, SAMPLE_INTERVAL_MS);

  // The browser's OWN "stop sharing" control (shown natively in the tab
  // bar/OS chrome) can end the share at any time outside this module's
  // control — the track's "ended" event is the only reliable signal for
  // that; treat it exactly like an explicit stop. getVideoTracks() is
  // optional-chained defensively (a real MediaStream always has it, but
  // this must never throw and abort an otherwise-successful start).
  const track = _stream.getVideoTracks?.()?.[0];
  if (track) {
    track.addEventListener("ended", () => {
      if (_activeRequestId === requestId) {
        stopScreenVision();
      }
    });
  }

  _setStatus("streaming");
  return true;
}

/** The live MediaStream for the current session, or null — components
 * wanting to DISPLAY the feed bind their own <video> element's
 * srcObject to this; the internal _video above is sampling-only and
 * never mounted. */
export function getStream() {
  return _stream;
}

export function activeRequestId() {
  return _activeRequestId;
}

/** Stops everything: shared-screen track, sampling timer, internal
 * video/canvas references. Idempotent — safe to call when nothing is
 * active. Always call this on unmount/logout/tab-hidden/explicit-stop;
 * there is no cleanup path that should leave a screen share running
 * unattended. */
export function stopScreenVision() {
  clearInterval(_sampleTimer);
  _sampleTimer = null;
  _stream?.getTracks().forEach((t) => t.stop());
  _stream = null;
  if (_video) {
    _video.srcObject = null;
    _video = null;
  }
  _canvas = null;
  _activeRequestId = null;
  _onFrame = null;
  _setStatus("stopped");
  _onStatus = null;
}
