// src/components/presentation/CalendarPresentation.jsx — a real month
// grid with real dates, never a fake/decorative calendar icon. `data`
// (see main.py's get_calendar_events dispatch):
//   month: "YYYY-MM"
//   marked_dates: ["YYYY-MM-DD", ...]  — real event dates this month
//     (actions/calendar.py's get_month_marked_dates() — a second,
//     bounded, real Google Calendar query, never invented/sampled)
//   focus_date: "YYYY-MM-DD" | null    — set only when the user's own
//     request WAS a single day ("what's on the 18th") — see
//     _extract_commit_reference-style honesty: never guessed
//   events: [{id, title, start, end, location, all_day}, ...] — exactly
//     the caller's own originally-requested range (the whole-month
//     grid's marks and the events LIST are deliberately two separate,
//     independently real queries — see main.py's own comment)
//
// The grid itself is plain date math (Monday-first weeks, ISO-style) —
// no calendar library, matching this project's own "no new dependency
// for a solvable-with-plain-JS problem" precedent (see
// IdentityTransition.jsx's own trig-not-a-physics-library choice).
//
// Production-polish addition ("calendar interaction" audit item):
// clicking any day in the grid re-filters the events list below to
// that day — entirely client-side, from the SAME `events` array the
// backend already sent, never a new backend round-trip/WS message
// (section "important architectural constraints": no new event bus).
// Clicking a day outside the originally-fetched range honestly shows
// "No events" (this component genuinely doesn't know what's there,
// having never been sent it) rather than fabricating anything.
import { useState } from "react";

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function buildMonthGrid(year, month) {
  // month is 1-12
  const first = new Date(year, month - 1, 1);
  const daysInMonth = new Date(year, month, 0).getDate();
  const firstWeekday = (first.getDay() + 6) % 7; // 0=Mon..6=Sun
  const cells = [];
  for (let i = 0; i < firstWeekday; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

function isoDate(year, month, day) {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function formatEventTime(iso, allDay) {
  if (!iso) return "";
  if (allDay) return "All day";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

// "FRIDAY, SEPTEMBER 11, 2026" — built from the ISO string's own y/m/d
// fields via new Date(y, m-1, d) (LOCAL construction), never
// new Date(isoString) directly: that parses a bare "YYYY-MM-DD" as UTC
// midnight, which can print the WRONG day once shifted to a negative-
// UTC-offset local zone — the exact off-by-one class of bug
// eventDateIso()'s own comment already guards against for event dates.
function formatDayHeading(iso) {
  const [y, m, d] = (iso || "").split("-").map(Number);
  if (!y || !m || !d) return iso || "";
  const dt = new Date(y, m - 1, d);
  return dt.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

function eventDateIso(ev) {
  const raw = ev?.start;
  if (!raw) return null;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return null;
  // All-day events carry a bare "YYYY-MM-DD" (no time component) --
  // `new Date()` parses that as UTC midnight, so read the date fields
  // straight from the string rather than through the Date object's own
  // (locale-shifted) getters to avoid an off-by-one-day near midnight.
  if (ev.all_day && /^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Universal Information Surface's own compact/expanded state (see
// PresentationSurface.jsx) — a compact glance shows a few upcoming
// events; "expand that"/"show me more" reveals the rest of whatever
// range the backend already sent (never a new fetch).
const COMPACT_EVENT_COUNT = 4;

// The reference cards' own circled "current day" read — a real local
// date comparison, independent of `marked` (has-events, kept RED per
// the explicit brief — see index.css's own .pw-calendar-cell-marked):
// today can be marked or not, the ring is a separate, honest signal.
function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Shared by the day view AND the month view's own "click a day to
// preview it" list — one rendering of "compact/expanded event list, or
// an honest empty state", never duplicated per view.
function CalendarEventList({ events, expanded, emptyText }) {
  const shown = expanded ? events : events.slice(0, COMPACT_EVENT_COUNT);
  const hidden = events.length - shown.length;
  if (events.length === 0) {
    return <div className="pw-calendar-events-empty">{emptyText}</div>;
  }
  return (
    <>
      {shown.map((ev, i) => (
        <div className="pw-calendar-event pw-reveal-item" style={{ "--pw-reveal-index": i }} key={ev.id || `${ev.title}-${ev.start}`}>
          <span className="pw-calendar-event-time">{formatEventTime(ev.start, ev.all_day)}</span>
          <span className="pw-calendar-event-title">{ev.title}</span>
          {ev.location && <span className="pw-calendar-event-location">{ev.location}</span>}
        </div>
      ))}
      {!expanded && hidden > 0 && (
        <div className="pw-calendar-more-hint">+{hidden} more — expand for the full list</div>
      )}
    </>
  );
}

export default function CalendarPresentation({ data, expanded = false }) {
  const focusDate = data?.focus_date || null;
  const events = Array.isArray(data?.events) ? data.events : [];
  // Local, month-view-only preview state (clicking a day in the grid) —
  // deliberately never seeded from focusDate: a real focusDate now
  // renders the dedicated day view below instead, so this only ever
  // matters for the grid's own "peek at a day without navigating away"
  // interaction. Called unconditionally, before any early return, per
  // React's own rules of hooks.
  const [selectedDate, setSelectedDate] = useState(null);

  // A specific single date was requested (main.py's own single-day
  // range detection — see that file's get_calendar_events dispatch and
  // this component's own header) — real, reported bug fixed: this used
  // to always render the full month grid even for "open the 11th",
  // with that day's events merely filtered into a list underneath it.
  // Now a dedicated day view: just that date and its real events, or an
  // honest "No events on this day" — never the month grid.
  if (focusDate) {
    return (
      <div className="pw-calendar pw-calendar-day-view">
        <div className="pw-calendar-day-heading">{formatDayHeading(focusDate)}</div>
        <div className="pw-calendar-events pw-calendar-events-solo">
          <CalendarEventList events={events} expanded={expanded} emptyText="No events on this day." />
        </div>
      </div>
    );
  }

  const [yearStr, monthStr] = (data?.month || "").split("-");
  const year = parseInt(yearStr, 10);
  const month = parseInt(monthStr, 10);
  const validMonth = Number.isInteger(year) && Number.isInteger(month) && month >= 1 && month <= 12;
  const marked = new Set(Array.isArray(data?.marked_dates) ? data.marked_dates : []);

  if (!validMonth) {
    return <div className="pw-calendar-empty">No calendar month to show.</div>;
  }

  const cells = buildMonthGrid(year, month);
  const today = todayIso();
  const filteredEvents = selectedDate ? events.filter((ev) => eventDateIso(ev) === selectedDate) : events;

  return (
    <div className="pw-calendar">
      <div className="pw-calendar-header">{MONTH_NAMES[month - 1]} {year}</div>
      <div className="pw-calendar-grid">
        {WEEKDAY_LABELS.map((w) => (
          <div className="pw-calendar-weekday" key={w}>{w}</div>
        ))}
        {cells.map((day, i) => {
          if (day == null) return <div className="pw-calendar-cell pw-calendar-cell-empty" key={`e${i}`} />;
          const iso = isoDate(year, month, day);
          const isMarked = marked.has(iso);
          const isSelected = iso === selectedDate;
          const isToday = iso === today;
          return (
            <button
              key={iso}
              type="button"
              className={`pw-calendar-cell${isMarked ? " pw-calendar-cell-marked" : ""}${isToday ? " pw-calendar-cell-today" : ""}${isSelected ? " pw-calendar-cell-focused" : ""}`}
              onClick={() => setSelectedDate((cur) => (cur === iso ? null : iso))}
              aria-pressed={isSelected}
              aria-label={`${MONTH_NAMES[month - 1]} ${day}, ${year}${isMarked ? " (has events)" : ""}`}
            >
              {day}
            </button>
          );
        })}
      </div>
      {(selectedDate || events.length > 0) && (
        <div className="pw-calendar-events">
          {selectedDate && <div className="pw-calendar-events-heading">{selectedDate}</div>}
          <CalendarEventList events={filteredEvents} expanded={expanded} emptyText="No events." />
        </div>
      )}
    </div>
  );
}
