"""
tests/test_weather_tool.py -- actions/weather.py (Open-Meteo) and
main.py's get_weather tool integration. All external HTTP is mocked --
never touches a live Open-Meteo server.

Run with:
    .venv/Scripts/python.exe -m tests.test_weather_tool
"""
import asyncio
from unittest.mock import patch, MagicMock

from actions.weather import get_weather_text
from core.headless_surface import HeadlessSurface
from main import JarvisLive

_OPEN_METEO_RESPONSE = {
    "current_units": {
        "temperature_2m": "°C", "apparent_temperature": "°C",
        "precipitation": "mm", "wind_speed_10m": "km/h",
    },
    "current": {
        "temperature_2m": 5.2, "apparent_temperature": 2.1,
        "precipitation": 0.0, "weather_code": 2, "wind_speed_10m": 14.3,
    },
    "daily_units": {
        "temperature_2m_max": "°C", "temperature_2m_min": "°C",
        "precipitation_probability_max": "%", "precipitation_sum": "mm",
    },
    "daily": {
        "time": ["2026-08-27", "2026-08-28", "2026-08-29"],
        "weather_code": [2, 61, 0],
        "temperature_2m_max": [8.0, 6.0, 9.0],
        "temperature_2m_min": [1.0, 2.0, 0.0],
        "precipitation_probability_max": [10, 70, 5],
        "precipitation_sum": [0.0, 4.2, 0.0],
    },
}


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


# ── actions/weather.py directly ───────────────────────────────────────────

def test_get_weather_text_formats_current_and_forecast() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = _OPEN_METEO_RESPONSE
    mock_resp.raise_for_status.return_value = None
    with patch("actions.weather.requests.get", return_value=mock_resp) as mock_get:
        text = get_weather_text(60.17, 24.94, place_label="Helsinki")
    assert "Helsinki" in text
    assert "5.2" in text            # current temperature
    assert "partly cloudy" in text  # WMO code 2
    assert "Tomorrow" in text
    assert "70%" in text            # tomorrow's rain chance
    mock_get.assert_called_once()
    called_kwargs = mock_get.call_args.kwargs
    assert called_kwargs["params"]["latitude"] == 60.17
    assert called_kwargs["params"]["longitude"] == 24.94
    print("test_get_weather_text_formats_current_and_forecast: PASS")


def test_get_weather_text_without_place_label_omits_header() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = _OPEN_METEO_RESPONSE
    mock_resp.raise_for_status.return_value = None
    with patch("actions.weather.requests.get", return_value=mock_resp):
        text = get_weather_text(60.17, 24.94)
    assert not text.startswith("Weather for")
    print("test_get_weather_text_without_place_label_omits_header: PASS")


def test_get_weather_text_propagates_http_failure() -> None:
    """No try/except inside the action itself -- matches every other
    action in this codebase; _execute_tool()'s existing generic handler
    is what turns this into an honest response."""
    import requests
    with patch("actions.weather.requests.get", side_effect=requests.ConnectionError("boom")):
        try:
            get_weather_text(60.17, 24.94)
            assert False, "must propagate the failure, never fabricate weather data"
        except requests.ConnectionError:
            pass
    print("test_get_weather_text_propagates_http_failure: PASS")


def test_unknown_weather_code_degrades_gracefully() -> None:
    resp = dict(_OPEN_METEO_RESPONSE)
    resp["current"] = dict(resp["current"], weather_code=9999)
    mock_resp = MagicMock()
    mock_resp.json.return_value = resp
    mock_resp.raise_for_status.return_value = None
    with patch("actions.weather.requests.get", return_value=mock_resp):
        text = get_weather_text(60.17, 24.94)
    assert "unknown conditions" in text
    print("test_unknown_weather_code_degrades_gracefully: PASS")


# ── main.py's get_weather tool integration ────────────────────────────────

def test_get_weather_tool_uses_current_session_location() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.17, 24.94, 50.0)
        with patch("main.get_weather_text", return_value="sunny and mild") as mock_weather:
            fc = _FakeFunctionCall("get_weather", {})
            resp = await jarvis._execute_tool(fc)
        assert resp.response["result"] == "sunny and mild"
        mock_weather.assert_called_once_with(60.17, 24.94)
    asyncio.run(_run())
    print("test_get_weather_tool_uses_current_session_location: PASS")


def test_get_weather_tool_with_named_place_geocodes_first() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        with patch("main.geocode_place", return_value=(60.17, 24.94, "Helsinki, Finland")) as mock_geo, \
             patch("main.get_weather_text", return_value="weather text") as mock_weather:
            fc = _FakeFunctionCall("get_weather", {"place": "Helsinki"})
            resp = await jarvis._execute_tool(fc)
        mock_geo.assert_called_once_with("Helsinki")
        mock_weather.assert_called_once_with(60.17, 24.94, "Helsinki, Finland")
        assert resp.response["result"] == "weather text"
    asyncio.run(_run())
    print("test_get_weather_tool_with_named_place_geocodes_first: PASS")


def test_get_weather_tool_unknown_place_is_honest() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        with patch("main.geocode_place", return_value=None):
            fc = _FakeFunctionCall("get_weather", {"place": "Nowhereville"})
            resp = await jarvis._execute_tool(fc)
        assert "couldn't find" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_get_weather_tool_unknown_place_is_honest: PASS")


def test_get_weather_tool_without_location_reports_unavailable() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        assert jarvis._session_location is None
        fc = _FakeFunctionCall("get_weather", {})
        resp = await jarvis._execute_tool(fc)
        assert "[LOCATION_UNAVAILABLE]" in resp.response["result"]
    asyncio.run(_run())
    print("test_get_weather_tool_without_location_reports_unavailable: PASS")


def test_get_weather_tool_desktop_without_location_is_honest_not_an_error() -> None:
    """Desktop has no browser location source -- must degrade the same
    honest way a web session with denied permission does, not crash or
    treat this as a desktop-only-tool restriction."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())   # auto_start=True, desktop's default
        fc = _FakeFunctionCall("get_weather", {})
        resp = await jarvis._execute_tool(fc)
        assert "[LOCATION_UNAVAILABLE]" in resp.response["result"]
    asyncio.run(_run())
    print("test_get_weather_tool_desktop_without_location_is_honest_not_an_error: PASS")


def test_get_weather_tool_propagates_provider_failure_honestly() -> None:
    """A genuine Open-Meteo failure must surface as an honest tool
    failure (main.py's existing generic exception handling), never a
    fabricated forecast."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.17, 24.94, 50.0)
        with patch("main.get_weather_text", side_effect=RuntimeError("Open-Meteo unreachable")):
            fc = _FakeFunctionCall("get_weather", {})
            resp = await jarvis._execute_tool(fc)
        assert "failed" in resp.response["result"].lower()
        assert "Open-Meteo unreachable" in resp.response["result"]
    asyncio.run(_run())
    print("test_get_weather_tool_propagates_provider_failure_honestly: PASS")


def test_weather_report_tool_no_longer_exists() -> None:
    """The old fake/browser-opening implementation is fully retired."""
    from main import TOOL_DECLARATIONS
    names = {t["name"] for t in TOOL_DECLARATIONS}
    assert "weather_report" not in names
    assert "get_weather" in names
    print("test_weather_report_tool_no_longer_exists: PASS")


if __name__ == "__main__":
    test_get_weather_text_formats_current_and_forecast()
    test_get_weather_text_without_place_label_omits_header()
    test_get_weather_text_propagates_http_failure()
    test_unknown_weather_code_degrades_gracefully()
    test_get_weather_tool_uses_current_session_location()
    test_get_weather_tool_with_named_place_geocodes_first()
    test_get_weather_tool_unknown_place_is_honest()
    test_get_weather_tool_without_location_reports_unavailable()
    test_get_weather_tool_desktop_without_location_is_honest_not_an_error()
    test_get_weather_tool_propagates_provider_failure_honestly()
    test_weather_report_tool_no_longer_exists()
    print("\nAll weather-tool tests passed.")
