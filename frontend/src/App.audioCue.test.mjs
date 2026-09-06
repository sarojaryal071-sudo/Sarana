// src/App.audioCue.test.mjs — the shared semantic audio event origin:
// App.jsx's onMessage switch now plays a cue directly from the backend's
// own authoritative "audio_cue" message (see dashboard/server.py's
// broadcast_audio_cue() and main.py's _emit_audio_cue_for_result()),
// replacing the old text-matching heuristic against Gemini's own
// paraphrased reply. Source-inspection style, same convention as this
// project's other App.jsx tests (see IdentityTransition.test.mjs).
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appSrc = fs.readFileSync(path.join(__dirname, "App.jsx"), "utf8");

test("handles the backend-authoritative audio_cue message by playing the exact event it names", () => {
  assert.match(appSrc, /case "audio_cue":[\s\S]{0,800}playAudioFx\(msg\.event\)/);
});

test("no longer text-matches log/sys message content for audio cues (the old, superseded heuristic)", () => {
  assert.doesNotMatch(appSrc, /playResultTagAudioFx/);
  assert.doesNotMatch(appSrc, /resultTagAudio/);
});

test("log and sys messages still update the Activity Log, unaffected by the audio-cue change", () => {
  assert.match(appSrc, /case "log":[\s\S]{0,120}dispatch\(\{ type: "LOG_MESSAGE"/);
  assert.match(appSrc, /case "sys":[\s\S]{0,120}dispatch\(\{ type: "SYS_MESSAGE"/);
});
