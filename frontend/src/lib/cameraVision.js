// src/lib/cameraVision.js — SARANA Web Live Camera Vision: getUserMedia
// lifecycle + adaptive frame sampling for an ACTIVE backend vision
// request (see dashboard/server.py's "camera_vision_request"/
// "camera_vision_stop"/"vision_frame" WS messages and main.py's
// web_camera_vision tool / _process_web_vision_frames()).
//
// Deliberately separate from — and never touches — the existing
// photo-upload feature (lib/image.js, Controls.jsx's 📎/📷 picker): that
// path stays a plain <input type="file">, this path never opens it and
// is never opened by it. Also deliberately separate from desktop's
// screen_process (an entirely different, OS-level mechanism this file
// has no relationship to).
//
// The camera only ever runs while a request is active — started here
// ONLY from components/CameraVisionPanel.jsx, itself only ever mounted
// by App.jsx in direct response to a real "camera_vision_request" from
// the backend. Never idle, never speculative, never started on page
// load.

const SAMPLE_INTERVAL_MS = 900;   // ~1 frame/900ms — a short burst, not continuous video
const MAX_DIM             = 800;  // smaller than the deliberate photo-upload path (1600px,
                                   // see lib/image.js) — these are quick-recognition frames,
                                   // not document reads
const JPEG_QUALITY         = 0.7; // lower than the upload path's 0.85 — throwaway sampling frames
const MIN_AVG_BRIGHTNESS   = 8;   // 0-255 luma scale; below this a frame is treated as
                                   // essentially unusable (lens covered / pitch black) and
                                   // dropped locally instead of sent — a coarse TECHNICAL
                                   // filter only, never a "too dark to answer" judgment call
                                   // (Gemini itself makes every real quality call — see
                                   // main.py's [VISION_OBSERVATION] prompt text)

let _stream = null;
let _video = null;        // detached, never-mounted <video> — sampling source only
let _canvas = null;       // full-res capture canvas — building the JPEG that gets sent
let _probeCanvas = null;  // tiny fixed-size canvas — cheap brightness probe only
let _sampleTimer = null;
let _seq = 0;
let _activeRequestId = null;
let _onFrame = null;      // (base64, mimeType, seq) => void
let _onStatus = null;     // (status) => void — "starting"|"streaming"|"denied"|"unavailable"|"stopped"

function _setStatus(s) {
  try { _onStatus?.(s); } catch { /* a UI callback failing must never break capture */ }
}

/** Cheap, fixed-cost (16x16) technical-only filter: average luminance.
 * Never judges "good enough to answer" — only "obviously unusable" (near-
 * total darkness, most likely a covered lens or a camera still warming
 * up). Every real quality judgment (blur/distance/framing/glare) is
 * Gemini's, made from the actual frames it receives. */
function _isObviouslyUsable(video) {
  try {
    if (!_probeCanvas) {
      _probeCanvas = document.createElement("canvas");
      _probeCanvas.width = 16;
      _probeCanvas.height = 16;
    }
    const pctx = _probeCanvas.getContext("2d", { willReadFrequently: true });
    if (!pctx) return true;
    pctx.drawImage(video, 0, 0, 16, 16);
    const { data } = pctx.getImageData(0, 0, 16, 16);
    let sum = 0;
    for (let i = 0; i < data.length; i += 4) {
      sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    }
    const avg = sum / (data.length / 4);
    return avg >= MIN_AVG_BRIGHTNESS;
  } catch {
    // Never let an unreliable heuristic block sending — see this file's
    // own header note on the browser/Gemini division of responsibility.
    return true;
  }
}

function _captureFrame() {
  if (!_video || !_canvas || !_activeRequestId) return;
  const vw = _video.videoWidth, vh = _video.videoHeight;
  if (!vw || !vh) return;   // not yet playing / no frame available this tick
  if (!_isObviouslyUsable(_video)) return;

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
 * Starts the camera and begins sampling for `requestId`. Must only be
 * called from inside a real user gesture the FIRST time on a given
 * browser (iOS Safari requires this) — CameraVisionPanel.jsx enforces
 * that by gating the very first call behind an explicit "Allow Camera"
 * tap; once the browser has actually granted the permission, later calls
 * (even from a backend-initiated request, no fresh tap) work the same
 * way any already-granted getUserMedia call does.
 *
 * `onFrame(base64, mimeType, seq)` fires roughly every
 * SAMPLE_INTERVAL_MS while streaming (skipping obviously-unusable
 * frames — see _isObviouslyUsable()). `onStatus(status)` reports
 * lifecycle changes for the panel UI. Resolves true once actually
 * streaming, false on any failure (permission denied, no camera, etc.).
 */
export async function startCameraVision(requestId, facing, { onFrame, onStatus } = {}) {
  if (_activeRequestId === requestId && _stream) return true;
  if (_activeRequestId) stopCameraVision();

  _onFrame = onFrame || null;
  _onStatus = onStatus || null;
  _setStatus("starting");

  if (!navigator.mediaDevices?.getUserMedia) {
    _setStatus("unavailable");
    return false;
  }

  try {
    _stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: facing === "user" ? "user" : "environment" } },
      audio: false,
    });
  } catch (e) {
    // NotAllowedError: denied. NotFoundError/OverconstrainedError: no
    // usable camera. NotReadableError: camera busy/in use elsewhere.
    // All of these are genuine, distinct failures — none silently
    // swallowed; the panel surfaces one of two honest buckets.
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
  _setStatus("streaming");
  return true;
}

/** The live MediaStream for the current session, or null — components
 * wanting to DISPLAY the feed bind their own <video> element's
 * srcObject to this (a stream can back more than one <video> at once);
 * the internal _video above is sampling-only and never mounted. */
export function getStream() {
  return _stream;
}

export function activeRequestId() {
  return _activeRequestId;
}

/** Stops everything: camera tracks, sampling timer, internal video/canvas
 * references. Idempotent — safe to call when nothing is active. Always
 * call this on unmount/logout/tab-hidden/explicit-stop; there is no
 * cleanup path that should leave a MediaStream running unattended. */
export function stopCameraVision() {
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
