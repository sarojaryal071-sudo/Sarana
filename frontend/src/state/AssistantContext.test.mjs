// src/state/AssistantContext.test.mjs — the Universal Information
// Surface's own lifted state (presentationExpanded/presentationPersistent).
// Source-inspection style, same convention as this project's other
// component/reducer tests (the reducer function itself is intentionally
// not exported — only the Provider/hooks are — so behavior is verified
// against the actual reducer source, not a rendered component).
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "AssistantContext.jsx"), "utf8");

test("presentationExpanded/presentationPersistent default to false, and are NOT nested inside `content`", () => {
  assert.match(src, /presentationExpanded: false,/);
  assert.match(src, /presentationPersistent: false,/);
});

test("CONTENT_MESSAGE (an in-place update, e.g. a 'what about tomorrow?' follow-up) never touches expanded/persistent", () => {
  // The explanatory comment right above this case legitimately mentions
  // both field names by name — only the actual `return` statement must
  // avoid them.
  const returnLine = src.match(/case "CONTENT_MESSAGE":\s*\n(?:\s*\/\/.*\n)*\s*(return \{[^\n]*\});/);
  assert.ok(returnLine, "expected to find CONTENT_MESSAGE's return statement");
  assert.doesNotMatch(returnLine[1], /presentationExpanded|presentationPersistent/);
});

test("DISMISS_CONTENT resets both expanded and persistent back to their defaults", () => {
  const block = src.slice(src.indexOf('case "DISMISS_CONTENT"'), src.indexOf('case "PRESENTATION_EXPANDED"'));
  assert.match(block, /content: null, presentationExpanded: false, presentationPersistent: false/);
});

test("PRESENTATION_EXPANDED and PRESENTATION_PERSISTENT are real, independent reducer actions", () => {
  assert.match(src, /case "PRESENTATION_EXPANDED":\s*\n\s*return \{ \.\.\.state, presentationExpanded: !!action\.value \};/);
  assert.match(src, /case "PRESENTATION_PERSISTENT":\s*\n\s*return \{ \.\.\.state, presentationPersistent: !!action\.value \};/);
});
