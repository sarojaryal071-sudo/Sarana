// src/components/presentation/PresentationSurface.test.mjs — Track 3's
// Presentation Engine: payload validation, type routing, lifecycle
// phases, theme selection, and the six initial visual modules. Source-
// inspection style, same convention as every other component test in
// this project (see IdentityTransition.test.mjs's own header note).
//
// Run with:
//   cd frontend && node --test src/components/presentation/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
function read(name) {
  return fs.readFileSync(path.join(__dirname, name), "utf8");
}

const registrySrc = read("registry.js");
const surfaceSrc = read("PresentationSurface.jsx");
const contentPanelSrc = fs.readFileSync(path.join(__dirname, "..", "ContentPanel.jsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "..", "index.css"), "utf8");
const weatherSrc = read("WeatherPresentation.jsx");
const calendarSrc = read("CalendarPresentation.jsx");
const tableSrc = read("TablePresentation.jsx");
const searchSrc = read("SearchResultsPresentation.jsx");
const genericSrc = read("GenericInfoPresentation.jsx");
const statusSrc = read("StatusPresentation.jsx");

// ── registry ──────────────────────────────────────────────────────────

test("registry maps exactly the six initial presentation modules, no more, no fewer", () => {
  const match = registrySrc.match(/PRESENTATION_REGISTRY = \{([\s\S]*?)\};/);
  assert.ok(match, "expected a PRESENTATION_REGISTRY object literal");
  const keys = [...match[1].matchAll(/^\s*(\w+):/gm)].map((m) => m[1]);
  assert.deepEqual(
    keys.sort(),
    ["calendar", "generic_information", "search_results", "status", "table", "weather"].sort(),
  );
});

test("isValidPresentation requires an object with a known `type` — never accepts an unregistered type", () => {
  assert.match(registrySrc, /function isValidPresentation\(presentation\)/);
  assert.match(registrySrc, /PRESENTATION_REGISTRY\[presentation\.type\]/);
});

test("resolvePresentationComponent returns null (not a throw/undefined-crash) for an unknown type", () => {
  assert.match(registrySrc, /return PRESENTATION_REGISTRY\[type\] \|\| null;/);
});

// ── ContentPanel: the one mount point ────────────────────────────────

test("ContentPanel renders nothing when content is null — HIDDEN is 'not mounted', no separate hidden DOM", () => {
  assert.match(contentPanelSrc, /if \(!content\) return null;/);
});

test("ContentPanel hosts exactly ONE PresentationSurface — no per-type modal/panel component of its own", () => {
  assert.match(contentPanelSrc, /<PresentationSurface content=\{content\} theme=\{theme\} onDismiss=\{onDismiss\} \/>/);
  assert.doesNotMatch(contentPanelSrc, /WeatherPresentation|CalendarPresentation|TablePresentation/, "ContentPanel must never import a typed renderer directly — only PresentationSurface does, via the registry");
});

// ── PresentationSurface: lifecycle, validation, theme ────────────────

test("falls back to plain text for an invalid/unsupported presentation payload, never crashes", () => {
  assert.match(surfaceSrc, /const valid = isValidPresentation\(presentation\);/);
  assert.match(surfaceSrc, /TypedRenderer \? <TypedRenderer data=\{presentation\.data\} \/> : <div className="pw-surface-plain">\{content\.text\}<\/div>/);
});

test("lifecycle phases: materializing -> active, and updating on a later content change while still mounted", () => {
  assert.match(surfaceSrc, /setPhase\("materializing"\)/);
  assert.match(surfaceSrc, /setPhase\("active"\)/);
  assert.match(surfaceSrc, /setPhase\("updating"\)/);
  assert.match(surfaceSrc, /setPhase\("dismissing"\)/);
});

test("an update pulse never fires while still materializing (no double-animation)", () => {
  assert.match(surfaceSrc, /if \(phase === "materializing"\) return undefined;/);
});

test("dismiss unmounts via the caller's onDismiss after the dismiss transition, not instantly", () => {
  assert.match(surfaceSrc, /dismissTimerRef\.current = setTimeout\(onDismiss, DISMISS_MS\)/);
});

test("production polish: the dismiss timer is tracked and cleared on unmount, not left dangling", () => {
  assert.match(surfaceSrc, /const dismissTimerRef = useRef\(null\);/);
  assert.match(surfaceSrc, /useEffect\(\(\) => \(\) => clearTimeout\(dismissTimerRef\.current\), \[\]\);/);
});

test("expand/collapse is a local toggle, independent of the lifecycle phase", () => {
  assert.match(surfaceSrc, /const \[expanded, setExpanded\] = useState\(false\)/);
  assert.match(surfaceSrc, /setExpanded\(\(e\) => !e\)/);
});

test("theme is applied via a CSS class, driven by the caller's own already-computed identity — no second theme source", () => {
  assert.match(surfaceSrc, /pw-surface-theme-\$\{theme\}/);
});

test("is accessible: aria-live for content changes, aria-expanded/aria-label on the interactive buttons", () => {
  assert.match(surfaceSrc, /aria-live="polite"/);
  assert.match(surfaceSrc, /aria-expanded=\{expanded\}/);
  assert.match(surfaceSrc, /aria-label="Dismiss"/);
});

// ── CSS: restraint, theming, accessibility ───────────────────────────

test("glass surface CSS is restrained — one border, one shadow, a modest blur, not stacked glow effects", () => {
  const rule = css.match(/\.pw-surface\s*\{[\s\S]*?\n\}/);
  assert.ok(rule);
  assert.match(rule[0], /backdrop-filter:\s*blur/);
  const shadowDeclarations = rule[0].match(/box-shadow:/g) || [];
  assert.equal(shadowDeclarations.length, 1, "exactly one box-shadow declaration, not stacked glow layers");
});

test("JARVIS and SARANA each contribute their own accent via a CSS custom property, reusing the existing --acc/--face-glow tokens", () => {
  assert.match(css, /\.pw-surface-theme-jarvis \{ --surface-accent: var\(--acc\); \}/);
  assert.match(css, /\.pw-surface-theme-sarana \{ --surface-accent: var\(--face-glow\); \}/);
});

test("respects prefers-reduced-motion — materialize/dismiss transforms are suppressed", () => {
  const anchor = css.indexOf(".pw-surface-plain {");
  assert.ok(anchor > -1);
  const following = css.slice(anchor, anchor + 400);
  assert.match(following, /@media \(prefers-reduced-motion: reduce\) \{/);
  assert.match(following, /\.pw-surface,\s*\n\s*\.pw-surface-hdr \{[\s\S]*?transition: none !important;/);
  assert.match(following, /\.pw-surface-materializing,\s*\n\s*\.pw-surface-dismissing \{ opacity: 1; transform: none; \}/);
});

test("marked calendar days use --red specifically, matching the explicit brief ('visually marked RED')", () => {
  assert.match(css, /\.pw-calendar-cell-marked \{ color: var\(--red\); border-color: var\(--red\)/);
});

test("interactive buttons have a visible focus state (keyboard accessibility)", () => {
  assert.match(css, /\.pw-surface-btn:focus-visible \{ outline:/);
});

// ── typed renderers: real data only, no fabrication ──────────────────

test("WeatherPresentation renders only fields from `data` — no hardcoded temperature/condition values", () => {
  assert.doesNotMatch(weatherSrc, /\b\d{2,3}°/, "no literal degree value baked into the component");
  assert.match(weatherSrc, /current\.temperature/);
  assert.match(weatherSrc, /daily\.map/);
});

test("CalendarPresentation computes a REAL month grid from actual Date math — no calendar library, no invented events", () => {
  assert.match(calendarSrc, /function buildMonthGrid\(year, month\)/);
  assert.match(calendarSrc, /new Date\(year, month - 1, 1\)/);
  // "react" itself is not a NEW dependency (every component already
  // uses it) -- CalendarPresentation legitimately needs useState for
  // its own client-side day-selection (see that component's own
  // "calendar interaction" production-polish addition). The actual
  // guard is: no OTHER, genuinely new package for date math.
  assert.doesNotMatch(calendarSrc, /from ["'](?!\.\.?\/|react["'])/m, "no new dependency for solvable-with-plain-JS date math");
  // marking uses the backend-supplied marked_dates set, never a guess
  assert.match(calendarSrc, /marked\.has\(iso\)/);
});

test("CalendarPresentation only shows a focus_date drill-down when the backend actually supplied one", () => {
  assert.match(calendarSrc, /const focusDate = data\?\.focus_date \|\| null;/);
});

test("production polish: clicking a day filters the ALREADY-FETCHED events client-side, never a new backend request", () => {
  assert.match(calendarSrc, /const \[selectedDate, setSelectedDate\] = useState\(focusDate\);/);
  assert.match(calendarSrc, /events\.filter\(\(ev\) => eventDateIso\(ev\) === selectedDate\)/);
  assert.doesNotMatch(calendarSrc, /fetch\(|sendCommand|WebSocket|new XMLHttpRequest/, "day selection must stay entirely client-side");
});

test("day cells are real, keyboard-accessible buttons, not click-handler divs", () => {
  assert.match(calendarSrc, /<button[\s\S]{0,80}type="button"/);
  assert.match(calendarSrc, /aria-pressed=\{isSelected\}/);
});

test("SearchResultsPresentation never re-parses free text into fabricated structured result objects", () => {
  assert.doesNotMatch(searchSrc, /JSON\.parse|\.split\("http/);
  assert.match(searchSrc, /data\?\.text \|\| ""\)\.split\("\\n"\)/);
});

test("TablePresentation renders exactly the caller-supplied columns/rows, no synthetic columns added", () => {
  assert.match(tableSrc, /columns\.map/);
  assert.match(tableSrc, /rows\.map/);
});

test("GenericInfoPresentation supports both freeform body and structured items, honest empty state when neither is given", () => {
  assert.match(genericSrc, /pw-generic-empty/);
});

test("StatusPresentation only draws a progress bar when a real numeric progress value was given, never a fake indeterminate fill", () => {
  assert.match(statusSrc, /typeof data\?\.progress === "number"/);
  assert.match(statusSrc, /hasProgress &&/);
});
