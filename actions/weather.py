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
"""
from __future__ import annotations

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


def _describe_code(code) -> str:
    try:
        return _WMO_DESCRIPTIONS.get(int(code), "unknown conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


def get_weather_text(latitude: float, longitude: float, place_label: str = "") -> str:
    """Fetches current conditions + a short forecast from Open-Meteo for
    the given coordinates and returns a compact, natural-language-ready
    text block for Gemini to summarize in its own words -- never a
    pre-written sentence spoken verbatim, and never fabricated data (see
    this module's own docstring for how a failure is handled instead)."""
    resp = requests.get(
        OPEN_METEO_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
            "timezone": "auto",
            "forecast_days": 3,
        },
        timeout=HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()

    current = data["current"]
    lines = []
    if place_label:
        lines.append(f"Weather for {place_label}:")
    lines.append(
        f"Current: {current['temperature_2m']}{data['current_units']['temperature_2m']}, "
        f"feels like {current['apparent_temperature']}{data['current_units']['apparent_temperature']}, "
        f"{_describe_code(current['weather_code'])}, "
        f"wind {current['wind_speed_10m']}{data['current_units']['wind_speed_10m']}, "
        f"precipitation {current['precipitation']}{data['current_units']['precipitation']} right now."
    )

    daily = data.get("daily")
    if daily and daily.get("time"):
        day_labels = ["Today", "Tomorrow", "Day after tomorrow"]
        for i, date in enumerate(daily["time"][:3]):
            label = day_labels[i] if i < len(day_labels) else date
            lines.append(
                f"{label} ({date}): {_describe_code(daily['weather_code'][i])}, "
                f"high {daily['temperature_2m_max'][i]}{data['daily_units']['temperature_2m_max']}, "
                f"low {daily['temperature_2m_min'][i]}{data['daily_units']['temperature_2m_min']}, "
                f"chance of precipitation {daily['precipitation_probability_max'][i]}"
                f"{data['daily_units']['precipitation_probability_max']}, "
                f"total precipitation {daily['precipitation_sum'][i]}{data['daily_units']['precipitation_sum']}."
            )

    return "\n".join(lines)
