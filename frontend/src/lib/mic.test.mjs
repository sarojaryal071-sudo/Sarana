// src/lib/mic.test.mjs — pre-J4 noisy-environment fix regression test:
// MicStreamer.start()'s getUserMedia call must request echoCancellation,
// noiseSuppression, AND autoGainControl (autoGainControl was previously
// left unset, falling back to whatever the browser happens to default to).
//
// mic.js can't be dynamically imported under plain Node ESM the way
// permissions.js/cameraVision.js are elsewhere in this test suite: it
// imports ./api.js, which reads `import.meta.env.VITE_JARVIS_BACKEND_URL`
// at module load time -- valid under Vite (this project's actual runtime)
// but `import.meta.env` doesn't exist under plain `node --test`, so the
// import itself throws before any test code runs. That's a pre-existing
// property of api.js, not something this fix touches. Rather than
// reworking api.js's module shape (out of scope for a mic-constraints
// fix) or faking Vite's import.meta.env, this asserts directly against
// mic.js's own source for the one literal object this fix changed --
// still a deterministic, no-guessing check that the required constraint
// is actually present in the shipped file, per this task's own guidance
// to prefer the smallest sufficient verification over faking a full
// browser/build environment.
//
// Run with:
//   cd frontend && node --test src/lib/mic.test.mjs
// (or `npm test`, see package.json)
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(__dirname, "mic.js"), "utf8");

// Isolate the getUserMedia({...}) call so a match elsewhere in the file
// (there is none today, but future code shouldn't be able to satisfy this
// test by accident) can't produce a false pass.
const match = source.match(/getUserMedia\(\{[\s\S]*?audio:\s*\{([^}]*)\}/);

test("MicStreamer's getUserMedia call requests echoCancellation, noiseSuppression, and autoGainControl", () => {
  assert.ok(match, "could not find the getUserMedia({ audio: {...} }) call in mic.js");
  const audioConstraints = match[1];
  assert.match(audioConstraints, /echoCancellation:\s*true/);
  assert.match(audioConstraints, /noiseSuppression:\s*true/);
  assert.match(audioConstraints, /autoGainControl:\s*true/);
  assert.match(audioConstraints, /channelCount:\s*1/);
});
