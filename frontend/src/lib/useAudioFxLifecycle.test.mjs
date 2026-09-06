// src/lib/useAudioFxLifecycle.test.mjs — extracted from App.jsx (Track 4)
// so the desktop-embedded Presentation Engine entry can react to the same
// assistantStatus/theme state through the exact same hook, not a
// re-derivation. Source-inspection style, same convention as this
// project's other component tests (no React renderer in this test setup —
// see PresentationSurface.test.mjs's own header note).
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "useAudioFxLifecycle.js"), "utf8");

test("exports a single useAudioFxLifecycle(assistantStatus, identity) hook", () => {
  assert.match(src, /export function useAudioFxLifecycle\(assistantStatus, identity\)/);
});

test("syncs the audio theme from `identity`, not a second theme source", () => {
  assert.match(src, /setAudioFxTheme\(identity\)/);
});

test("feeds speaking-aware ducking on every status change (SPEAKING drives setSpeaking)", () => {
  assert.match(src, /setAudioFxSpeaking\(next === "SPEAKING"\)/);
});

test("only fires a cue on an ACTUAL transition, never on every render", () => {
  assert.match(src, /if \(prev === next\) return;/);
});

test("covers exactly the four documented transitions — wake, sleep, listening, thinking", () => {
  assert.match(src, /next === "SLEEPING"[\s\S]{0,20}playAudioFx\("assistant_sleep"\)/);
  assert.match(src, /prev === "SLEEPING"[\s\S]{0,20}playAudioFx\("assistant_wake"\)/);
  assert.match(src, /next === "LISTENING"[\s\S]{0,20}playAudioFx\("assistant_listening"\)/);
  assert.match(src, /next === "THINKING"[\s\S]{0,20}playAudioFx\("assistant_thinking"\)/);
});

test("tracks previous status in a ref, not component state (no extra re-render just to remember it)", () => {
  assert.match(src, /const prevStatusRef = useRef\(assistantStatus\)/);
});
