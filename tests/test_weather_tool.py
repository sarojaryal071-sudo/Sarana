"""
tests/test_weather_tool.py -- actions/weather.py (Open-Meteo) and
main.py's get_weather tool integration. All external HTTP is mocked --
never touches a live Open-Meteo server.

Run with:
    .venv/Scripts/python.exe -m tests.test_weather_tool
"""
import asyncio
from unittest.mock import patch, MagicMock

from actions.weather import get_weather_text, get_weather_data, format_weather_text
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


def _fake_weather_data(location: str = "") -> dict:
    """A structured get_weather_data()-shaped dict for tests that patch
    main.get_weather_data() directly — main.py's own get_weather dispatch
    formats its text reply from exactly this shape (format_weather_text())
    and broadcasts the same dict as the weather presentation payload
    (Track 3), so patching at this level exercises both paths honestly."""
    return {
        "location": location,
        "current": {
            "temperature": 5.2, "unit": "°C", "feels_like": 2.1, "feels_like_unit": "°C",
            "condition": "partly cloudy", "wind": 14.3, "wind_unit": "km/h",
            "precipitation": 0.0, "precipitation_unit": "mm",
        },
        "daily": [
            {"label": "Today", "date": "2026-08-27", "condition": "partly cloudy",
             "high": 8.0, "low": 1.0, "temp_unit": "°C",
             "precip_probability": 10, "precip_probability_unit": "%",
             "precip_total": 0.0, "precip_total_unit": "mm"},
        ],
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
        fake_data = _fake_weather_data()
        with patch("main.get_weather_data", return_value=fake_data) as mock_weather:
            fc = _FakeFunctionCall("get_weather", {})
            resp = await jarvis._execute_tool(fc)
        assert resp.response["result"] == format_weather_text(fake_data)
        mock_weather.assert_called_once_with(60.17, 24.94, "")
    asyncio.run(_run())
    print("test_get_weather_tool_uses_current_session_location: PASS")


def test_get_weather_tool_with_named_place_geocodes_first() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        fake_data = _fake_weather_data("Helsinki, Finland")
        with patch("main.geocode_place", return_value=(60.17, 24.94, "Helsinki, Finland")) as mock_geo, \
             patch("main.get_weather_data", return_value=fake_data) as mock_weather:
            fc = _FakeFunctionCall("get_weather", {"place": "Helsinki"})
            resp = await jarvis._execute_tool(fc)
        mock_geo.assert_called_once_with("Helsinki")
        mock_weather.assert_called_once_with(60.17, 24.94, "Helsinki, Finland")
        assert resp.response["result"] == format_weather_text(fake_data)
    asyncio.run(_run())
    print("test_get_weather_tool_with_named_place_geocodes_first: PASS")


def test_get_weather_tool_broadcasts_the_weather_presentation_to_the_dashboard() -> None:
    """Track 3: the SAME structured data used for the spoken/text reply
    is also broadcast to the web frontend as a weather presentation
    payload — real gap check, not merely that text formatting works."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.17, 24.94, 50.0)
        fake_data = _fake_weather_data()
        fake_dashboard = MagicMock()
        fake_dashboard.broadcast_content = MagicMock(return_value=asyncio.sleep(0))
        jarvis._dashboard = fake_dashboard
        with patch("main.get_weather_data", return_value=fake_data):
            fc = _FakeFunctionCall("get_weather", {})
            await jarvis._execute_tool(fc)
        await asyncio.sleep(0)  # let the create_task()'d broadcast actually run
        fake_dashboard.broadcast_content.assert_called_once()
        call_args = fake_dashboard.broadcast_content.call_args.args
        presentation = call_args[2]
        assert presentation["type"] == "weather"
        assert presentation["data"] == fake_data
    asyncio.run(_run())
    print("test_get_weather_tool_broadcasts_the_weather_presentation_to_the_dashboard: PASS")


def test_get_weather_tool_calls_ui_show_content_even_without_a_dashboard() -> None:
    """Real, disclosed bug fix: get_weather used to call ONLY
    self._dashboard.broadcast_content() — the one path a desktop-side
    Presentation Engine consumer can ever receive a presentation through
    (self.ui.show_content()) was never called at all, unlike web_search's
    identical-shape branch. Must fire unconditionally, dashboard or not."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.17, 24.94, 50.0)
        assert jarvis._dashboard is None
        fake_data = _fake_weather_data()
        with patch("main.get_weather_data", return_value=fake_data), \
             patch.object(jarvis.ui, "show_content") as mock_show:
            fc = _FakeFunctionCall("get_weather", {})
            await jarvis._execute_tool(fc)
        mock_show.assert_called_once()
        title, text, presentation = mock_show.call_args.args
        assert presentation["type"] == "weather"
        assert presentation["data"] == fake_data
    asyncio.run(_run())
    print("test_get_weather_tool_calls_ui_show_content_even_without_a_dashboard: PASS")


# ── 7-day forecast (Universal Information Surface's own "expand" needs
# real data to reveal beyond day 3 — see actions/weather.py's own
# updated docstring) ──────────────────────────────────────────────────

_OPEN_METEO_7DAY_RESPONSE = {
    "current_units": _OPEN_METEO_RESPONSE["current_units"],
    "current": _OPEN_METEO_RESPONSE["current"],
    "daily_units": _OPEN_METEO_RESPONSE["daily_units"],
    "daily": {
        "time": ["2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31", "2026-09-01", "2026-09-02"],
        "weather_code": [2, 61, 0, 1, 3, 2, 0],
        "temperature_2m_max": [8.0, 6.0, 9.0, 7.0, 5.0, 6.5, 10.0],
        "temperature_2m_min": [1.0, 2.0, 0.0, -1.0, 0.5, 1.5, 3.0],
        "precipitation_probability_max": [10, 70, 5, 20, 40, 15, 0],
        "precipitation_sum": [0.0, 4.2, 0.0, 1.0, 2.5, 0.5, 0.0],
    },
}


def test_get_weather_data_requests_a_7_day_forecast() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = _OPEN_METEO_7DAY_RESPONSE
    mock_resp.raise_for_status.return_value = None
    with patch("actions.weather.requests.get", return_value=mock_resp) as mock_get:
        data = get_weather_data(60.17, 24.94)
    assert mock_get.call_args.kwargs["params"]["forecast_days"] == 7
    assert len(data["daily"]) == 7
    print("test_get_weather_data_requests_a_7_day_forecast: PASS")


def test_get_weather_data_days_beyond_2_use_a_real_weekday_name() -> None:
    """Day 4+ (index >= 3, past "Today"/"Tomorrow"/"Day after tomorrow")
    must be a real weekday derived from the actual date Open-Meteo
    returned, never the raw ISO string and never guessed."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = _OPEN_METEO_7DAY_RESPONSE
    mock_resp.raise_for_status.return_value = None
    with patch("actions.weather.requests.get", return_value=mock_resp):
        data = get_weather_data(60.17, 24.94)
    assert data["daily"][0]["label"] == "Today"
    assert data["daily"][1]["label"] == "Tomorrow"
    assert data["daily"][2]["label"] == "Day after tomorrow"
    # 2026-08-30 is a Sunday
    assert data["daily"][3]["label"] == "Sunday"
    assert data["daily"][3]["date"] == "2026-08-30"
    print("test_get_weather_data_days_beyond_2_use_a_real_weekday_name: PASS")


def test_format_weather_text_stays_compact_reading_only_the_first_3_days() -> None:
    """The structured data carries a full week (for the visual surface's
    own expand feature), but Gemini's SPOKEN reply must stay compact —
    nobody wants "what's the weather" answered with a 7-day recitation."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = _OPEN_METEO_7DAY_RESPONSE
    mock_resp.raise_for_status.return_value = None
    with patch("actions.weather.requests.get", return_value=mock_resp):
        data = get_weather_data(60.17, 24.94)
    text = format_weather_text(data)
    assert "Today" in text
    assert "Tomorrow" in text
    assert "Day after tomorrow" in text
    assert "Sunday" not in text   # day 4 — present in the data, not in the spoken text
    print("test_format_weather_text_stays_compact_reading_only_the_first_3_days: PASS")


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
        with patch("main.get_weather_data", side_effect=RuntimeError("Open-Meteo unreachable")):
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
    test_get_weather_tool_broadcasts_the_weather_presentation_to_the_dashboard()
    test_get_weather_tool_calls_ui_show_content_even_without_a_dashboard()
    test_get_weather_data_requests_a_7_day_forecast()
    test_get_weather_data_days_beyond_2_use_a_real_weekday_name()
    test_format_weather_text_stays_compact_reading_only_the_first_3_days()
    test_get_weather_tool_unknown_place_is_honest()
    test_get_weather_tool_without_location_reports_unavailable()
    test_get_weather_tool_desktop_without_location_is_honest_not_an_error()
    test_get_weather_tool_propagates_provider_failure_honestly()
    test_weather_report_tool_no_longer_exists()
    print("\nAll weather-tool tests passed.")
