// src/lib/geolocation.js — thin Promise wrapper around the browser's own
// navigator.geolocation.getCurrentPosition(). This is the ONLY location
// mechanism SARANA Web uses: a one-shot fix, requested once per login (see
// App.jsx), never a continuous watch. No custom permission UI is built
// here — the browser's own native "Allow location access?" prompt is what
// the user sees, exactly as the architecture requires.
//
// No API keys, no third-party endpoints, nothing external touched here —
// this file only ever talks to the browser itself. Sending the resulting
// fix to our own backend is lib/api.js's job (sendLocation()), not this
// file's.

const DEFAULT_TIMEOUT_MS = 8000;
// A browser-cached fix up to 5 minutes old is fine for a voice assistant's
// occasional weather/nearby-place questions — this is city-level
// granularity, not turn-by-turn navigation, so trading a little staleness
// for a faster/cheaper resolution (or none at all, if the OS already has
// a fresh fix cached) is the right default.
const DEFAULT_MAX_AGE_MS = 5 * 60 * 1000;

/**
 * @typedef {{ latitude: number, longitude: number, accuracy: number }} LocationFix
 */

/**
 * Requests ONE current position fix via the browser's native geolocation
 * permission flow.
 *
 * Resolves with a LocationFix. Rejects with an Error whose `.code` is one
 * of "unsupported" | "denied" | "unavailable" | "timeout" — callers
 * should treat all four identically (location just isn't available right
 * now); none of them warrants retrying automatically or alarming the
 * user.
 *
 * @param {{ timeout?: number, maximumAge?: number }} [options]
 * @returns {Promise<LocationFix>}
 */
export function getCurrentLocation({
  timeout = DEFAULT_TIMEOUT_MS,
  maximumAge = DEFAULT_MAX_AGE_MS,
} = {}) {
  return new Promise((resolve, reject) => {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      const err = new Error("Geolocation is not supported by this browser.");
      err.code = "unsupported";
      reject(err);
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude, accuracy } = position.coords;
        // position.timestamp is the browser's own fix time (epoch ms) --
        // sent through so the backend can tell two fixes apart by when
        // they were actually TAKEN, not just when their POST happened to
        // arrive (see main.py's _set_session_location() — this is what
        // stops a slower-arriving-but-older refresh response from
        // clobbering a faster-arriving-but-newer one).
        resolve({ latitude, longitude, accuracy, timestamp: position.timestamp });
      },
      (error) => {
        // GeolocationPositionError.code: 1 = PERMISSION_DENIED,
        // 2 = POSITION_UNAVAILABLE, 3 = TIMEOUT.
        const codeByNumber = { 1: "denied", 2: "unavailable", 3: "timeout" };
        const err = new Error(error?.message || "Location unavailable.");
        err.code = codeByNumber[error?.code] || "unavailable";
        reject(err);
      },
      {
        enableHighAccuracy: false, // city-level is enough; faster lock, less battery
        timeout,
        maximumAge,
      },
    );
  });
}
