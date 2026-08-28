"""
actions/calendar.py -- Google Calendar API operations: read events, find
free time, create/update/delete events. Same plain-function convention
every other actions/*.py module here already uses (see actions/geo.py's
own docstring for the fuller rationale): every function takes an
already-resolved Credentials object and an already-resolved local
`tzinfo` -- this module has no notion of "which SARANA user" or "current
session" at all, and never touches Postgres, OAuth, or main.py's
JarvisLive state directly.

Time handling: every datetime string this module receives from Gemini
(via main.py's _execute_tool()) is expected to be a NAIVE local ISO
string (e.g. "2026-08-29T15:00:00") -- Gemini computes it from the
[CURRENT DATE & TIME] context it already has (see main.py's
_build_config()), which is itself derived from the user's real device-
local time (main.py's _local_now()/_web_timezone), never server/UTC
time. parse_local_datetime() attaches the caller's resolved tzinfo to
that naive string -- this module never assumes or hardcodes a timezone
itself.

Failures (network errors, Google API errors, malformed responses) are
deliberately NOT caught here -- they propagate to _execute_tool()'s
existing generic exception handling, which already turns any tool
failure into an honest response instead of fabricating calendar data.
This matches actions/weather.py, actions/geo.py, and actions/routing.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta

try:
    from googleapiclient.discovery import build
    _API_OK = True
except ImportError:                      # pragma: no cover — optional dependency
    build = None
    _API_OK = False

DEFAULT_EVENT_DURATION_MINUTES = 60
DEFAULT_FREE_SLOT_MINUTES = 30
DEFAULT_DAY_START_HOUR = 8    # find_free_slots never proposes a slot before this local hour...
DEFAULT_DAY_END_HOUR = 20     # ...or at/after this one, even if the calendar is technically empty then.
MAX_EVENTS_RETURNED = 25
MAX_FREE_SLOTS_RETURNED = 10


def _service(credentials):
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def parse_local_datetime(dt_str: str, tzinfo) -> datetime:
    """Parses a local ISO datetime string and attaches `tzinfo` if the
    string itself has no offset (the expected case -- see this module's
    own docstring). Tolerates an already-offset-aware string too (in case
    Gemini includes one anyway) by leaving it as-is rather than
    overriding it. Raises ValueError on a genuinely malformed string --
    callers must treat that as "ask the user for a valid time", never
    silently default one."""
    dt = datetime.fromisoformat(dt_str.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzinfo)
    return dt


def _summarize_event(item: dict) -> dict:
    start = item.get("start", {})
    end = item.get("end", {})
    return {
        "id": item.get("id", ""),
        "title": item.get("summary") or "(no title)",
        "start": start.get("dateTime") or start.get("date", ""),
        "end": end.get("dateTime") or end.get("date", ""),
        "location": item.get("location", ""),
        "all_day": "date" in start and "dateTime" not in start,
    }


# ── read ─────────────────────────────────────────────────────────────

def get_events(
    credentials, *, time_min: datetime, time_max: datetime, max_results: int = MAX_EVENTS_RETURNED,
) -> list[dict]:
    service = _service(credentials)
    resp = service.events().list(
        calendarId="primary",
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=max_results,
    ).execute()
    return [_summarize_event(item) for item in resp.get("items", [])]


def format_events(events: list[dict]) -> str:
    """Natural-language-ready text for Gemini to summarize in its own
    words -- never a pre-written sentence, never fabricated data (an
    empty calendar is reported honestly, not glossed over)."""
    if not events:
        return "No events found in that range."
    lines = ["Calendar events (chronological):"]
    for ev in events:
        when = ev["start"] if ev["all_day"] else f"{ev['start']} to {ev['end']}"
        loc = f" @ {ev['location']}" if ev["location"] else ""
        lines.append(f"- [{ev['id']}] {ev['title']}: {when}{loc}")
    return "\n".join(lines)


def find_events_matching(credentials, tzinfo, *, query: str, day: datetime) -> list[dict]:
    """Searches events on `day` (any datetime that date-falls on the
    intended day) whose title contains `query` (case-insensitive).
    Used by update/delete when Gemini doesn't already have a specific
    event_id -- see main.py's update_calendar_event/delete_calendar_event
    handling for why this never guesses which event was meant."""
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    events = get_events(credentials, time_min=day_start, time_max=day_end, max_results=50)
    q = (query or "").lower().strip()
    if not q:
        return events
    return [e for e in events if q in e["title"].lower()]


# ── free time ────────────────────────────────────────────────────────

def find_free_slots(
    credentials, tzinfo, *, window_start: datetime, window_end: datetime,
    duration_minutes: int = DEFAULT_FREE_SLOT_MINUTES,
    day_start_hour: int = DEFAULT_DAY_START_HOUR, day_end_hour: int = DEFAULT_DAY_END_HOUR,
) -> list[tuple[datetime, datetime]]:
    """Fetches events in [window_start, window_end) and returns free gaps
    of at least `duration_minutes`, bounded to a sensible waking-hours
    window each day in range -- never proposes a slot at 3 AM just
    because the calendar happens to be empty then."""
    events = get_events(credentials, time_min=window_start, time_max=window_end, max_results=50)
    busy = []
    for ev in events:
        if ev["all_day"]:
            continue
        try:
            s = datetime.fromisoformat(ev["start"])
            e = datetime.fromisoformat(ev["end"])
        except ValueError:
            continue
        busy.append((s, e))
    busy.sort()

    duration = timedelta(minutes=duration_minutes)
    free: list[tuple[datetime, datetime]] = []

    day = window_start.date()
    end_day = window_end.date()
    while day <= end_day:
        day_open = datetime.combine(day, datetime.min.time(), tzinfo=tzinfo).replace(hour=day_start_hour)
        day_close = datetime.combine(day, datetime.min.time(), tzinfo=tzinfo).replace(hour=day_end_hour)
        span_start = max(day_open, window_start)
        span_end = min(day_close, window_end)
        if span_start < span_end:
            pointer = span_start
            for s, e in busy:
                if e <= pointer or s >= span_end:
                    continue
                if s > pointer and (s - pointer) >= duration:
                    free.append((pointer, s))
                pointer = max(pointer, e)
            if span_end > pointer and (span_end - pointer) >= duration:
                free.append((pointer, span_end))
        day = day + timedelta(days=1)

    return free[:MAX_FREE_SLOTS_RETURNED]


def format_free_slots(slots: list[tuple[datetime, datetime]], duration_minutes: int) -> str:
    if not slots:
        return f"No free slots of at least {duration_minutes} minutes found in that window."
    lines = [f"Free slots of at least {duration_minutes} minutes:"]
    for s, e in slots:
        lines.append(f"- {s.isoformat()} to {e.isoformat()}")
    return "\n".join(lines)


# ── write ────────────────────────────────────────────────────────────

def create_event(
    credentials, tzinfo, *, title: str, start: str, end: str | None = None,
    duration_minutes: int | None = None, description: str = "", location: str = "",
    attendees: list[str] | None = None,
) -> dict:
    service = _service(credentials)
    start_dt = parse_local_datetime(start, tzinfo)
    if end:
        end_dt = parse_local_datetime(end, tzinfo)
    else:
        end_dt = start_dt + timedelta(minutes=duration_minutes or DEFAULT_EVENT_DURATION_MINUTES)

    body: dict = {
        "summary": title,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]

    created = service.events().insert(calendarId="primary", body=body).execute()
    return _summarize_event(created)


def update_event(
    credentials, tzinfo, *, event_id: str, title: str | None = None,
    start: str | None = None, end: str | None = None,
    description: str | None = None, location: str | None = None,
) -> dict:
    service = _service(credentials)
    existing = service.events().get(calendarId="primary", eventId=event_id).execute()
    if title is not None:
        existing["summary"] = title
    if start is not None:
        existing["start"] = {"dateTime": parse_local_datetime(start, tzinfo).isoformat()}
    if end is not None:
        existing["end"] = {"dateTime": parse_local_datetime(end, tzinfo).isoformat()}
    if description is not None:
        existing["description"] = description
    if location is not None:
        existing["location"] = location
    updated = service.events().update(calendarId="primary", eventId=event_id, body=existing).execute()
    return _summarize_event(updated)


def delete_event(credentials, event_id: str) -> None:
    service = _service(credentials)
    service.events().delete(calendarId="primary", eventId=event_id).execute()
