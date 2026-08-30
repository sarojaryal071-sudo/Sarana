// src/components/PermissionsSettings.jsx — the "Permissions" section of
// Settings: a small control-panel module for SARANA's device access.
//
// This file is PRESENTATION ONLY. Every piece of state shown here still
// comes from permissionManager (lib/permissions.js) exactly as before —
// nothing about how permissions are queried/requested/stored changed, only
// how that truth is drawn. Renders PERMISSION_DEFS generically, so a
// future capability (camera, contacts, files, bluetooth, ...) only needs a
// new registry entry + one icon below, never a new layout.
//
// "Enable"/"Try again" still invoke the real browser permission-request
// flow (permissionManager.request()); a granted permission still has no
// "disable" control (browsers don't allow revoking it from a webpage), and
// a denied permission is never rendered as a working switch — see the
// switch's `disabled` logic below.
import { useEffect, useState } from "react";
import { PERMISSION_DEFS, permissionManager } from "../lib/permissions";

const STATE_TEXT = {
  granted: "Allowed",
  denied: "Access off",
  prompt: "Not enabled",
  unsupported: "Not available",
};

// Small hand-drawn line icons — no icon library, just inline SVG, kept
// deliberately minimal/technical to match the app's existing glyph-based
// visual language (◈ ◉ ● in Header/Orb/LoginScreen) rather than importing
// a whole icon set for two capabilities.
function MicGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0" />
      <path d="M12 17.5v3.2" />
      <path d="M8.6 20.7h6.8" />
    </svg>
  );
}

function LocationGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" strokeDasharray="1.6 3" opacity="0.55" />
      <path d="M12 6.4c-2.6 0-4.6 2-4.6 4.5 0 3.2 4.6 8.3 4.6 8.3s4.6-5.1 4.6-8.3c0-2.5-2-4.5-4.6-4.5z" />
      <circle cx="12" cy="10.8" r="1.5" fill="currentColor" stroke="none" />
    </svg>
  );
}

const GLYPHS = {
  microphone: MicGlyph,
  location: LocationGlyph,
};

// Generic fallback for any future PERMISSION_DEFS entry that hasn't
// earned its own glyph yet — keeps the registry genuinely extensible
// (see lib/permissions.js's own docstring) instead of crashing/blanking.
function GenericGlyph() {
  return <span className="perm-generic-glyph" aria-hidden="true">◈</span>;
}

function PermissionCard({ def }) {
  const [state, setState] = useState(permissionManager.getCached(def.id) || null);
  const [requesting, setRequesting] = useState(false);

  useEffect(() => permissionManager.subscribe(def.id, setState), [def.id]);

  async function invokeRequest() {
    if (requesting) return;
    setRequesting(true);
    try {
      await permissionManager.request(def.id);
    } finally {
      setRequesting(false);
    }
  }

  const on = state === "granted";
  // The switch itself is a real, working control only while it can
  // actually DO something honest: turn a not-yet-decided permission on,
  // or re-affirm an already-granted one (harmless — no second OS
  // prompt). A denied/unsupported permission never becomes a fake
  // switch that pretends to flip the OS/browser setting — it renders
  // inert, and (when denied) a plain "Try again" button below does the
  // actual honest retry instead.
  const switchIsLive = state === "granted" || state === "prompt";
  const Glyph = GLYPHS[def.id] || GenericGlyph;

  return (
    <div className={`perm-card perm-card-${state || "checking"}`}>
      <div className="perm-card-top">
        <div className="perm-card-icon">
          <Glyph />
        </div>
        <div className="perm-card-copy">
          <p className="perm-card-name">{def.name}</p>
          <p className="perm-card-desc">{def.description}</p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label={`${def.name} — ${on ? "on" : "off"}`}
          className={`perm-switch${on ? " is-on" : ""}${requesting ? " is-busy" : ""}`}
          onClick={invokeRequest}
          disabled={!switchIsLive || requesting}
        >
          <span className="perm-switch-track">
            <span className="perm-switch-knob" />
          </span>
        </button>
      </div>

      <div className="perm-card-foot">
        <span className={`perm-status perm-status-${state || "checking"}`}>
          <span className="perm-status-led" />
          {state ? STATE_TEXT[state] : "Reading…"}
        </span>

        {state === "denied" && (
          <div className="perm-denied-actions">
            <p className="perm-card-help">
              You'll need to allow this in your browser or device settings, then try again.
            </p>
            <button
              type="button"
              className="perm-retry-btn"
              onClick={invokeRequest}
              disabled={requesting}
            >
              {requesting ? "Retrying…" : "Try again"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function PermissionsSettings() {
  return (
    <div className="settings-permissions">
      <div className="settings-permissions-hdr">
        <span className="settings-permissions-kicker">Device Access</span>
        <p className="settings-permissions-label">Permissions</p>
        <p className="settings-permissions-sub">What SARANA can sense on this device.</p>
      </div>
      <div className="perm-grid">
        {PERMISSION_DEFS.map((def) => (
          <PermissionCard key={def.id} def={def} />
        ))}
      </div>
    </div>
  );
}
