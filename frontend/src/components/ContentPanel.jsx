// src/components/ContentPanel.jsx — the mount point for Track 3's
// Presentation Engine, now the JARVIS Information Surface. Unchanged
// from the caller's point of view beyond the new expand/persistent
// pair — still just passed straight through, never re-derived here
// (this component owns no state of its own by design).
import PresentationSurface from "./presentation/PresentationSurface";

export default function ContentPanel({ content, theme, expanded, persistent, onDismiss, onSetExpanded }) {
  if (!content) return null;
  return (
    <PresentationSurface
      content={content}
      theme={theme}
      expanded={expanded}
      persistent={persistent}
      onDismiss={onDismiss}
      onSetExpanded={onSetExpanded}
    />
  );
}
