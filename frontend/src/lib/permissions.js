// src/lib/permissions.js — centralized, extensible capability coordinator
// for SARANA Web. This is the ONE place browser/OS permission state is
// ever read/requested from, AND the ONE place SARANA's own "am I
// currently allowed to use this" decision lives — every component and
// every capability consumer (MicStreamer, a future CameraStreamer, ...)
// reads/drives that decision through here instead of keeping its own
// independent on/off boolean.
//
// Two deliberately separate layers, per capability id:
//
//   browserState  — what the platform actually allows: "granted" |
//                    "denied" | "prompt" | "unsupported". Sourced only
//                    from real platform APIs (Permissions API query, or
//                    the observed outcome of an actual getUserMedia/
//                    getCurrentPosition attempt). SARANA can request it,
//                    but can never fabricate or override it.
//
//   saranaEnabled — whether SARANA currently WANTS to use a capability
//                    it's allowed to use. A purely application-level
//                    preference, defaulting to true each fresh session
//                    (see resetSession()) and living ONLY in memory here
//                    — never localStorage/sessionStorage/a backend
//                    database. Investigated against the existing,
//                    working microphone button before adding this: that
//                    button already re-decides "was the mic manually
//                    stopped" fresh on every login (see App.jsx's
//                    micAutoStartedRef), never remembers it across a
//                    reload — this mirrors that exact, deliberate,
//                    session-only precedent instead of introducing a new
//                    persisted preference system.
//
// effectiveState — the ONE value everything else (UI, the backend, a
// capability's own consumer) should ever act on:
//   browserState !== "granted"            -> browserState  (denied/
//                                             prompt/unsupported pass
//                                             through unchanged --
//                                             saranaEnabled can NEVER
//                                             make an unavailable
//                                             capability usable)
//   browserState === "granted" && enabled  -> "granted"
//   browserState === "granted" && !enabled -> "denied"       (browser-
//                                             authorized, but SARANA
//                                             must not use it -- to the
//                                             rest of the app this reads
//                                             exactly like an ordinary
//                                             denial, which is honest:
//                                             it genuinely isn't usable)
//
// A capability may optionally register a CONSUMER — the thing that
// actually starts/stops using it (MicStreamer for microphone; location
// is pull-on-demand and has none; a future camera would get its own).
// The coordinator calls the consumer's onEnable()/onDisable() exactly
// once per genuine effectiveState transition, from EITHER cause: the
// user flipping saranaEnabled, OR the browser permission itself
// changing (a live revocation must stop a consumer just as surely as
// the user turning the switch off -- see registerConsumer()'s own note).
// This is what makes "off" actually enforced, not just displayed.

import { getCurrentLocation } from "./geolocation.js";

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
        // (lib/mic.js), started/stopped via this capability's registered
        // consumer (see App.jsx), reusing this same underlying browser
        // permission.
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
  // Web live camera vision: unlike microphone/location above, nothing
  // proactively calls enable("camera") on login — a real getUserMedia
  // request only ever happens in direct response to a backend
  // "camera_vision_request" (see lib/cameraVision.js/
  // components/CameraVisionPanel.jsx), and even then only automatically
  // when the browser already granted it; the FIRST time, it waits for an
  // explicit "Allow Camera" tap so the request stays inside a real user
  // gesture (required by iOS Safari, and good practice everywhere). This
  // registry entry exists so that flow shares the exact same "never
  // fabricate/override a browser denial" state model as everything else,
  // and so Settings can show real camera status.
  camera: {
    permissionsApiName: "camera",
    async request() {
      if (!navigator.mediaDevices?.getUserMedia) return "unsupported";
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
        });
        stream.getTracks().forEach((t) => t.stop());
        return "granted";
      } catch (e) {
        return e?.name === "NotAllowedError" ? "denied" : "unsupported";
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
  {
    id: "camera",
    name: "Camera",
    description:
      "Lets SARANA briefly look through your camera when it needs to see something you're showing it.",
  },
];

const VALID_STATES = new Set(["granted", "denied", "prompt", "unsupported"]);

class PermissionManager {
  constructor() {
    /** @type {Record<string, PermissionState>} browser/OS permission, per id */
    this._state = {};
    /** @type {Record<string, boolean>} SARANA's own enable preference, per
     * id — absent means "default true" (see isEnabled()); never read from
     * or written to any persistent store. */
    this._enabled = {};
    /** @type {Record<string, {onEnable?: Function, onDisable?: Function}>} */
    this._consumers = {};
    /** @type {Record<string, Set<(state: PermissionState) => void>>} */
    this._listeners = {};
    /** @type {Record<string, PermissionStatus>} */
    this._statuses = {};
  }

  // ── browser permission (unchanged mechanics from before) ──────────────

  /** Last known EFFECTIVE state without triggering any query/request —
   * may be undefined if nothing has queried this capability yet this
   * page load. This is what UI should render from. */
  getCached(id) {
    return this.getEffectiveState(id);
  }

  /** The raw platform permission, with no SARANA-preference gating
   * applied — only needed by UI that has to explain WHY a capability is
   * off (browser denial vs. the user's own choice — see
   * PermissionsSettings.jsx). Everything else should use
   * getEffectiveState()/getCached(). */
  getBrowserState(id) {
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
      this._setState(id, this._state[id] || "prompt");
      return this.getEffectiveState(id);
    }

    try {
      const status = await navigator.permissions.query({ name: def.permissionsApiName });
      this._wireLiveUpdates(id, status);
      this._setState(id, status.state);
    } catch {
      // Some browsers support the API but reject specific permission
      // names (e.g. Safari's partial support) — same honest fallback.
      this._setState(id, this._state[id] || "prompt");
    }
    return this.getEffectiveState(id);
  }

  /** Invokes the REAL platform permission-request flow (the browser's own
   * native prompt, if one is due) and resolves to the observed BROWSER
   * state (not the effective one — callers that care about SARANA's own
   * preference should use enable()/toggle() below, which call this
   * internally and then apply that preference). Only ever called from
   * enable()/toggle() (a direct user action) or PermissionsSettings.jsx's
   * "Try again" — never automatically on page load (least privilege). */
  async request(id) {
    const def = REGISTRY[id];
    if (!def) return "unsupported";
    const result = await def.request();
    if (result) {
      this._setState(id, result);
      return result;
    }
    // Ambiguous outcome — re-query the real permission rather than guess.
    await this.query(id);
    return this._state[id];
  }

  /** A capability's BROWSER state was determined some other way this
   * module doesn't itself observe (e.g. lib/mic.js's own live streaming
   * attempt hit "denied" mid-conversation) — folds that real,
   * already-observed outcome into the shared cache so Settings and every
   * other reader stay honest, without pretending this module performed
   * the check. */
  reportObserved(id, state) {
    if (!VALID_STATES.has(state)) return;
    this._setState(id, state);
  }

  _wireLiveUpdates(id, status) {
    if (this._statuses[id] === status) return; // already wired to this exact status object
    this._statuses[id] = status;
    status.onchange = () => this._setState(id, status.state);
  }

  // ── SARANA's own enable preference ─────────────────────────────────────

  /** Defaults to true — a fresh session (or a capability nothing has ever
   * touched) assumes SARANA MAY use whatever the browser allows, exactly
   * matching the existing mic/location auto-request behavior this was
   * modeled on. Only ever false once setEnabled(id, false) has actually
   * been called THIS session. */
  isEnabled(id) {
    return this._enabled[id] !== false;
  }

  /** The one thing a UI control (main mic button, Settings switch) is
   * ever allowed to set directly. Never touches the browser permission.
   * Fires the registered consumer's onEnable()/onDisable() — and notifies
   * subscribers — only when this actually changes the EFFECTIVE state
   * (e.g. setting enabled=false while the browser is denied is a no-op:
   * the capability was already unusable). */
  setEnabled(id, value) {
    if (this.isEnabled(id) === value) return; // already effectively that value
    const prevEffective = this.getEffectiveState(id);
    this._enabled[id] = value;
    this._recomputeAndNotify(id, prevEffective);
  }

  /** Registers the thing that actually starts/stops CONSUMING a
   * capability once it becomes effectively granted/ungranted — e.g.
   * MicStreamer for "microphone". Optional: a pull-on-demand capability
   * like "location" has nothing to start/stop and registers nothing.
   * Passing undefined clears a previous registration (see App.jsx's
   * per-login re-registration with a fresh token-bound closure).
   *
   * This is the enforcement mechanism: onDisable() fires not only when
   * the user flips the Settings/main-button switch off, but also if the
   * BROWSER permission itself is revoked while the capability was in use
   * — a live revocation must stop a consumer exactly as surely as the
   * user's own choice does. */
  registerConsumer(id, consumer) {
    this._consumers[id] = consumer || {};
  }

  /** The single source of truth every reader (UI, backend sync, a
   * consumer's own start/stop decision) should use. */
  getEffectiveState(id) {
    const browser = this._state[id];
    if (browser === undefined) return undefined; // never queried/observed yet
    if (browser !== "granted") return browser; // denied/prompt/unsupported: enabled can never override
    return this.isEnabled(id) ? "granted" : "denied";
  }

  /** Turns a capability ON: if the browser has already granted it, this
   * is purely a local preference flip (setEnabled — no browser call, no
   * re-prompt, matches "ON -> click -> OFF -> click -> ON must be real
   * state changes, not permission re-requests"). If the browser hasn't
   * decided yet (or actively refuses), this is the one place that makes
   * a REAL request — and only actually enables SARANA's own preference
   * if that request comes back granted; a denial is never overridden. */
  async enable(id) {
    if (this._state[id] === "granted") {
      this.setEnabled(id, true);
      return this.getEffectiveState(id);
    }
    const result = await this.request(id);
    if (result === "granted") this.setEnabled(id, true);
    return this.getEffectiveState(id);
  }

  /** The single click-semantics both the main mic button and the
   * Settings switch call. Granted+on -> turn off (local only, no browser
   * call). Anything else -> enable() (which itself decides whether a
   * real browser request is needed). */
  async toggle(id) {
    if (this.getEffectiveState(id) === "granted") {
      this.setEnabled(id, false);
      return this.getEffectiveState(id);
    }
    return this.enable(id);
  }

  /** Fired at the same point in the login/logout lifecycle App.jsx
   * already resets micAutoStartedRef/locationRequestedRef (the /ws
   * effect's cleanup) — forgets SARANA's own enable preference so the
   * NEXT login starts fresh at the default (true), exactly mirroring
   * that existing, deliberate "never remember across a reload/relogin"
   * precedent. Deliberately does NOT touch _state/_statuses: the
   * browser's own permission is a real platform fact that doesn't
   * change just because SARANA logged out, and does NOT itself call any
   * consumer — actually stopping an in-flight consumer (e.g. closing the
   * current MicStreamer) is the teardown code's own direct
   * responsibility (it's tied to the token/session being torn down, not
   * to this preference), already handled at the same call site. */
  resetSession() {
    this._enabled = {};
  }

  _recomputeAndNotify(id, prevEffective) {
    const nowEffective = this.getEffectiveState(id);
    if (nowEffective !== prevEffective) {
      const consumer = this._consumers[id];
      if (nowEffective === "granted") consumer?.onEnable?.();
      else consumer?.onDisable?.();
    }
    // Always notify listeners, even when the EFFECTIVE value itself
    // didn't change — the browser state can still have changed
    // underneath an unchanged effective value (e.g. the browser
    // permission gets revoked while the user had already turned SARANA
    // off), and PermissionsSettings.jsx's status copy needs a fresh
    // getBrowserState() read to keep distinguishing "you turned this
    // off" from "your browser is blocking this". React's own setState
    // bails out on an identical value, so this costs nothing when
    // truly nothing changed.
    const effective = this.getEffectiveState(id);
    this._listeners[id]?.forEach((fn) => fn(effective));
  }

  _setState(id, browserState) {
    if (this._state[id] === browserState) return browserState;
    const prevEffective = this.getEffectiveState(id);
    this._state[id] = browserState;
    this._recomputeAndNotify(id, prevEffective);
    return browserState;
  }

  /** Subscribes to live EFFECTIVE state changes for one capability.
   * Immediately fires once with the cached effective state (if any) and
   * kicks off a fresh query (never a request — no prompt). Returns an
   * unsubscribe fn. */
  subscribe(id, fn) {
    if (!this._listeners[id]) this._listeners[id] = new Set();
    this._listeners[id].add(fn);
    const cached = this.getEffectiveState(id);
    if (cached !== undefined) fn(cached);
    this.query(id);
    return () => this._listeners[id]?.delete(fn);
  }
}

/** Singleton — the one shared permission/capability coordinator instance
 * for the whole app, per the "centralized, not scattered" requirement.
 * The main mic button (App.jsx) and the Settings mic switch
 * (PermissionsSettings.jsx) both read/drive THIS object's "microphone"
 * entry — neither keeps an independent on/off boolean of its own. */
export const permissionManager = new PermissionManager();
