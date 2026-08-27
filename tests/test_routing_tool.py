"""
tests/test_routing_tool.py -- actions/routing.py (OSRM) and main.py's
get_directions tool integration, including the honest straight-line
fallback when OSRM can't compute a real route. All external HTTP is
mocked -- never touches a live OSRM server.

Run with:
    .venv/Scripts/python.exe -m tests.test_routing_tool
"""
import asyncio
from unittest.mock import patch, MagicMock

from actions.routing import get_route
from core.headless_surface import HeadlessSurface
from main import JarvisLive


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


def _mock_resp(payload):
    m = MagicMock()
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


# ── actions/routing.py directly ────────────────────────────────────────

def test_get_route_success() -> None:
    payload = {"code": "Ok", "routes": [{"distance": 12345.0, "duration": 900.0}]}
    with patch("actions.routing.requests.get", return_value=_mock_resp(payload)) as mock_get:
        route = get_route(60.17, 24.94, 60.20, 25.00, mode="driving")
    assert route == {"distance_m": 12345.0, "duration_s": 900.0, "mode": "driving"}
    url = mock_get.call_args.args[0]
    assert "/driving/" in url
    print("test_get_route_success: PASS")


def test_get_route_walking_mode() -> None:
    payload = {"code": "Ok", "routes": [{"distance": 800.0, "duration": 600.0}]}
    with patch("actions.routing.requests.get", return_value=_mock_resp(payload)) as mock_get:
        route = get_route(60.17, 24.94, 60.171, 24.941, mode="walking")
    assert route["mode"] == "walking"
    assert "/walking/" in mock_get.call_args.args[0]
    print("test_get_route_walking_mode: PASS")


def test_get_route_unrecognized_mode_defaults_to_driving() -> None:
    payload = {"code": "Ok", "routes": [{"distance": 1.0, "duration": 1.0}]}
    with patch("actions.routing.requests.get", return_value=_mock_resp(payload)):
        route = get_route(60.17, 24.94, 60.20, 25.00, mode="teleport")
    assert route["mode"] == "driving"
    print("test_get_route_unrecognized_mode_defaults_to_driving: PASS")


def test_get_route_raises_honestly_when_osrm_reports_failure() -> None:
    """The exact case the task calls out: if the public OSRM instance
    can't support a requested profile, this must raise -- never invent
    a distance/duration."""
    payload = {"code": "NoRoute", "routes": []}
    with patch("actions.routing.requests.get", return_value=_mock_resp(payload)):
        try:
            get_route(60.17, 24.94, 60.20, 25.00, mode="walking")
            assert False, "must raise when OSRM reports no route"
        except RuntimeError as e:
            assert "walking" in str(e)
    print("test_get_route_raises_honestly_when_osrm_reports_failure: PASS")


def test_get_route_propagates_http_failure() -> None:
    import requests
    with patch("actions.routing.requests.get", side_effect=requests.ConnectionError("boom")):
        try:
            get_route(60.17, 24.94, 60.20, 25.00)
            assert False
        except requests.ConnectionError:
            pass
    print("test_get_route_propagates_http_failure: PASS")


# ── main.py: get_directions tool integration ──────────────────────────

def test_get_directions_tool_success() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        with patch("main.geocode_place", return_value=(60.1710, 24.9414, "Helsinki Central Station")), \
             patch("main.get_route", return_value={"distance_m": 1200.0, "duration_s": 900.0, "mode": "walking"}):
            fc = _FakeFunctionCall("get_directions", {"destination": "Helsinki Central Station", "mode": "walking"})
            resp = await jarvis._execute_tool(fc)
        result = resp.response["result"]
        assert "Helsinki Central Station" in result
        assert "walking" in result
        assert "1.2 km" in result
        assert "15 minutes" in result
    asyncio.run(_run())
    print("test_get_directions_tool_success: PASS")


def test_get_directions_tool_requires_destination() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        fc = _FakeFunctionCall("get_directions", {})
        resp = await jarvis._execute_tool(fc)
        assert "specify" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_get_directions_tool_requires_destination: PASS")


def test_get_directions_tool_without_location_is_honest() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        fc = _FakeFunctionCall("get_directions", {"destination": "somewhere"})
        resp = await jarvis._execute_tool(fc)
        assert "[LOCATION_UNAVAILABLE]" in resp.response["result"]
    asyncio.run(_run())
    print("test_get_directions_tool_without_location_is_honest: PASS")


def test_get_directions_tool_unknown_destination_is_honest() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        with patch("main.geocode_place", return_value=None):
            fc = _FakeFunctionCall("get_directions", {"destination": "Nowhereville12345"})
            resp = await jarvis._execute_tool(fc)
        assert "couldn't find" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_get_directions_tool_unknown_destination_is_honest: PASS")


def test_get_directions_tool_falls_back_to_straight_line_when_routing_fails() -> None:
    """The exact honest-degradation behavior the task requires: OSRM
    failing for a requested mode must not become a generic tool failure
    or a fabricated ETA -- it should offer an honest straight-line
    distance instead."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        with patch("main.geocode_place", return_value=(60.1710, 24.9414, "Helsinki Central Station")), \
             patch("main.get_route", side_effect=RuntimeError("OSRM could not compute a 'walking' route.")):
            fc = _FakeFunctionCall("get_directions", {"destination": "Helsinki Central Station", "mode": "walking"})
            resp = await jarvis._execute_tool(fc)
        result = resp.response["result"]
        assert "[ROUTING_UNAVAILABLE]" in result
        assert "Helsinki Central Station" in result
        assert "km" in result or "m" in result   # an approximate straight-line distance is still given
    asyncio.run(_run())
    print("test_get_directions_tool_falls_back_to_straight_line_when_routing_fails: PASS")


def test_get_directions_tool_propagates_genuine_geocode_failure() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        with patch("main.geocode_place", side_effect=RuntimeError("Nominatim unreachable")):
            fc = _FakeFunctionCall("get_directions", {"destination": "Helsinki"})
            resp = await jarvis._execute_tool(fc)
        assert "failed" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_get_directions_tool_propagates_genuine_geocode_failure: PASS")


if __name__ == "__main__":
    test_get_route_success()
    test_get_route_walking_mode()
    test_get_route_unrecognized_mode_defaults_to_driving()
    test_get_route_raises_honestly_when_osrm_reports_failure()
    test_get_route_propagates_http_failure()
    test_get_directions_tool_success()
    test_get_directions_tool_requires_destination()
    test_get_directions_tool_without_location_is_honest()
    test_get_directions_tool_unknown_destination_is_honest()
    test_get_directions_tool_falls_back_to_straight_line_when_routing_fails()
    test_get_directions_tool_propagates_genuine_geocode_failure()
    print("\nAll routing-tool tests passed.")
