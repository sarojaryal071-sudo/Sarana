// src/components/ToolsRail.jsx — displays the tool list from GET /api/session
// (section 10: descriptions come from the backend, nothing duplicated here,
// nothing executed from the browser).
export default function ToolsRail({ tools }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
        overflowY: "auto",
        width: "100%",
      }}
    >
      <span style={{ fontSize: 9, color: "var(--text-dim)", letterSpacing: 1 }}>
        TOOLS
      </span>
      <span style={{ fontSize: 14, color: "var(--pri)", fontWeight: 700 }}>{tools.length}</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, overflowY: "auto" }}>
        {tools.map((t) => (
          <div
            key={t.name}
            title={`${t.name} — ${t.description}`}
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "var(--border-b)",
              cursor: "default",
            }}
          />
        ))}
      </div>
    </div>
  );
}
