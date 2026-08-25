// src/components/SidePanel.jsx — hamburger drawer: a vertical nav list
// (Activity Log, Settings) with Logout + a footer credit pinned to the
// bottom, not a horizontal tab bar. Selecting a nav item swaps the drawer's
// own body to that view (still the same slide-in overlay/backdrop — see
// index.css's .side-panel — never the main page) with a back arrow to
// return to the nav list; the existing close button/backdrop-click still
// closes the whole drawer from any view. Activity Log reuses LogPanel/the
// existing `messages` state unchanged — no second conversation store.
import { useEffect, useState } from "react";
import LogPanel from "./LogPanel";

const VIEW_TITLES = {
  activity: "Activity Log",
  settings: "Settings",
};

export default function SidePanel({ open, onClose, messages, onLogout }) {
  const [view, setView] = useState("menu"); // "menu" | "activity" | "settings"

  // Reset back to the nav list after the drawer finishes closing, so it
  // never reopens showing whatever sub-view was last visible.
  useEffect(() => {
    if (open) return;
    const t = setTimeout(() => setView("menu"), 200);
    return () => clearTimeout(t);
  }, [open]);

  return (
    <>
      <div
        className={`side-panel-backdrop ${open ? "open" : ""}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside className={`side-panel ${open ? "open" : ""}`} aria-hidden={!open}>
        <div className="side-panel-hdr">
          {view === "menu" ? (
            <span className="side-panel-title">SARANA</span>
          ) : (
            <button
              type="button"
              className="side-panel-back"
              onClick={() => setView("menu")}
            >
              ← {VIEW_TITLES[view]}
            </button>
          )}
          <button type="button" className="side-panel-close" onClick={onClose} aria-label="Close menu">
            ✕
          </button>
        </div>

        <div className="side-panel-body">
          {view === "menu" && (
            <nav className="side-panel-nav">
              <button type="button" className="side-panel-nav-item" onClick={() => setView("activity")}>
                Activity Log
              </button>
              <button type="button" className="side-panel-nav-item" onClick={() => setView("settings")}>
                Settings
              </button>
            </nav>
          )}
          {view === "activity" && <LogPanel messages={messages} />}
          {view === "settings" && (
            <div className="settings-placeholder">
              <p>Settings will be configured here in a future update.</p>
            </div>
          )}
        </div>

        {view === "menu" && (
          <div className="side-panel-footer">
            <button type="button" className="side-panel-logout" onClick={onLogout}>
              Logout
            </button>
            <p className="side-panel-credit">Developed by Saroj</p>
          </div>
        )}
      </aside>
    </>
  );
}
