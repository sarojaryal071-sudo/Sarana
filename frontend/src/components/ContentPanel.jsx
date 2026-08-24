export default function ContentPanel({ content, onDismiss }) {
  if (!content) return null;
  return (
    <div className="content-panel">
      <div className="content-hdr">
        <span className="title">{content.title}</span>
        <div className="spacer" style={{ flex: 1 }} />
        <button className="btn" onClick={onDismiss}>
          DISMISS ✕
        </button>
      </div>
      <div className="content-body">{content.text}</div>
    </div>
  );
}
