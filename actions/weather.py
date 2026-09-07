"""
actions/weather.py -- real weather data via Open-Meteo (free, keyless).

Replaces the old actions/weather_report.py, which didn't fetch any real
weather data at all -- it called webbrowser.open() on a Google search URL,
on whichever machine main.py happens to be running on. On desktop that
opens the user's own browser tab (marginal, but not weather data spoken
aloud); on the web/Render deployment it opens a browser ON THE SERVER and
reports fake success back to the user. This module returns real,
structured weather text instead, on every surface.

Same plain-synchronous-function shape as every other actions/*.py module
(see actions/geo.py's own docstring for the fuller rationale) --
main.py's _execute_tool() dispatches to it via loop.run_in_executor(),
exactly like every other tool. Coordinates are resolved by main.py
(either from the current session location, or by geocoding a named place
via actions/geo.py) and passed in here already resolved -- this module
never touches session state.

Failures (network errors, a malformed/unexpected provider response) are
deliberately NOT caught here -- they propagate up to _execute_tool()'s
existing generic exception handling, which already turns any tool
failure into an honest, spoken explanation instead of silently
fabricating an answer. This matches every other action in this codebase.

Track 3 (Presentation Engine) addition: `get_weather_data()` is the SAME
real Open-Meteo fetch, reshaped into a structured dict instead of prose --
added so main.py can hand real weather data to the frontend's
WeatherPresentation (see frontend/src/components/presentation/
WeatherPresentation.jsx) without a second HTTP call or a second parser.
`get_weather_text()` is now a thin formatter OVER `get_weather_data()` --
same fetch, same fields, byte-identical output to before this change
(verified via tests/test_weather.py) -- Gemini's own spoken-weather path
is completely unaffected.
"""
from __future__ import annotations

from datetime import date as _date

import requests

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT_S = 8

# WMO weather codes (used by Open-Meteo) -> a short, natural description.
# https://open-meteo.com/en/docs -- "WMO Weather interpretation codes"
_WMO_DESCRIPTIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow fall", 73: "moderate snow fall", 75: "heavy snow fall",
    77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}

_DAY_LABELS = ["Today", "Tomorrow", "Day after tomorrow"]


def _describe_code(code) -> str:
    try:
        return _WMO_DESCRIPTIONS.get(int(code), "unknown conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


def get_weather_data(latitude: float, longitude: float, place_label: str = "") -> dict:
    """Fetches current conditions + a 7-day forecast from Open-Meteo and
    returns it as a structured dict -- the one real HTTP call/parse this
    module makes; get_weather_text() below formats the SAME dict into
    prose rather than fetching separately. 7 days (not 3) so a genuine
    "show me the next five days"/"expand that" request has real data to
    reveal -- WeatherPresentation.jsx's own `expanded` prop decides how
    many of these get shown by default vs. on request; the fetch itself
    is deliberately never re-done just because the user asks to see more
    of what was already fetched. Shape:
        {
          "location": str,               # place_label, "" if none given
          "current": {
            "temperature": float, "unit": str,
            "feels_like": float, "feels_like_unit": str,
            "condition": str,            # e.g. "partly cloudy"
            "wind": float, "wind_unit": str,
            "precipitation": float, "precipitation_unit": str,
          },
          "daily": [
            {"label": str, "date": str, "condition": str,
             "high": float, "low": float, "temp_unit": str,
             "precip_probability": float, "precip_probability_unit": str},
            ...
          ],
        }
    Raises exactly what requests/the JSON parse would raise -- callers
    (main.py's _execute_tool()) already handle that honestly; this
    function never catches/hides a real failure (see module docstring)."""
    resp = requests.get(
        OPEN_METEO_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
            # (kept identical to the pre-split request -- see the daily
            # loop below, which still reports precipitation_sum per day)
            "timezone": "auto",
            "forecast_days": 7,
        },
        timeout=HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()

    current = data["current"]
    current_units = data["current_units"]
    structured = {
        "location": place_label or "",
        "current": {
            "temperature": current["temperature_2m"],
            "unit": current_units["temperature_2m"],
            "feels_like": current["apparent_temperature"],
            "feels_like_unit": current_units["apparent_temperature"],
            "condition": _describe_code(current["weather_code"]),
            "wind": current["wind_speed_10m"],
            "wind_unit": current_units["wind_speed_10m"],
            "precipitation": current["precipitation"],
            "precipitation_unit": current_units["precipitation"],
        },
        "daily": [],
    }

    daily = data.get("daily")
    daily_units = data.get("daily_units", {})
    if daily and daily.get("time"):
        for i, date in enumerate(daily["time"][:7]):
            if i < len(_DAY_LABELS):
                label = _DAY_LABELS[i]
            else:
                # Day 4+: a real weekday name ("Friday"), not the raw
                # ISO date string -- still honestly derived from the
                # actual date Open-Meteo returned, never guessed.
                try:
                    label = _date.fromisoformat(date).strftime("%A")
                except ValueError:
                    label = date
            structured["daily"].append({
                "label": label,
                "date": date,
                "condition": _describe_code(daily["weather_code"][i]),
                "high": daily["temperature_2m_max"][i],
                "low": daily["temperature_2m_min"][i],
                "temp_unit": daily_units.get("temperature_2m_max", ""),
                "precip_probability": daily["precipitation_probability_max"][i],
                "precip_probability_unit": daily_units.get("precipitation_probability_max", ""),
                "precip_total": daily["precipitation_sum"][i],
                "precip_total_unit": daily_units.get("precipitation_sum", ""),
            })

    return structured


def format_weather_text(w: dict) -> str:
    """Pure formatter: structured get_weather_data() output -> the SAME
    compact, natural-language-ready text get_weather_text() has always
    returned (see that function's own docstring). Split out so a caller
    that already has the structured dict (main.py -- see its own
    presentation-broadcast use) never fetches Open-Meteo twice just to
    get both the text and the structured shape for one response.

    Deliberately only reads out the first 3 of `w["daily"]`'s now-7
    entries -- get_weather_data() fetches a full week so the VISUAL
    presentation can expand into it on request (see
    WeatherPresentation.jsx's own `expanded` prop), but Gemini's SPOKEN
    reply staying compact by default is a real, separate concern: nobody
    wants "what's the weather" answered with a 7-day recitation. If the
    user explicitly asks for more days ("what about this week"), that
    request itself reaches Gemini as ordinary conversation text, not
    through this fixed formatter."""
    cur = w["current"]

    lines = []
    if w["location"]:
        lines.append(f"Weather for {w['location']}:")
    lines.append(
        f"Current: {cur['temperature']}{cur['unit']}, "
        f"feels like {cur['feels_like']}{cur['feels_like_unit']}, "
        f"{cur['condition']}, "
        f"wind {cur['wind']}{cur['wind_unit']}, "
        f"precipitation {cur['precipitation']}{cur['precipitation_unit']} right now."
    )
    for day in w["daily"][:3]:
        lines.append(
            f"{day['label']} ({day['date']}): {day['condition']}, "
            f"high {day['high']}{day['temp_unit']}, "
            f"low {day['low']}{day['temp_unit']}, "
            f"chance of precipitation {day['precip_probability']}{day['precip_probability_unit']}, "
            f"total precipitation {day['precip_total']}{day['precip_total_unit']}."
        )

    return "\n".join(lines)


def get_weather_text(latitude: float, longitude: float, place_label: str = "") -> str:
    """Compact, natural-language-ready text block for Gemini to summarize
    in its own words -- never a pre-written sentence spoken verbatim, and
    never fabricated data (see module docstring for how a failure is
    handled instead). A thin fetch-then-format over get_weather_data()/
    format_weather_text() -- same fetch, same fields, same output shape
    as before this function was split."""
    return format_weather_text(get_weather_data(latitude, longitude, place_label))
