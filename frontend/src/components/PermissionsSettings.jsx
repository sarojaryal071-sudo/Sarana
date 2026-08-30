// src/components/PermissionsSettings.jsx — the "Permissions" section of
// Settings: a small control-panel module for SARANA's device access.
//
// This file is PRESENTATION ONLY. All state ownership lives in
// permissionManager (lib/permissions.js) — this component reads its
// EFFECTIVE state (browser permission + SARANA's own enable preference,
// combined) and, on click, calls the exact same permissionManager.toggle()
// the main microphone button (App.jsx) calls. There is no second,
// independent on/off boolean here — see permissionManager's own docstring
// for why that matters. Renders PERMISSION_DEFS generically, so a future
// capability (camera, contacts, files, bluetooth, ...) only needs a new
// registry entry + one icon below, never a new layout.
//
// The switch is a REAL toggle now, not just a "(re-)request permission"
// trigger: granted+on -> click turns SARANA's OWN use of the capability
// off (no browser call at all); off (either reason) -> click turns it
// back on, which only touches the browser if the browser hasn't already
// decided. A TRUE browser denial is never rendered as a working switch —
// "Try again" (same toggle() call) is the only honest retry, see the
// `offByChoice` distinction below.
import { useEffect, useState } from "react";
import { PERMISSION_DEFS, permissionManager } from "../lib/permissions";

const STATE_TEXT = {
  granted: "Allowed",
  denied: "Access off",
  prompt: "Not enabled",
  unsupported: "Not available",
};
// Shown instead of STATE_TEXT.denied specifically when the BROWSER has
// granted the capability but SARANA's own preference is off — a
// different situation from a real browser denial, and one where "change
// your browser settings" guidance would be actively wrong (there's
// nothing to change there; the switch itself is all that's needed).
const TURNED_OFF_TEXT = "Turned off";

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
  // `state` here is the EFFECTIVE state (browser permission + SARANA's
  // own enable preference already combined by permissionManager) —
  // exactly what App.jsx's mic-auto-start/backend-sync code reads too.
  // No separate "is this switch on" boolean is kept in this component.
  const [state, setState] = useState(permissionManager.getCached(def.id) || null);
  const [requesting, setRequesting] = useState(false);

  useEffect(() => permissionManager.subscribe(def.id, setState), [def.id]);

  // Interaction fix: a permission that's already been decided (granted,
  // or — most commonly — already auto-requested elsewhere in the app,
  // e.g. the mic starting on connect / location requesting on login,
  // both well before Settings is ever opened) resolves permissionManager
  // .request() almost instantly, with no real OS dialog in between and
  // no resulting state CHANGE for _setState() to notify — so the click
  // was real and handled correctly, but produced nothing perceptible.
  // That reads as "the switch doesn't respond." Holding the busy/pressed
  // visual for a minimum stretch (matching this design's own ~260ms
  // transition timing, not invented) makes every click acknowledge
  // itself, without inventing a fake state change of any kind — the
  // eventual on/off/status shown afterward is still exactly whatever
  // permissionManager actually reports.
  const MIN_FEEDBACK_MS = 260;

  async function invokeRequest() {
    if (requesting) return;
    setRequesting(true);
    const startedAt = Date.now();
    try {
      // The single click-semantics both this switch and the main mic
      // button (App.jsx) call — granted+on turns SARANA's own use off
      // (a local preference flip, no browser call); anything else tries
      // to turn it on, which only touches the browser if it hasn't
      // already decided. See permissionManager.toggle()'s own docstring.
      await permissionManager.toggle(def.id);
    } finally {
      const elapsed = Date.now() - startedAt;
      if (elapsed < MIN_FEEDBACK_MS) {
        await new Promise((resolve) => setTimeout(resolve, MIN_FEEDBACK_MS - elapsed));
      }
      setRequesting(false);
    }
  }

  const on = state === "granted";
  // Raw browser permission, read fresh on every render — only needed to
  // tell apart the two different reasons a capability can show as "off":
  // the browser genuinely refusing it, vs. SARANA's own preference being
  // off while the browser would otherwise allow it. `state` (effective)
  // alone can't distinguish these; permissionManager.getEffectiveState()
  // collapses both to the same "denied"-shaped value on purpose (to the
  // rest of the app, both really are "not usable right now").
  const browserState = permissionManager.getBrowserState(def.id);
  const offByChoice = browserState === "granted" && !on;
  // The switch itself is a real, working control whenever it can
  // actually DO something honest: turn SARANA's own use on/off while the
  // browser already allows it (no browser call either direction), or
  // attempt a not-yet-decided permission. A TRUE browser denial/
  // unsupported never becomes a fake switch that pretends to flip the
  // OS/browser setting — it renders inert, and (when genuinely denied)
  // a plain "Try again" button below does the actual honest retry.
  const switchIsLive = on || offByChoice || state === "prompt";
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
          {state ? (offByChoice ? TURNED_OFF_TEXT : STATE_TEXT[state]) : "Reading…"}
        </span>

        {state === "denied" && !offByChoice && (
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
