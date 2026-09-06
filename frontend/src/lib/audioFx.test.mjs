// src/lib/audioFx.test.mjs — Track 4's cinematic audio event system.
// Runs the REAL module (not source-inspection) — Node has no
// AudioContext, so _getContext() naturally returns null and every
// playAudioFx() call takes the honest no-op path; this is exactly the
// "no crash when Web Audio is unavailable" behavior section 20 asks to
// be tested, exercised for real rather than mocked around.
//
// Run with:
//   cd frontend && node --test src/lib/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { playAudioFx, setAudioFxTheme, setSpeaking, AUDIO_FX_EVENTS } from "./audioFx.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "audioFx.js"), "utf8");

// ── real runtime behavior (no AudioContext in Node — honest no-op) ───

test("playAudioFx never throws for a known event when Web Audio is unavailable (Node has no AudioContext)", () => {
  assert.doesNotThrow(() => {
    for (const name of AUDIO_FX_EVENTS) playAudioFx(name, { force: true });
  });
});

test("playAudioFx returns false (not a throw) for an unknown event name", () => {
  assert.equal(playAudioFx("totally_made_up_event"), false);
});

test("setAudioFxTheme/setSpeaking never throw regardless of input", () => {
  assert.doesNotThrow(() => setAudioFxTheme("jarvis"));
  assert.doesNotThrow(() => setAudioFxTheme("sarana"));
  assert.doesNotThrow(() => setAudioFxTheme("nonsense"));
  assert.doesNotThrow(() => setSpeaking(true));
  assert.doesNotThrow(() => setSpeaking(false));
});

test("the exported event vocabulary is frozen — no accidental runtime mutation", () => {
  assert.ok(Object.isFrozen(AUDIO_FX_EVENTS));
});

test("the vocabulary covers section 12's required events (wake/sleep/listening/thinking, confirmation/blocked/error, both transitions)", () => {
  const required = [
    "assistant_wake", "assistant_sleep", "assistant_listening", "assistant_thinking",
    "confirmation_required", "blocked", "error",
    "transition_sarana_to_jarvis", "transition_jarvis_to_sarana",
  ];
  for (const name of required) assert.ok(AUDIO_FX_EVENTS.includes(name), `missing event: ${name}`);
});

// ── source-inspection: architecture/priority/mixing (section 13) ────

test("no second audio engine: never imports lib/audioOut.js — a genuinely separate concern (see module's own header)", () => {
  assert.doesNotMatch(src, /^import.*audioOut/m);
  assert.doesNotMatch(src, /from ["'](?!\.\.?\/)/m, "every import must be a relative project module, never a new package");
});

test("no external assets — every cue is synthesized (OscillatorNode/BiquadFilterNode), no actual asset file path referenced", () => {
  assert.doesNotMatch(src, /["'][^"']*\.(mp3|wav|ogg|m4a)["']/i, "no quoted asset file path anywhere in the module");
  assert.doesNotMatch(src, /new Audio\(|\bfetch\(/);
  assert.match(src, /createOscillator/);
  assert.match(src, /createBiquadFilter/);
});

test("priority model: three tiers, AMBIENT fully suppressed while speaking, CRITICAL never suppressed by speech", () => {
  assert.match(src, /PRIORITY = \{ CRITICAL: 2, NORMAL: 1, AMBIENT: 0 \}/);
  assert.match(src, /if \(spec\.priority === PRIORITY\.AMBIENT\) return false;/);
  assert.match(src, /spec\.priority < PRIORITY\.CRITICAL/);
});

test("production-polish fix: NORMAL genuinely DUCKS (plays quieter) while speaking, never fully suppressed like AMBIENT", () => {
  // Real bug found via code audit: NORMAL used to `return false`
  // (fully silent) under exactly the same condition as AMBIENT, despite
  // being documented as "ducks under speech". Verifies the fix: a duck
  // gain node is actually created and used as the synth destination
  // instead of unconditionally returning early for NORMAL.
  assert.match(src, /let duck = false;/);
  assert.match(src, /duck = true;/);
  assert.match(src, /const duckGain = ctx\.createGain\(\);/);
  assert.match(src, /duckGain\.gain\.value = DUCK_GAIN;/);
  assert.match(src, /const DUCK_GAIN = [\d.]+;/);
  // The old bug's exact shape must not reappear: a bare early return
  // immediately after detecting NORMAL-under-speech.
  assert.doesNotMatch(src, /duck = true[\s\S]{0,20}return false/);
});

test("every event has a cooldown — a spam guard, never an unconditional re-trigger", () => {
  const entries = [...src.matchAll(/priority: PRIORITY\.\w+, cooldownMs: (\d+)/g)];
  assert.ok(entries.length >= 9, "expected a cooldownMs on every registered event");
  for (const m of entries) assert.ok(Number(m[1]) > 0);
});

test("a synthesis error is caught, never propagated into the caller's own event handling", () => {
  assert.match(src, /try \{[\s\S]*?spec\.synth\(ctx, dest, ctx\.currentTime \+ 0\.01\);[\s\S]*?\} catch \{/);
});

test("missing/blocked Web Audio API degrades to a silent no-op, never a thrown error at import or first call", () => {
  assert.match(src, /if \(!Ctx\) return null;/);
  assert.match(src, /_ctx = null; \/\/ missing\/blocked Web Audio/);
});

test("theme detuning is ONE shared parameter applied uniformly, not a second cue set per theme (no duplicated event definitions)", () => {
  const jarvisEvents = (src.match(/transition_sarana_to_jarvis: \{/g) || []).length;
  const saranaEvents = (src.match(/transition_jarvis_to_sarana: \{/g) || []).length;
  assert.equal(jarvisEvents, 1);
  assert.equal(saranaEvents, 1);
  assert.match(src, /const THEME_DETUNE = \{ jarvis: -60, sarana: 40 \};/);
});

test("the Sarana->JARVIS and JARVIS->Sarana transitions are genuinely different syntheses, not the same call twice", () => {
  const jarvisBlock = src.slice(src.indexOf("transition_sarana_to_jarvis:"), src.indexOf("transition_jarvis_to_sarana:"));
  const saranaBlock = src.slice(src.indexOf("transition_jarvis_to_sarana:"));
  assert.notEqual(jarvisBlock.trim(), saranaBlock.trim());
  assert.match(jarvisBlock, /_clickBurst/);
  assert.match(jarvisBlock, /_sweep/);
  assert.match(jarvisBlock, /_tone/);
  assert.match(saranaBlock, /_clickBurst/);
  assert.match(saranaBlock, /_sweep/);
  assert.match(saranaBlock, /_tone/);
});
