// src/desktop-presentation-main.jsx — the desktop-embedded Presentation
// Engine entry point (built as its own small bundle, loaded by a
// QWebEngineView inside ui.py's native Qt content panel — see that file's
// own header for the full architecture). Deliberately NOT App.jsx: this
// mounts none of the mic/camera/vision/image-upload/login machinery a
// browser session needs (desktop already has native equivalents for all
// of that) — only the ONE thing desktop genuinely lacked: a real renderer
// for the SAME structured Presentation Engine payloads main.py already
// produces, and the SAME cinematic audio vocabulary audioFx.js already
// defines. Reuses AssistantProvider/ContentPanel/audioFx.js/
// useAudioFxLifecycle verbatim — this file is transport glue, not a
// second Presentation Engine.
import { useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { AssistantProvider, useAssistantState, useAssistantDispatch } from "./state/AssistantContext";
import ContentPanel from "./components/ContentPanel";
import { installDesktopBridge, setPresentationFocus } from "./lib/desktopBridge";
import { useAudioFxLifecycle } from "./lib/useAudioFxLifecycle";
import { playAudioFx } from "./lib/audioFx";
import "./index.css";

function DesktopPresentationRoot() {
  const state = useAssistantState();
  const dispatch = useAssistantDispatch();

  useEffect(() => { installDesktopBridge(dispatch); }, [dispatch]);

  // Cinematic presentation focus (section 1-2 of the "cinematic
  // presentation experience" brief): tells native Qt to defocus the
  // orb/face while this panel's own content is non-null — the SAME
  // contract App.jsx implements on the web via .identity-stage-defocused.
  useEffect(() => { setPresentationFocus(!!state.content); }, [state.content]);

  const identity = state.jarvisMode ? "jarvis" : "sarana";

  // Shared wake/sleep/listening/thinking cues + theme sync + ducking —
  // the exact same hook App.jsx uses, reacting to the exact same
  // assistantStatus stream (delivered here via the bridge instead of a
  // real WebSocket — see desktopBridge.js).
  useAudioFxLifecycle(state.assistantStatus, identity);

  // Identity transition cue — mirrors App.jsx's own one-liner (see that
  // file's "Track 4: the mechanical-assembly cue" effect) against the
  // same backend-authoritative jarvisMode field. Desktop's native Qt HUD
  // owns the actual visual Orb/SaranaFace crossfade — this view only
  // needs the SOUND, not App.jsx's full deconstruct/rebuild timer state
  // machine (there is nothing visual here for it to choreograph).
  const prevJarvisModeRef = useRef(state.jarvisMode);
  useEffect(() => {
    const prev = prevJarvisModeRef.current;
    prevJarvisModeRef.current = state.jarvisMode;
    if (prev === state.jarvisMode) return;
    playAudioFx(state.jarvisMode ? "transition_sarana_to_jarvis" : "transition_jarvis_to_sarana", { force: true });
  }, [state.jarvisMode]);

  return (
    <div className="desktop-presentation-root">
      <ContentPanel
        content={state.content}
        theme={identity}
        expanded={state.presentationExpanded}
        persistent={state.presentationPersistent}
        onDismiss={() => dispatch({ type: "DISMISS_CONTENT" })}
        onSetExpanded={(value) => dispatch({ type: "PRESENTATION_EXPANDED", value })}
      />
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <AssistantProvider>
    <DesktopPresentationRoot />
  </AssistantProvider>
);
