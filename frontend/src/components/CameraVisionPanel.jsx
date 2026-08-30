// src/components/CameraVisionPanel.jsx — small floating live-camera
// preview for SARANA Web Live Camera Vision (see lib/cameraVision.js,
// main.py's web_camera_vision tool, dashboard/server.py's
// "camera_vision_request"/"camera_vision_stop" WS messages).
//
// Deliberately separate from the existing photo-upload picker
// (Controls.jsx's 📎/📷 buttons, lib/image.js) — this component never
// renders inside Controls and never touches that code path. Only ever
// mounted (requestId non-null) by App.jsx in direct response to a real
// backend request; unmounting always stops the camera (see the cleanup
// effect below), so there is no path that leaves the camera running with
// nothing on screen.
//
// Never fullscreen — a small fixed-position card, mobile/portrait-
// friendly, with an explicit Stop control and a minimize toggle.
import { useEffect, useRef, useState } from "react";
import { permissionManager } from "../lib/permissions";
import { startCameraVision, stopCameraVision, getStream } from "../lib/cameraVision";

const STATUS_COPY = {
  denied: "Camera access is off. You can allow it from Settings and ask again.",
  unavailable: "Camera isn't available right now — it may be busy or missing.",
  sarana_disabled: "Camera use is turned off in Settings.",
};

export default function CameraVisionPanel({ requestId, facing, onFrame, onStopped }) {
  const videoRef = useRef(null);
  // starting | streaming | prompt | denied | unavailable | sarana_disabled | stopped
  const [status, setStatus] = useState("starting");
  const [minimized, setMinimized] = useState(false);

  function attachPreview() {
    const stream = getStream();
    if (videoRef.current && stream) videoRef.current.srcObject = stream;
  }

  useEffect(() => {
    if (!requestId) return undefined;
    let cancelled = false;
    setMinimized(false);

    async function begin() {
      const ok = await startCameraVision(requestId, facing, {
        onFrame,
        onStatus: (s) => { if (!cancelled) setStatus(s); },
      });
      if (!cancelled && ok) attachPreview();
    }

    const effective = permissionManager.getEffectiveState("camera");
    if (effective === "granted") {
      begin();
    } else if (effective === "denied" && permissionManager.getBrowserState("camera") === "granted") {
      // Browser allows it, but the user turned SARANA's own use of it off
      // in Settings — a real, distinct state from a browser-level denial.
      setStatus("sarana_disabled");
    } else {
      // Never call getUserMedia without a real user gesture the first
      // time (required by iOS Safari, good practice everywhere) — show
      // the explicit prompt instead (see handleAllow below).
      setStatus("prompt");
    }

    return () => {
      cancelled = true;
      stopCameraVision();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId, facing]);

  async function handleAllow() {
    setStatus("starting");
    const result = await permissionManager.enable("camera");
    if (result !== "granted") {
      setStatus(result === "denied" ? "denied" : "unavailable");
      return;
    }
    const ok = await startCameraVision(requestId, facing, {
      onFrame,
      onStatus: setStatus,
    });
    if (ok) attachPreview();
  }

  function handleStop() {
    stopCameraVision();
    onStopped?.("user_stopped");
  }

  // iOS Safari (and most mobile browsers) suspend a camera stream when the
  // tab/app is backgrounded — continuing to hold it open there is neither
  // reliable nor something the user can see is happening. Stop cleanly and
  // tell the backend, rather than leaving a silently-dead stream running.
  useEffect(() => {
    if (!requestId) return undefined;
    function handleVisibility() {
      if (document.hidden) {
        stopCameraVision();
        onStopped?.("tab_hidden");
      }
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  if (!requestId) return null;

  const showVideo = status === "starting" || status === "streaming";
  const errorCopy = STATUS_COPY[status];

  return (
    <div className={`vision-panel${minimized ? " vision-panel-minimized" : ""}`}>
      <div className="vision-panel-header">
        <span className="vision-panel-title">
          {status === "streaming" ? "👁 SARANA is looking…" : "👁 SARANA's camera"}
        </span>
        <button
          type="button"
          className="vision-panel-min"
          onClick={() => setMinimized((m) => !m)}
          aria-label={minimized ? "Expand camera preview" : "Minimize camera preview"}
        >
          {minimized ? "▢" : "—"}
        </button>
      </div>
      {!minimized && (
        <div className="vision-panel-body">
          {status === "prompt" && (
            <div className="vision-panel-permission">
              <p>SARANA wants to look through your camera.</p>
              <button type="button" className="btn" onClick={handleAllow}>
                Allow Camera
              </button>
            </div>
          )}
          {errorCopy && <p className="vision-panel-error">{errorCopy}</p>}
          {showVideo && (
            <video ref={videoRef} className="vision-panel-video" autoPlay muted playsInline />
          )}
          {status !== "prompt" && (
            <button type="button" className="btn danger vision-panel-stop" onClick={handleStop}>
              Stop
            </button>
          )}
        </div>
      )}
    </div>
  );
}
