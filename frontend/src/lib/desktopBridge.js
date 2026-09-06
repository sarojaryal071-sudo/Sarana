// src/lib/desktopBridge.js — the ONE platform-specific transport adapter
// for the desktop-embedded Presentation Engine entry (desktop-presentation-
// main.jsx). Deliberately NOT a second event system: it receives the exact
// same message shapes dashboard/server.py's /ws already sends a real
// browser client (see DashboardServer.set_local_sink()'s own docstring in
// dashboard/server.py, and ui.py's JarvisUI.receive_dashboard_message(),
// which pushes each one into window.__jarvisBridge.receive(msg) via
// QWebEngineView.page().runJavaScript()) and dispatches into the SAME
// AssistantContext reducer App.jsx already uses — only a scoped subset of
// message types, since this view has no mic/camera/vision/image-upload
// concerns of its own (those stay native/Python on desktop). Mirrors
// lib/websocket.js's own onMessage switch one-for-one for the cases it
// actually handles — never a re-derivation of what a message means.
import { playAudioFx } from "./audioFx.js";

let _dispatch = null;
let _presentationBridge = null;   // set once the QWebChannel handshake completes

/** Installs the bridge and starts routing messages into `dispatch`. Safe
 * to call once, when the root component mounts. Also kicks off the
 * (best-effort, async) QWebChannel handshake for the ONE bidirectional
 * signal this integration needs — see setPresentationFocus() below and
 * ui.py's own _PresentationBridge docstring. */
export function installDesktopBridge(dispatch) {
  _dispatch = dispatch;
  window.__jarvisBridge = { receive: handleMessage };
  loadQWebChannelScript()
    .then(() => {
      // eslint-disable-next-line no-undef
      new QWebChannel(window.qt.webChannelTransport, (channel) => {
        _presentationBridge = channel.objects.presentationBridge;
      });
    })
    .catch(() => {
      // No real target (e.g. this bundle loaded outside a QWebEngineView
      // during local iteration) — setPresentationFocus() below just
      // stays a no-op, never a crash.
    });
}

/** Loads Qt's own qtwebchannel.js resource at RUNTIME (not as a static
 * <script> tag in desktop.html — Vite's build reorders/hoists a
 * non-module <script src>, which broke the load order this needs; see
 * that file's own comment). Always present once PyQt6-WebEngine is
 * installed — ui.py's own QWebChannel usage is what serves it. */
function loadQWebChannelScript() {
  return new Promise((resolve, reject) => {
    if (window.QWebChannel) { resolve(); return; }
    const script = document.createElement("script");
    script.src = "qrc:///qtwebchannel/qwebchannel.js";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("qwebchannel.js unavailable"));
    document.head.appendChild(script);
  });
}

/** Cinematic presentation focus: tells native Qt whether a presentation
 * is currently the visual focus, so the orb/face can defocus — the same
 * contract App.jsx's own .identity-stage-defocused class implements on
 * the web (see index.css). A silent no-op until the QWebChannel
 * handshake above completes (a presentation showing up before that
 * finishes just means the native dim arrives a beat late, never an
 * error) — see DesktopPresentationRoot's own effect for the call site. */
export function setPresentationFocus(active) {
  _presentationBridge?.setPresentationActive(!!active);
}

function handleMessage(msg) {
  if (!_dispatch || !msg || typeof msg !== "object") return;
  switch (msg.type) {
    case "content":
      // Track 3 (Presentation Engine) — the actual point of this whole
      // bridge: the same structured payload PresentationSurface already
      // knows how to render on the web.
      _dispatch({ type: "CONTENT_MESSAGE", title: msg.title, text: msg.text, presentation: msg.presentation ?? null });
      break;
    case "status":
      // Drives useAudioFxLifecycle's wake/sleep/listening/thinking cues —
      // see that hook, shared verbatim with App.jsx.
      _dispatch({ type: "STATUS_MESSAGE", state: msg.state });
      break;
    case "jarvis_mode_changed":
      _dispatch({ type: "JARVIS_MODE", value: msg.active });
      break;
    case "audio_cue":
      // Shared semantic audio event origin — main.py's own
      // _execute_tool() choke point already decided this; the exact same
      // event name a browser tab receives through the identical
      // broadcast (see dashboard/server.py's broadcast_audio_cue() and
      // App.jsx's own "audio_cue" case). Never re-derived from message
      // text here.
      playAudioFx(msg.event);
      break;
    case "log":
    case "sys":
      // This view has no transcript/Activity Log of its own (desktop's
      // native Qt HUD already has one, fed directly by write_log()) —
      // nothing further to do with these here now that audio cues are
      // authoritative (see the "audio_cue" case above).
      break;
    default:
      // camera_vision_request/location_refresh_request/image_error/etc.
      // are browser-API-only concerns with no meaning for this scoped
      // presentation+audio view — desktop already has native equivalents
      // for location/vision. Deliberately silent, never a console error.
      break;
  }
}
