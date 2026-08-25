// src/components/SidePanel.jsx — hamburger-menu drawer (item 4). Holds the
// Activity Log (the existing LogPanel component, unchanged, fed the same
// `messages` state AssistantContext already tracks — no second conversation
// store) and a Settings placeholder. Scrolls internally; never grows the
// main page (see index.css's .side-panel — its own fixed-height flex column).
import { useState } from "react";
import LogPanel from "./LogPanel";

export default function SidePanel({ open, onClose, messages }) {
  const [tab, setTab] = useState("activity"); // "activity" | "settings"

  return (
    <>
      <div
        className={`side-panel-backdrop ${open ? "open" : ""}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside className={`side-panel ${open ? "open" : ""}`} aria-hidden={!open}>
        <div className="side-panel-hdr">
          <div className="side-panel-tabs">
            <button
              type="button"
              className={`side-panel-tab ${tab === "activity" ? "active" : ""}`}
              onClick={() => setTab("activity")}
            >
              Activity Log
            </button>
            <button
              type="button"
              className={`side-panel-tab ${tab === "settings" ? "active" : ""}`}
              onClick={() => setTab("settings")}
            >
              Settings
            </button>
          </div>
          <button type="button" className="side-panel-close" onClick={onClose} aria-label="Close menu">
            ✕
          </button>
        </div>
        <div className="side-panel-body">
          {tab === "activity" ? (
            <LogPanel messages={messages} />
          ) : (
            <div className="settings-placeholder">
              <p>Settings will be configured here in a future update.</p>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
