// src/components/Controls.test.mjs — focused regression tests for the
// 📎 attach / 📷 camera input split in Controls.jsx.
//
// This project has no jsdom/React Testing Library dependency, and none of
// its existing frontend tests render a component — they all verify real,
// committed source text via targeted checks (see
// src/lib/permissions.test.mjs's own "no raw MicStreamer boolean" test for
// the established precedent). This file follows that same convention
// rather than introducing a new rendering-test setup for one component.
//
// What these tests intentionally do NOT do: assert that capture="environment"
// makes any particular browser actually open the camera — that's a real,
// browser-permitted (WHATWG HTML) inconsistency, most notably on iOS
// Safari, that no source-level check can prove one way or the other. These
// tests only prove the two inputs stay correctly, separately configured,
// so a future edit can't silently reintroduce the bug of one input's
// behavior leaking into the other (e.g. capture ending up on both, or
// neither, or the wrong ref being wired to the wrong button).
//
// Run with:
//   cd frontend && node --test src/components/*.test.mjs
// (or `npm test`, see package.json)
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "Controls.jsx"), "utf8");

// ── the two <input type="file"> elements, isolated for inspection ───────

function fileInputBlocks(source) {
  // Each <input ... /> in this file is self-closing and stays on its own
  // small block — split on that boundary rather than a fragile single
  // regex, so a reordered/reformatted attribute doesn't break the test.
  return source.match(/<input\s+type="file"[\s\S]*?\/>/g) || [];
}

test("exactly two file inputs exist: one plain (attach), one with capture (camera)", () => {
  const blocks = fileInputBlocks(src);
  assert.equal(blocks.length, 2, "expected exactly two <input type=\"file\"> elements");

  const withCapture = blocks.filter((b) => /capture=/.test(b));
  const withoutCapture = blocks.filter((b) => !/capture=/.test(b));
  assert.equal(withCapture.length, 1, "exactly one file input should request capture");
  assert.equal(withoutCapture.length, 1, "exactly one file input should stay a plain picker");
});

test("the camera input uses the standards-based capture=\"environment\" hint", () => {
  const blocks = fileInputBlocks(src);
  const cameraBlock = blocks.find((b) => /capture=/.test(b));
  assert.ok(cameraBlock, "no capture input found");
  assert.match(cameraBlock, /capture="environment"/);
  assert.match(cameraBlock, /accept="image\/\*"/);
  assert.match(cameraBlock, /ref=\{cameraInputRef\}/);
});

test("the attach input has no capture attribute and accepts any image", () => {
  const blocks = fileInputBlocks(src);
  const attachBlock = blocks.find((b) => !/capture=/.test(b));
  assert.ok(attachBlock, "no plain file input found");
  assert.match(attachBlock, /accept="image\/\*"/);
  assert.match(attachBlock, /ref=\{fileInputRef\}/);
});

// ── the two buttons target the correct, distinct refs ────────────────────

function buttonBlocks(source) {
  return source.match(/<button\b[\s\S]*?<\/button>/g) || [];
}

test("the 📎 button clicks fileInputRef and the 📷 button clicks cameraInputRef (never swapped)", () => {
  const attachBtn = buttonBlocks(src).find((b) => b.includes("📎"));
  const cameraBtn = buttonBlocks(src).find((b) => b.includes("📷"));
  assert.ok(attachBtn, "no 📎 button found");
  assert.ok(cameraBtn, "no 📷 button found");
  assert.match(attachBtn, /onClick=\{\(\) => fileInputRef\.current\?\.click\(\)\}/,
    "📎 button must trigger fileInputRef");
  assert.match(cameraBtn, /onClick=\{\(\) => cameraInputRef\.current\?\.click\(\)\}/,
    "📷 button must trigger cameraInputRef");
});

// ── picked images are tagged by source, and only flow through the
//    existing onSendImage prop — never a second/parallel send path ──────

test("both inputs' onChange route through the same pickImage()/onSendImage pipeline", () => {
  assert.match(src, /pickImage\(e\.target\.files\?\.\[0\], "file"\)/);
  assert.match(src, /pickImage\(e\.target\.files\?\.\[0\], "camera"\)/);
  // Exactly one send path out of this component — no parallel/second
  // image pipeline was introduced for the camera case.
  const sendCalls = src.match(/onSendImage\?\.\(/g) || [];
  assert.equal(sendCalls.length, 1, "there must be exactly one onSendImage call site");
});

test("retake re-opens the SAME camera input, not a new capture mechanism", () => {
  assert.match(src, /function retakePhoto\(\)/);
  assert.match(src, /retakePhoto[\s\S]{0,120}cameraInputRef\.current\?\.click\(\)/);
  // No getUserMedia/WebRTC/custom camera preview was introduced.
  assert.doesNotMatch(src, /getUserMedia/);
  assert.doesNotMatch(src, /RTCPeerConnection/);
});
