// src/components/ContentPanel.jsx — the mount point for Track 3's
// Presentation Engine. Unchanged from the caller's point of view (still
// just `content`/`onDismiss`) — `theme` is the one new prop, passed
// straight through from App.jsx's own already-computed `identity`
// ("jarvis" | "sarana", the SAME value IdentityTransition/Orb/SaranaFace
// already use — see App.jsx's own crossfade effect), never a second
// theme source.
import PresentationSurface from "./presentation/PresentationSurface";

export default function ContentPanel({ content, theme, onDismiss }) {
  if (!content) return null;
  return <PresentationSurface content={content} theme={theme} onDismiss={onDismiss} />;
}
