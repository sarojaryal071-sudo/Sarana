// src/components/Controls.jsx — only surfaces controls the EXISTING protocol
// actually supports remotely: text command, mic streaming
// (/ws/phone-audio), and image submission (/ws "image_command" — see
// websocket.js/App.jsx's handleSendImage()). No WAKE button — as of Phase
// 9, logging in (POST /login/username) is itself the start signal (see
// dashboard/server.py's /login/username route), so there is nothing left
// for a WAKE button to do in the normal flow. The desktop's interrupt/mute
// buttons call local JarvisLive callbacks (on_interrupt, muted) that have
// no WebSocket/REST equivalent today — inventing one would mean wiring new
// dashboard->JarvisLive control paths, out of scope here (see Phase 6
// report, "problems discovered"). Better to omit than to ship a button that
// silently does nothing.
import { useRef, useState } from "react";

const MIC_LABELS = {
  idle: "🎤 MIC",
  requesting: "🎤 REQUESTING…",
  streaming: "🎤 LISTENING (TAP TO STOP)",
  denied: "🎤 MIC DENIED",
  unsupported: "🎤 UNSUPPORTED",
  error: "🎤 MIC ERROR",
};

export default function Controls({
  onSend,
  onSendImage,
  micState,
  onToggleMic,
  disabled,
  onInterrupt,
}) {
  const [text, setText] = useState("");
  // { file, previewUrl, source: "camera" | "file" } | null — a picked-but-
  // not-yet-sent image. Plain component state, not a new global/context:
  // it never outlives this one pending message, same lifetime as `text`
  // above. `source` only drives which buttons the preview row shows
  // (Retake only makes sense for a camera shot) — it never reaches
  // onSendImage/the backend, which only ever sees the File itself.
  const [pendingImage, setPendingImage] = useState(null);
  const fileInputRef   = useRef(null);
  const cameraInputRef = useRef(null);

  function pickImage(file, source) {
    if (!file) return;
    setPendingImage((prev) => {
      if (prev?.previewUrl) URL.revokeObjectURL(prev.previewUrl);
      return { file, previewUrl: URL.createObjectURL(file), source };
    });
  }

  function clearImage() {
    setPendingImage((prev) => {
      if (prev?.previewUrl) URL.revokeObjectURL(prev.previewUrl);
      return null;
    });
  }

  // Retake: same idea as picking a fresh camera photo, just one tap
  // instead of Remove-then-📷. Only offered for a camera-sourced image —
  // re-opens the SAME cameraInputRef input, no new/duplicate camera
  // mechanism.
  function retakePhoto() {
    clearImage();
    cameraInputRef.current?.click();
  }

  function submit(e) {
    e.preventDefault();
    if (disabled) return;
    const t = text.trim();
    if (pendingImage) {
      onSendImage?.(pendingImage.file, t);
      clearImage();
      setText("");
      return;
    }
    if (!t) return;
    onSend(t);
    setText("");
  }

  return (
    <div className="controls">
      {pendingImage && (
        <div className="image-preview-row">
          <img className="image-preview-thumb" src={pendingImage.previewUrl} alt="" />
          <span className="image-preview-hint">Ask about this image, or just send it as-is.</span>
          {pendingImage.source === "camera" && (
            <button
              type="button"
              className="btn image-preview-retake"
              onClick={retakePhoto}
              disabled={disabled}
              title="Retake photo"
            >
              ↻
            </button>
          )}
          <button
            type="button"
            className="btn danger image-preview-remove"
            onClick={clearImage}
            disabled={disabled}
            title="Remove image"
          >
            ✕
          </button>
        </div>
      )}
      <form className="text-input-row" onSubmit={submit}>
        {/* Upload: works identically on desktop/Android/iOS Safari — no
            capture attribute, so iOS itself offers Photo Library / Take
            Photo / Choose File from one native sheet. */}
        <input
          type="file"
          accept="image/*"
          ref={fileInputRef}
          className="visually-hidden-file-input"
          onChange={(e) => { pickImage(e.target.files?.[0], "file"); e.target.value = ""; }}
        />
        {/* Camera: capture="environment" is the standards-based hint that
            asks the browser to skip the picker and go straight to the
            native rear camera. It's a HINT, not a guarantee (WHATWG HTML
            explicitly allows a user agent to ignore it) — Android Chrome
            honors it reliably; iOS Safari's support is real but
            version-dependent and sometimes still shows its own action
            sheet (with "Take Photo" as an option) instead of jumping
            straight to the viewfinder. That's a platform limitation, not
            something fixable from here without a live in-page camera
            preview mechanism, which this feature deliberately avoids. */}
        <input
          type="file"
          accept="image/*"
          capture="environment"
          ref={cameraInputRef}
          className="visually-hidden-file-input"
          onChange={(e) => { pickImage(e.target.files?.[0], "camera"); e.target.value = ""; }}
        />
        <button
          type="button"
          className="btn image-attach-btn"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled}
          title="Attach a photo"
        >
          📎
        </button>
        <button
          type="button"
          className="btn image-attach-btn"
          onClick={() => cameraInputRef.current?.click()}
          disabled={disabled}
          title="Take a photo"
        >
          📷
        </button>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={pendingImage ? "Ask about this image… (optional)" : "Type a command…"}
          disabled={disabled}
        />
        <button
          className="btn"
          type="submit"
          disabled={disabled || (!pendingImage && !text.trim())}
        >
          ▸
        </button>
      </form>
      <div className="row">
        <button
          className={`btn primary ${micState === "streaming" ? "active" : ""}`}
          onClick={onToggleMic}
          disabled={disabled || micState === "requesting"}
          title={
            micState === "denied"
              ? "Microphone permission denied — allow it in your browser's site settings"
              : micState === "unsupported"
                ? "This browser does not support microphone capture"
                : undefined
          }
        >
          {MIC_LABELS[micState] || MIC_LABELS.idle}
        </button>
        <button
          className="btn danger interrupt-btn"
          onClick={onInterrupt}
          disabled={disabled}
          title="Stop SARANA and return to listening"
        >
          ✋ INTERRUPT
        </button>
      </div>
    </div>
  );
}
