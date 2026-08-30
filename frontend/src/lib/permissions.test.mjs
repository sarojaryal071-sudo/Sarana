// src/lib/permissions.test.mjs — focused regression tests for
// permissionManager's two-layer capability model (browser permission +
// SARANA's own enable preference -> effective state -> optional
// consumer). Uses Node's own built-in test runner (node:test/assert) —
// no new dependency, matching this project's existing "standalone
// script, no framework" testing convention on the Python side.
//
// Run with:
//   cd frontend && node --test src/lib/permissions.test.mjs
// (or `npm test`, see package.json)
//
// A minimal navigator mock is installed BEFORE importing permissions.js
// — Node's own built-in `navigator` global is read-only, so it's
// replaced via Object.defineProperty. Most tests use arbitrary,
// per-test-unique capability ids with reportObserved() to seed browser
// state directly, bypassing REGISTRY/the mock entirely (reportObserved/
// setEnabled/getEffectiveState/registerConsumer/resetSession don't
// consult REGISTRY at all) — this keeps every test isolated from every
// other, even though permissionManager is a persistent singleton for the
// whole file's run. The few tests that need to exercise a REAL registry
// entry (proving no browser call happens, or Firefox-style fallback
// behavior) use "microphone"/"location" deliberately, with the mock
// wired for that one case.
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const mockNavigator = {
  permissions: undefined, // no Permissions API by default -- exercises the honest fallback branch
  mediaDevices: {
    getUserMedia: async () => {
      const err = new Error("mock: no getUserMedia wired for this test");
      err.name = "NotAllowedError";
      throw err;
    },
  },
  geolocation: {
    getCurrentPosition: (_success, error) => {
      error({ code: 2, message: "mock: no geolocation wired for this test" });
    },
  },
};
Object.defineProperty(globalThis, "navigator", {
  value: mockNavigator, configurable: true, writable: true,
});

const { permissionManager } = await import("./permissions.js");

let counter = 0;
function freshId() {
  // A unique fake capability id per test -- REGISTRY has no entry for
  // it, so request()/enable() safely resolve "unsupported" rather than
  // touching the (shared, mocked) navigator -- exactly what keeps these
  // tests independent of each other and of the two real ids.
  counter += 1;
  return `test-capability-${counter}`;
}

// ── item 1/2: the effective-state formula ──────────────────────────────

test("browserState=granted + saranaEnabled=true -> effective granted", () => {
  const id = freshId();
  permissionManager.reportObserved(id, "granted");
  permissionManager.setEnabled(id, true);
  assert.equal(permissionManager.getEffectiveState(id), "granted");
});

test("browserState=granted + saranaEnabled=false -> effective denied", () => {
  const id = freshId();
  permissionManager.reportObserved(id, "granted");
  permissionManager.setEnabled(id, false);
  assert.equal(permissionManager.getEffectiveState(id), "denied");
});

// ── item 8: a browser denial can never be overridden ────────────────────

test("browserState=denied stays denied even with saranaEnabled=true", () => {
  const id = freshId();
  permissionManager.reportObserved(id, "denied");
  permissionManager.setEnabled(id, true);
  assert.equal(permissionManager.getEffectiveState(id), "denied");
});

test("browserState=unsupported/prompt pass through unchanged regardless of saranaEnabled", () => {
  const idA = freshId();
  permissionManager.reportObserved(idA, "unsupported");
  permissionManager.setEnabled(idA, true);
  assert.equal(permissionManager.getEffectiveState(idA), "unsupported");

  const idB = freshId();
  permissionManager.reportObserved(idB, "prompt");
  permissionManager.setEnabled(idB, true);
  assert.equal(permissionManager.getEffectiveState(idB), "prompt");
});

// ── items 5/6: the consumer is the enforcement mechanism ────────────────

test("turning a granted+enabled capability off fires the consumer's onDisable", () => {
  const id = freshId();
  let enables = 0, disables = 0;
  permissionManager.registerConsumer(id, {
    onEnable: () => { enables += 1; },
    onDisable: () => { disables += 1; },
  });
  permissionManager.reportObserved(id, "granted"); // default enabled=true -> effective becomes granted
  assert.equal(enables, 1);
  assert.equal(disables, 0);

  permissionManager.setEnabled(id, false);
  assert.equal(disables, 1, "onDisable must fire when SARANA's own preference turns off");
});

test("re-enabling while the browser is already granted fires onEnable again", () => {
  const id = freshId();
  let enables = 0;
  permissionManager.registerConsumer(id, { onEnable: () => { enables += 1; }, onDisable: () => {} });
  permissionManager.reportObserved(id, "granted");
  permissionManager.setEnabled(id, false);
  assert.equal(enables, 1);

  permissionManager.setEnabled(id, true);
  assert.equal(enables, 2, "onEnable must fire again when turned back on");
});

test("a live browser revocation also fires onDisable, not just a user click", () => {
  const id = freshId();
  let disables = 0;
  permissionManager.registerConsumer(id, { onEnable: () => {}, onDisable: () => { disables += 1; } });
  permissionManager.reportObserved(id, "granted"); // effective granted, enabled stays true throughout
  assert.equal(disables, 0);

  permissionManager.reportObserved(id, "denied"); // the BROWSER changed, not saranaEnabled
  assert.equal(disables, 1, "revoking the browser permission must stop the consumer too");
});

// ── item 7: turning off is a LOCAL preference flip, never a browser call ─

test("turning microphone off never invokes getUserMedia", async () => {
  let getUserMediaCalls = 0;
  mockNavigator.mediaDevices.getUserMedia = async () => {
    getUserMediaCalls += 1;
    return { getTracks: () => [] };
  };
  try {
    await permissionManager.request("microphone"); // establishes browserState=granted
    assert.equal(getUserMediaCalls, 1);
    permissionManager.setEnabled("microphone", true);

    const before = getUserMediaCalls;
    permissionManager.setEnabled("microphone", false); // the actual action under test
    assert.equal(getUserMediaCalls, before, "setEnabled(false) must not touch the browser API at all");
  } finally {
    // leave the shared "microphone" registry entry in a clean, known
    // state for any other test in this file that also touches it.
    permissionManager.setEnabled("microphone", true);
  }
});

// ── toggle(): the one click-semantics both UI controls call ─────────────

test("toggle() turns an effectively-granted capability off locally, then back on without a new browser call", async () => {
  const id = freshId();
  permissionManager.reportObserved(id, "granted");
  assert.equal(permissionManager.getEffectiveState(id), "granted");

  await permissionManager.toggle(id);
  assert.equal(permissionManager.getEffectiveState(id), "denied");

  await permissionManager.toggle(id);
  assert.equal(permissionManager.getEffectiveState(id), "granted");
});

test("toggle() on a not-yet-decided (prompt) capability attempts a real request", async () => {
  // Uses the real "location" registry entry (not a fake id) so the
  // request path actually reaches the mocked navigator — proving a
  // genuine attempt happens, not just a local state flip.
  let getCurrentPositionCalls = 0;
  mockNavigator.geolocation.getCurrentPosition = (_success, error) => {
    getCurrentPositionCalls += 1;
    error({ code: 1, message: "mock: user denied" }); // PERMISSION_DENIED
  };
  try {
    permissionManager.reportObserved("location", "prompt");
    const effective = await permissionManager.toggle("location");
    assert.equal(getCurrentPositionCalls, 1, "toggle() on a prompt capability must actually call the platform API");
    assert.equal(effective, "denied");
  } finally {
    permissionManager.reportObserved("location", "prompt"); // leave it clean for other tests
  }
});

// ── web live camera vision: the "camera" registry entry ─────────────────

test("PERMISSION_DEFS includes camera alongside microphone/location", async () => {
  const { PERMISSION_DEFS } = await import("./permissions.js");
  const ids = PERMISSION_DEFS.map((d) => d.id);
  assert.ok(ids.includes("camera"), "camera must be a listed capability, same as microphone/location");
});

test("camera request() resolves granted via getUserMedia({video: {facingMode: {ideal: \"environment\"}}})", async () => {
  let lastConstraints = null;
  mockNavigator.mediaDevices.getUserMedia = async (constraints) => {
    lastConstraints = constraints;
    return { getTracks: () => [{ stop: () => {} }] };
  };
  try {
    const result = await permissionManager.request("camera");
    assert.equal(result, "granted");
    assert.equal(lastConstraints.video.facingMode.ideal, "environment");
    assert.ok(!("exact" in lastConstraints.video.facingMode), "must never hard-require exact:\"environment\"");
  } finally {
    permissionManager.reportObserved("camera", "prompt"); // leave clean for other tests
  }
});

test("camera request() resolves denied on NotAllowedError, never silently granted", async () => {
  mockNavigator.mediaDevices.getUserMedia = async () => {
    const err = new Error("mock denial");
    err.name = "NotAllowedError";
    throw err;
  };
  try {
    const result = await permissionManager.request("camera");
    assert.equal(result, "denied");
  } finally {
    permissionManager.reportObserved("camera", "prompt");
  }
});

test("App.jsx never auto-enables camera the way it auto-starts microphone", () => {
  // No consumer/auto-enable effect exists for "camera" the way the mic
  // auto-start effect exists for "microphone" (see App.jsx's own
  // permissionManager.enable("microphone") effect) — starting the camera
  // is always driven directly by CameraVisionPanel.jsx in response to a
  // real backend "camera_vision_request", never proactively on login.
  const appSrc = fs.readFileSync(path.join(__dirname, "..", "App.jsx"), "utf8");
  assert.doesNotMatch(
    appSrc, /permissionManager\.enable\(\s*"camera"\s*\)/,
    "App.jsx must never proactively request camera access on login",
  );
});

// ── item 11: session reset preserves the existing lifecycle behavior ────

test("resetSession() forgets the enable preference but never the browser permission", () => {
  const id = freshId();
  permissionManager.reportObserved(id, "granted");
  permissionManager.setEnabled(id, false);
  assert.equal(permissionManager.getEffectiveState(id), "denied");

  permissionManager.resetSession();

  assert.equal(permissionManager.isEnabled(id), true, "next session starts at the default (enabled)");
  assert.equal(permissionManager.getBrowserState(id), "granted", "the real platform permission must not be forgotten");
  assert.equal(permissionManager.getEffectiveState(id), "granted", "usable again immediately at session start");
});

// ── item 12: exactly one authoritative state, no independent booleans ───

test("the main mic button and the Settings switch call the same permissionManager.toggle(), never a raw MicStreamer boolean", () => {
  const appSrc = fs.readFileSync(path.join(__dirname, "..", "App.jsx"), "utf8");
  const settingsSrc = fs.readFileSync(path.join(__dirname, "..", "components", "PermissionsSettings.jsx"), "utf8");

  assert.match(
    appSrc, /permissionManager\.toggle\(\s*"microphone"\s*\)/,
    "App.jsx's handleToggleMic must call permissionManager.toggle(\"microphone\")",
  );
  assert.match(
    settingsSrc, /permissionManager\.toggle\(def\.id\)/,
    "PermissionsSettings.jsx's click handler must call permissionManager.toggle(def.id)",
  );
  assert.doesNotMatch(
    appSrc, /micRef\.current\.active/,
    "no code path may decide on/off by branching on MicStreamer's own .active anymore",
  );
});
