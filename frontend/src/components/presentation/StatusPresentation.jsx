// src/components/presentation/StatusPresentation.jsx — "what JARVIS is
// currently doing" (section 6F). `data`: {label: str, state:
// "in_progress"|"done"|"failed"|"blocked", progress?: 0..1}. `progress`
// is optional — an indeterminate task (most of them, honestly — this
// project's own Result Envelope has no notion of "% complete") just
// shows the state pill with no bar.
const STATE_LABELS = {
  in_progress: "IN PROGRESS",
  done: "DONE",
  failed: "FAILED",
  blocked: "BLOCKED",
};

export default function StatusPresentation({ data }) {
  const label = data?.label || "";
  const state = data?.state || "in_progress";
  const hasProgress = typeof data?.progress === "number" && Number.isFinite(data.progress);
  const pct = hasProgress ? Math.max(0, Math.min(1, data.progress)) * 100 : null;

  return (
    <div className="pw-status">
      <div className="pw-status-row pw-reveal-item">
        <span className={`pw-status-pill pw-status-pill-${state}`}>{STATE_LABELS[state] || state.toUpperCase()}</span>
        {label && <span className="pw-status-label">{label}</span>}
      </div>
      {hasProgress && (
        <div className="pw-status-bar-track pw-reveal-item" style={{ "--pw-reveal-index": 1 }}>
          <div className="pw-status-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}
