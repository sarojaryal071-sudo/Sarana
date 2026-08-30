// src/components/VisionStage.jsx — live visual preview for SARANA's Web
// Visual Context System: camera (Phase 1/2/3) and screen (Phase 4) share
// this one thin UI shell, but never share a browser capture mechanism —
// `source === "camera"` drives lib/cameraVision.js (getUserMedia),
// `source === "screen"` drives lib/screenVision.js (getDisplayMedia).
// Renamed from CameraVisionPanel.jsx now that it covers both; the
// underlying lifecycle libraries stay in their own separate files (see
// each one's own header) — this component only decides which one to
// call and how to render its output, never mixes their mechanics.
//
// Renders INTO the exact same "orb-stage" slot Orb.jsx normally occupies
// (see App.jsx, which mounts exactly one of the two at a time) — the
// visual centerpiece replacement, never a second/separate floating box.
//
// Deliberately separate from the existing photo-upload picker
// (Controls.jsx's 📎/📷 buttons, lib/image.js) — this component never
// renders inside Controls and never touches that code path. Only ever
// mounted (requestId non-null) by App.jsx in direct response to a real
// backend request; unmounting always stops capture (see the cleanup
// effect below), so there is no path that leaves camera/screen capture
// running with nothing on screen. Stopping while mounted is done through
// the existing INTERRUPT control (App.jsx's handleInterrupt), not a
// dedicated Stop button here — no new control was added for that.
import { useEffect, useRef, useState } from "react";
import { permissionManager } from "../lib/permissions";
import {
  startCameraVision, stopCameraVision, getStream as getCameraStream,
  flipCameraFacing, currentFacing,
} from "../lib/cameraVision";
import {
  startScreenVision, stopScreenVision, getStream as getScreenStream,
  isScreenShareSupported,
} from "../lib/screenVision";

const STATUS_COPY = {
  denied: {
    camera: "Camera access is off. You can allow it from Settings and ask again.",
    screen: "Screen sharing wasn't allowed. Ask again when you're ready.",
  },
  unavailable: {
    camera: "Camera isn't available right now — it may be busy or missing.",
    screen: "Screen sharing isn't supported in this browser (this is common on iOS Safari).",
  },
  sarana_disabled: {
    camera: "Camera use is turned off in Settings.",
    screen: "Screen sharing is turned off in Settings.",
  },
};

export default function VisionStage({ source, requestId, facing, onFrame, onStopped }) {
  const videoRef = useRef(null);
  // starting | streaming | prompt | denied | unavailable | sarana_disabled | stopped
  const [status, setStatus] = useState("starting");
  const [flipBusy, setFlipBusy] = useState(false);
  const isCamera = source === "camera";

  function attachPreview() {
    const stream = isCamera ? getCameraStream() : getScreenStream();
    if (videoRef.current && stream) videoRef.current.srcObject = stream;
  }

  useEffect(() => {
    if (!requestId) return undefined;
    let cancelled = false;

    async function beginCamera() {
      const ok = await startCameraVision(requestId, facing, {
        onFrame,
        onStatus: (s) => { if (!cancelled) setStatus(s); },
      });
      if (!cancelled && ok) attachPreview();
    }

    if (isCamera) {
      const effective = permissionManager.getEffectiveState("camera");
      if (effective === "granted") {
        beginCamera();
      } else if (effective === "denied" && permissionManager.getBrowserState("camera") === "granted") {
        // Browser allows it, but the user turned SARANA's own use of it
        // off in Settings — a real, distinct state from a browser denial.
        setStatus("sarana_disabled");
      } else {
        // Never call getUserMedia without a real user gesture the first
        // time (required by iOS Safari, good practice everywhere) — show
        // the explicit prompt instead (see handleAllow below).
        setStatus("prompt");
      }
    } else {
      // Screen sharing: browsers never persist display-capture consent
      // (the native picker appears every time, by design) — there is no
      // "already granted, start silently" path the way camera has.
      // Always requires an explicit tap; check honest support first.
      setStatus(isScreenShareSupported() ? "prompt" : "unavailable");
    }

    return () => {
      cancelled = true;
      if (isCamera) stopCameraVision(); else stopScreenVision();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId, facing, source]);

  async function handleAllow() {
    setStatus("starting");
    if (isCamera) {
      const result = await permissionManager.enable("camera");
      if (result !== "granted") {
        setStatus(result === "denied" ? "denied" : "unavailable");
        return;
      }
      const ok = await startCameraVision(requestId, facing, { onFrame, onStatus: setStatus });
      if (ok) attachPreview();
    } else {
      const ok = await startScreenVision(requestId, { onFrame, onStatus: setStatus });
      if (ok) attachPreview();
    }
  }

  // Phase 1: camera flip — only meaningful for the camera source, only
  // while actually streaming. Never touches the request_id, the Gemini
  // session, or the WebSocket — see cameraVision.js's own docstring.
  async function handleFlip() {
    if (flipBusy) return;
    setFlipBusy(true);
    const result = await flipCameraFacing();
    if (result.ok) {
      attachPreview();   // the underlying MediaStream object changed — rebind it
    }
    // A failure (single_camera/denied/unavailable) leaves the current
    // stream untouched and running — nothing else to do here; a silent
    // no-op is the correct, non-disruptive behavior for "only one camera
    // on this device", the most common real-world failure case.
    setFlipBusy(false);
  }

  // iOS Safari (and most mobile browsers) suspend camera/screen capture
  // when the tab/app is backgrounded — continuing to hold it open there
  // is neither reliable nor something the user can see is happening.
  // Stop cleanly and tell the backend, rather than leaving a silently-
  // dead stream running.
  useEffect(() => {
    if (!requestId) return undefined;
    function handleVisibility() {
      if (document.hidden) {
        if (isCamera) stopCameraVision(); else stopScreenVision();
        onStopped?.("tab_hidden");
      }
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId, source]);

  if (!requestId) return null;

  const showVideo = status === "starting" || status === "streaming";
  const errorCopy = STATUS_COPY[status]?.[source];
  const promptCopy = isCamera
    ? "SARANA wants to look through your camera."
    : "SARANA wants to see your screen.";
  const allowLabel = isCamera ? "Allow Camera" : "Share Screen";

  return (
    <div className="orb-stage">
      {showVideo && (
        <video ref={videoRef} className="vision-stage-video" autoPlay muted playsInline />
      )}
      {status === "streaming" && (
        <span className="vision-stage-badge">
          {isCamera ? "👁 watching" : "🖥️ watching your screen"}
        </span>
      )}
      {status === "streaming" && isCamera && (
        <button
          type="button"
          className="vision-stage-flip"
          onClick={handleFlip}
          disabled={flipBusy}
          aria-label="Switch camera"
          title="Switch camera"
        >
          🔄
        </button>
      )}
      {status === "prompt" && (
        <div className="vision-stage-overlay">
          <p>{promptCopy}</p>
          <button type="button" className="btn" onClick={handleAllow}>
            {allowLabel}
          </button>
        </div>
      )}
      {errorCopy && (
        <div className="vision-stage-overlay">
          <p className="vision-stage-error">{errorCopy}</p>
        </div>
      )}
    </div>
  );
}
