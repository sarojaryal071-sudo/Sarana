// src/App.presentationControl.test.mjs — the Universal Information
// Surface's own control channel: a real voice/text command ("expand
// that", "keep this on screen", "hide it") reaches main.py's
// presentation_control tool, which broadcasts a "presentation_control"
// message App.jsx dispatches into the SAME reducer actions its own
// expand button/dismiss button already use — never a second command
// parser, never a second state model. Source-inspection style, same
// convention as this project's other App.jsx tests.
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appSrc = fs.readFileSync(path.join(__dirname, "App.jsx"), "utf8");

function presentationControlBlock() {
  const start = appSrc.indexOf('case "presentation_control":');
  assert.ok(start > -1, "expected a presentation_control case in App.jsx's onMessage switch");
  const end = appSrc.indexOf('case "file_received"', start);
  return appSrc.slice(start, end);
}

test("expand maps to PRESENTATION_EXPANDED: true", () => {
  assert.match(presentationControlBlock(), /msg\.action === "expand"[\s\S]{0,40}dispatch\(\{ type: "PRESENTATION_EXPANDED", value: true \}\)/);
});

test("collapse maps to PRESENTATION_EXPANDED: false", () => {
  assert.match(presentationControlBlock(), /msg\.action === "collapse"[\s\S]{0,40}dispatch\(\{ type: "PRESENTATION_EXPANDED", value: false \}\)/);
});

test("dismiss maps to the SAME DISMISS_CONTENT action the dismiss button already dispatches — no second dismissal path", () => {
  assert.match(presentationControlBlock(), /msg\.action === "dismiss"[\s\S]{0,40}dispatch\(\{ type: "DISMISS_CONTENT" \}\)/);
});

test("keep_visible maps to PRESENTATION_PERSISTENT: true", () => {
  assert.match(presentationControlBlock(), /msg\.action === "keep_visible"[\s\S]{0,40}dispatch\(\{ type: "PRESENTATION_PERSISTENT", value: true \}\)/);
});

test("an unrecognized action is silently ignored — no default branch that could crash or guess", () => {
  const block = presentationControlBlock();
  const ifCount = (block.match(/if \(msg\.action ===|else if \(msg\.action ===/g) || []).length;
  assert.equal(ifCount, 4, "expected exactly the four documented actions, no catch-all");
});

test("ContentPanel's expand button and the presentation_control WS case both dispatch the exact same PRESENTATION_EXPANDED action type — one shared control surface", () => {
  assert.match(appSrc, /onSetExpanded=\{\(value\) => dispatch\(\{ type: "PRESENTATION_EXPANDED", value \}\)\}/);
});
