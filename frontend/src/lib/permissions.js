// src/lib/permissions.js — centralized, extensible permission/capability
// registry for SARANA Web. This is the ONE place browser/OS permission
// state is ever read or requested from — every component queries this
// module instead of calling navigator.permissions/getUserMedia/
// getCurrentPosition directly, so there is exactly one source of truth
// for "is capability X actually available right now".
//
// Real platform state only — never a fake/local-only toggle. Every state
// this module reports comes from either:
//   1. The browser's own Permissions API (navigator.permissions.query),
//      when the browser supports querying that particular permission
//      name, or
//   2. The real, observed outcome of an actual permission-request
//      attempt (getUserMedia / getCurrentPosition), when the Permissions
//      API can't be queried ahead of time (e.g. Firefox has no queryable
//      "microphone" permission name).
//
// A permission that the browser has already granted can generally NOT be
// programmatically revoked — there is no "disable" request here, only
// "request" (idempotent: requesting an already-granted permission just
// resolves granted again, no second OS prompt). A denied permission
// usually can't be re-prompted either — request() still attempts it
// honestly, but a browser that refuses to re-prompt will just report
// "denied" again; the UI is responsible for telling the user to change it
// in their browser/device settings in that case (see
// PermissionsSettings.jsx), never for pretending a retry silently fixed it.
//
// Extensibility (see the permission-system spec, item 11): adding a
// future capability (camera, contacts, files, bluetooth, ...) means
// adding one entry to REGISTRY/PERMISSION_DEFS below — nothing else in
// this module, or in the Settings UI that renders PERMISSION_DEFS,
// needs to change.

import { getCurrentLocation } from "./geolocation";

/**
 * @typedef {"granted"|"denied"|"prompt"|"unsupported"} PermissionState
 */

// ── the registry ────────────────────────────────────────────────────────
// Each entry:
//   permissionsApiName: the name navigator.permissions.query({name}) uses
//     for this capability, or null if the Permissions API has no query
//     name for it at all (nothing today needs this, kept for future
//     capabilities that don't map to a queryable permission).
//   request(): triggers the REAL platform request flow and resolves to
//     the observed PermissionState, or null if the attempt's outcome is
//     genuinely ambiguous (not a denial, not unsupported, just didn't
//     complete) — the manager re-queries the real permission in that case
//     rather than guessing.
const REGISTRY = {
  microphone: {
    permissionsApiName: "microphone",
    async request() {
      if (!navigator.mediaDevices?.getUserMedia) return "unsupported";
      try {
        // Only ever used to trigger/observe the real permission prompt —
        // the stream is stopped immediately, never kept open. Actual
        // mic capture for voice input is MicStreamer's own job
        // (lib/mic.js), reusing this same underlying browser permission.
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
        return "granted";
      } catch (e) {
        return e?.name === "NotAllowedError" ? "denied" : "unsupported";
      }
    },
  },
  location: {
    permissionsApiName: "geolocation",
    async request() {
      try {
        await getCurrentLocation();
        return "granted";
      } catch (e) {
        if (e?.code === "denied") return "denied";
        if (e?.code === "unsupported") return "unsupported";
        // "unavailable"/"timeout": the browser allowed the attempt (no
        // denial) but a fix didn't come back — the PERMISSION itself is
        // not necessarily denied. Ambiguous; let the caller re-query the
        // real permission state instead of guessing here.
        return null;
      }
    },
  },
};

/** Static, UI-facing metadata — id/name/description only. Deliberately
 * separate from REGISTRY's request-mechanics above so the Settings UI
 * (PermissionsSettings.jsx) can iterate this list without knowing
 * anything about how each capability is actually queried/requested. */
export const PERMISSION_DEFS = [
  {
    id: "microphone",
    name: "Microphone",
    description: "Lets SARANA hear you for voice conversations and voice commands.",
  },
  {
    id: "location",
    name: "Location",
    description:
      "Allows SARANA to provide nearby places, weather, directions, and location-aware assistance.",
  },
];

class PermissionManager {
  constructor() {
    /** @type {Record<string, PermissionState>} */
    this._state = {};
    /** @type {Record<string, Set<(state: PermissionState) => void>>} */
    this._listeners = {};
    /** @type {Record<string, PermissionStatus>} */
    this._statuses = {};
  }

  /** Last known state without triggering any query/request — may be
   * undefined if nothing has queried this capability yet this page load. */
  getCached(id) {
    return this._state[id];
  }

  /** Reads the REAL current permission state. Never prompts the user —
   * safe to call at any time (page load, opening Settings, before
   * deciding whether to attempt a capability-dependent action) without
   * violating least-privilege. */
  async query(id) {
    const def = REGISTRY[id];
    if (!def) return "unsupported";

    if (!navigator.permissions?.query || !def.permissionsApiName) {
      // Can't query ahead of time on this browser (e.g. Firefox has no
      // queryable "microphone" permission name) — report the last state
      // an actual request() attempt genuinely observed, or "prompt"
      // (honestly "not yet decided/known", never fabricated as granted)
      // if nothing has been observed yet.
      return this._setState(id, this._state[id] || "prompt");
    }

    try {
      const status = await navigator.permissions.query({ name: def.permissionsApiName });
      this._wireLiveUpdates(id, status);
      return this._setState(id, status.state);
    } catch {
      // Some browsers support the API but reject specific permission
      // names (e.g. Safari's partial support) — same honest fallback.
      return this._setState(id, this._state[id] || "prompt");
    }
  }

  /** Invokes the REAL platform permission-request flow (the browser's own
   * native prompt, if one is due) and resolves to the observed result.
   * Only call this in direct response to the user enabling a capability
   * in Settings, or a genuine in-conversation need for it — never
   * automatically on page load (least privilege — see the permission-
   * system spec). */
  async request(id) {
    const def = REGISTRY[id];
    if (!def) return "unsupported";
    const result = await def.request();
    if (result) return this._setState(id, result);
    // Ambiguous outcome — re-query the real permission rather than guess.
    return this.query(id);
  }

  /** A capability's state was determined some other way this module
   * doesn't itself observe (e.g. lib/mic.js's own live streaming attempt
   * hit "denied" mid-conversation) — folds that real, already-observed
   * outcome into the shared cache so Settings and every other reader
   * stay honest, without pretending this module performed the check. */
  reportObserved(id, state) {
    if (!["granted", "denied", "prompt", "unsupported"].includes(state)) return;
    this._setState(id, state);
  }

  _wireLiveUpdates(id, status) {
    if (this._statuses[id] === status) return; // already wired to this exact status object
    this._statuses[id] = status;
    status.onchange = () => this._setState(id, status.state);
  }

  _setState(id, state) {
    if (this._state[id] === state) return state;
    this._state[id] = state;
    this._listeners[id]?.forEach((fn) => fn(state));
    return state;
  }

  /** Subscribes to live state changes for one capability. Immediately
   * fires once with the cached state (if any) and kicks off a fresh
   * query (never a request — no prompt). Returns an unsubscribe fn. */
  subscribe(id, fn) {
    if (!this._listeners[id]) this._listeners[id] = new Set();
    this._listeners[id].add(fn);
    if (this._state[id]) fn(this._state[id]);
    this.query(id);
    return () => this._listeners[id]?.delete(fn);
  }
}

/** Singleton — the one shared permission manager instance for the whole
 * app, per the "centralized, not scattered" requirement. */
export const permissionManager = new PermissionManager();
