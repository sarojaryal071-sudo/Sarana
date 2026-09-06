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

export default function CalendarPresentation({ data }) {
  const [yearStr, monthStr] = (data?.month || "").split("-");
  const year = parseInt(yearStr, 10);
  const month = parseInt(monthStr, 10);
  const validMonth = Number.isInteger(year) && Number.isInteger(month) && month >= 1 && month <= 12;
  const marked = new Set(Array.isArray(data?.marked_dates) ? data.marked_dates : []);
  const focusDate = data?.focus_date || null;
  const events = Array.isArray(data?.events) ? data.events : [];

  if (!validMonth) {
    return <div className="pw-calendar-empty">No calendar month to show.</div>;
  }

  const cells = buildMonthGrid(year, month);

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
          const isFocused = iso === focusDate;
          return (
            <div
              key={iso}
              className={`pw-calendar-cell${isMarked ? " pw-calendar-cell-marked" : ""}${isFocused ? " pw-calendar-cell-focused" : ""}`}
            >
              {day}
            </div>
          );
        })}
      </div>
      {(focusDate || events.length > 0) && (
        <div className="pw-calendar-events">
          {focusDate && <div className="pw-calendar-events-heading">{focusDate}</div>}
          {events.length === 0 ? (
            <div className="pw-calendar-events-empty">No events.</div>
          ) : (
            events.map((ev) => (
              <div className="pw-calendar-event" key={ev.id || `${ev.title}-${ev.start}`}>
                <span className="pw-calendar-event-time">{formatEventTime(ev.start, ev.all_day)}</span>
                <span className="pw-calendar-event-title">{ev.title}</span>
                {ev.location && <span className="pw-calendar-event-location">{ev.location}</span>}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
