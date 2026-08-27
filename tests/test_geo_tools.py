"""
tests/test_geo_tools.py -- actions/geo.py (Nominatim geocoding/reverse
geocoding, Overpass nearby-places, haversine distance) and main.py's
get_current_place/find_nearby_places tool integration. All external HTTP
is mocked -- never touches a live Nominatim/Overpass server.

Run with:
    .venv/Scripts/python.exe -m tests.test_geo_tools
"""
import asyncio
import time
from unittest.mock import patch, MagicMock

from actions.geo import (
    geocode_place, reverse_geocode, format_place,
    find_nearby_places, format_nearby_places, haversine_m, format_distance,
    _match_tags, MAX_RADIUS_M, MIN_RADIUS_M,
)
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


# ── actions/geo.py: geocoding ──────────────────────────────────────────

def test_geocode_place_returns_first_match() -> None:
    payload = [{"lat": "60.1699", "lon": "24.9384", "display_name": "Helsinki, Finland"}]
    with patch("actions.geo.requests.get", return_value=_mock_resp(payload)), \
         patch("actions.geo._throttle_nominatim"):
        result = geocode_place("Helsinki")
    assert result == (60.1699, 24.9384, "Helsinki, Finland")
    print("test_geocode_place_returns_first_match: PASS")


def test_geocode_place_no_match_returns_none() -> None:
    with patch("actions.geo.requests.get", return_value=_mock_resp([])), \
         patch("actions.geo._throttle_nominatim"):
        assert geocode_place("Nowhereville12345") is None
    print("test_geocode_place_no_match_returns_none: PASS")


def test_geocode_place_empty_string_returns_none_without_network() -> None:
    with patch("actions.geo.requests.get") as mock_get:
        assert geocode_place("") is None
        assert geocode_place("   ") is None
        mock_get.assert_not_called()
    print("test_geocode_place_empty_string_returns_none_without_network: PASS")


def test_geocode_place_propagates_http_failure() -> None:
    import requests
    with patch("actions.geo.requests.get", side_effect=requests.ConnectionError("boom")), \
         patch("actions.geo._throttle_nominatim"):
        try:
            geocode_place("Helsinki")
            assert False
        except requests.ConnectionError:
            pass
    print("test_geocode_place_propagates_http_failure: PASS")


def test_geocode_place_sends_identifying_user_agent() -> None:
    payload = [{"lat": "1.0", "lon": "2.0", "display_name": "X"}]
    with patch("actions.geo.requests.get", return_value=_mock_resp(payload)) as mock_get, \
         patch("actions.geo._throttle_nominatim"):
        geocode_place("X")
    headers = mock_get.call_args.kwargs["headers"]
    assert "User-Agent" in headers
    assert headers["User-Agent"]
    print("test_geocode_place_sends_identifying_user_agent: PASS")


# ── actions/geo.py: reverse geocoding ─────────────────────────────────

def test_reverse_geocode_extracts_city_area_country() -> None:
    payload = {
        "display_name": "Kallio, Helsinki, Finland",
        "address": {"suburb": "Kallio", "city": "Helsinki", "country": "Finland"},
    }
    with patch("actions.geo.requests.get", return_value=_mock_resp(payload)), \
         patch("actions.geo._throttle_nominatim"):
        place = reverse_geocode(60.1837, 24.9502)
    assert place == {"city": "Helsinki", "area": "Kallio", "country": "Finland",
                      "label": "Kallio, Helsinki, Finland"}
    print("test_reverse_geocode_extracts_city_area_country: PASS")


def test_reverse_geocode_falls_back_to_town_or_village() -> None:
    payload = {"display_name": "X", "address": {"village": "Smallville", "country": "Finland"}}
    with patch("actions.geo.requests.get", return_value=_mock_resp(payload)), \
         patch("actions.geo._throttle_nominatim"):
        place = reverse_geocode(1.0, 2.0)
    assert place["city"] == "Smallville"
    print("test_reverse_geocode_falls_back_to_town_or_village: PASS")


def test_reverse_geocode_error_response_returns_none() -> None:
    with patch("actions.geo.requests.get", return_value=_mock_resp({"error": "Unable to geocode"})), \
         patch("actions.geo._throttle_nominatim"):
        assert reverse_geocode(0.0, 0.0) is None
    print("test_reverse_geocode_error_response_returns_none: PASS")


def test_format_place_joins_area_city_country() -> None:
    place = {"city": "Helsinki", "area": "Kallio", "country": "Finland", "label": "x"}
    assert format_place(place) == "Kallio, Helsinki, Finland"
    print("test_format_place_joins_area_city_country: PASS")


def test_format_place_handles_missing_fields() -> None:
    assert format_place(None) == "Current location could not be resolved to a place."
    assert format_place({}) == "Current location could not be resolved to a place."
    print("test_format_place_handles_missing_fields: PASS")


# ── distance math ──────────────────────────────────────────────────────

def test_haversine_zero_distance_for_same_point() -> None:
    assert haversine_m(60.0, 24.0, 60.0, 24.0) == 0.0
    print("test_haversine_zero_distance_for_same_point: PASS")


def test_haversine_known_distance_helsinki_to_tallinn() -> None:
    # Helsinki <-> Tallinn is roughly 80 km across the gulf.
    d = haversine_m(60.1699, 24.9384, 59.4370, 24.7536)
    assert 70_000 < d < 90_000
    print("test_haversine_known_distance_helsinki_to_tallinn: PASS")


def test_format_distance_meters_vs_km() -> None:
    assert format_distance(500) == "500 m"
    assert format_distance(1500) == "1.5 km"
    print("test_format_distance_meters_vs_km: PASS")


# ── actions/geo.py: nearby places (Overpass) ──────────────────────────

def test_match_tags_recognizes_common_categories() -> None:
    assert _match_tags("pharmacy") == [("amenity", "pharmacy")]
    assert _match_tags("a coffee shop please") != []
    assert _match_tags("some completely made up thing xyz") == []
    print("test_match_tags_recognizes_common_categories: PASS")


def _overpass_payload(elements):
    return {"elements": elements}


def test_find_nearby_places_sorts_by_distance() -> None:
    elements = [
        {"lat": 60.20, "lon": 24.94, "tags": {"name": "Far Pharmacy", "amenity": "pharmacy"}},
        {"lat": 60.1705, "lon": 24.9390, "tags": {"name": "Near Pharmacy", "amenity": "pharmacy"}},
    ]
    with patch("actions.geo.requests.post", return_value=_mock_resp(_overpass_payload(elements))):
        results = find_nearby_places("pharmacy", 60.1699, 24.9384)
    assert [r["name"] for r in results] == ["Near Pharmacy", "Far Pharmacy"]
    assert results[0]["distance_m"] < results[1]["distance_m"]
    print("test_find_nearby_places_sorts_by_distance: PASS")


def test_find_nearby_places_skips_unnamed_elements() -> None:
    elements = [
        {"lat": 60.17, "lon": 24.94, "tags": {"amenity": "pharmacy"}},   # no name -- skipped
        {"lat": 60.17, "lon": 24.94, "tags": {"name": "Real Pharmacy", "amenity": "pharmacy"}},
    ]
    with patch("actions.geo.requests.post", return_value=_mock_resp(_overpass_payload(elements))):
        results = find_nearby_places("pharmacy", 60.1699, 24.9384)
    assert len(results) == 1
    assert results[0]["name"] == "Real Pharmacy"
    print("test_find_nearby_places_skips_unnamed_elements: PASS")


def test_find_nearby_places_uses_center_for_ways() -> None:
    elements = [
        {"center": {"lat": 60.17, "lon": 24.94}, "tags": {"name": "A Way", "shop": "supermarket"}},
    ]
    with patch("actions.geo.requests.post", return_value=_mock_resp(_overpass_payload(elements))):
        results = find_nearby_places("supermarket", 60.1699, 24.9384)
    assert results[0]["name"] == "A Way"
    print("test_find_nearby_places_uses_center_for_ways: PASS")


def test_find_nearby_places_empty_results() -> None:
    with patch("actions.geo.requests.post", return_value=_mock_resp(_overpass_payload([]))):
        results = find_nearby_places("pharmacy", 60.1699, 24.9384)
    assert results == []
    print("test_find_nearby_places_empty_results: PASS")


def test_find_nearby_places_clamps_radius() -> None:
    with patch("actions.geo.requests.post", return_value=_mock_resp(_overpass_payload([]))) as mock_post:
        find_nearby_places("pharmacy", 60.0, 24.0, radius_m=999999)
        ql = mock_post.call_args.kwargs["data"]["data"]
        assert f"around:{MAX_RADIUS_M}," in ql

        find_nearby_places("pharmacy", 60.0, 24.0, radius_m=1)
        ql2 = mock_post.call_args.kwargs["data"]["data"]
        assert f"around:{MIN_RADIUS_M}," in ql2
    print("test_find_nearby_places_clamps_radius: PASS")


def test_find_nearby_places_propagates_http_failure() -> None:
    import requests
    with patch("actions.geo.requests.post", side_effect=requests.ConnectionError("boom")):
        try:
            find_nearby_places("pharmacy", 60.0, 24.0)
            assert False
        except requests.ConnectionError:
            pass
    print("test_find_nearby_places_propagates_http_failure: PASS")


def test_format_nearby_places_empty() -> None:
    assert "No pharmacy found" in format_nearby_places("pharmacy", [])
    print("test_format_nearby_places_empty: PASS")


def test_format_nearby_places_lists_results() -> None:
    results = [{"name": "X", "category": "pharmacy", "distance_m": 200, "address": ""}]
    text = format_nearby_places("pharmacy", results)
    assert "X" in text and "200 m" in text
    print("test_format_nearby_places_lists_results: PASS")


# ── main.py: get_current_place tool integration ───────────────────────

def test_get_current_place_tool_resolves_and_caches() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        place = {"city": "Helsinki", "area": "Kallio", "country": "Finland", "label": "x"}
        with patch("main.reverse_geocode", return_value=place) as mock_reverse:
            fc = _FakeFunctionCall("get_current_place", {})
            resp1 = await jarvis._execute_tool(fc)
            resp2 = await jarvis._execute_tool(fc)   # second call -- must hit the cache
        assert "Kallio" in resp1.response["result"]
        assert resp1.response["result"] == resp2.response["result"]
        mock_reverse.assert_called_once()   # only ONE real Nominatim call for both
    asyncio.run(_run())
    print("test_get_current_place_tool_resolves_and_caches: PASS")


def test_get_current_place_tool_without_location_is_honest() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        fc = _FakeFunctionCall("get_current_place", {})
        resp = await jarvis._execute_tool(fc)
        assert "[LOCATION_UNAVAILABLE]" in resp.response["result"]
    asyncio.run(_run())
    print("test_get_current_place_tool_without_location_is_honest: PASS")


def test_get_current_place_cache_expires_after_max_age() -> None:
    from main import LOCATION_MAX_AGE_S
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        jarvis._place_cache = {
            "city": "Old", "area": "", "country": "", "label": "Old",
            "for": (60.17, 24.94), "timestamp": time.monotonic() - LOCATION_MAX_AGE_S - 1,
        }
        with patch("main.reverse_geocode", return_value={"city": "New", "area": "", "country": "", "label": "New"}):
            fc = _FakeFunctionCall("get_current_place", {})
            resp = await jarvis._execute_tool(fc)
        assert "New" in resp.response["result"]
    asyncio.run(_run())
    print("test_get_current_place_cache_expires_after_max_age: PASS")


def test_get_current_place_cache_cleared_on_new_login() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._place_cache = {"city": "Helsinki", "for": (60.17, 24.94), "timestamp": time.monotonic()}
    with patch("main.set_active_owner"):
        from users import user_db
        jarvis._set_user_profile(user_db.authenticate("Saroj", "2057"))
    assert jarvis._place_cache is None
    print("test_get_current_place_cache_cleared_on_new_login: PASS")


def test_get_current_place_cache_cleared_on_logout() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._place_cache = {"city": "Helsinki", "for": (60.17, 24.94), "timestamp": time.monotonic()}
    with patch("main.clear_active_session"):
        jarvis._clear_memory_session()
    assert jarvis._place_cache is None
    print("test_get_current_place_cache_cleared_on_logout: PASS")


# ── main.py: find_nearby_places tool integration ──────────────────────

def test_find_nearby_places_tool_without_location_is_honest() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        fc = _FakeFunctionCall("find_nearby_places", {"query": "pharmacy"})
        resp = await jarvis._execute_tool(fc)
        assert "[LOCATION_UNAVAILABLE]" in resp.response["result"]
    asyncio.run(_run())
    print("test_find_nearby_places_tool_without_location_is_honest: PASS")


def test_find_nearby_places_tool_requires_a_query() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        fc = _FakeFunctionCall("find_nearby_places", {})
        resp = await jarvis._execute_tool(fc)
        assert "specify" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_find_nearby_places_tool_requires_a_query: PASS")


def test_find_nearby_places_tool_uses_cache_for_repeat_query() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        with patch("main.find_nearby_places", return_value=[{"name": "X", "category": "pharmacy",
                                                               "distance_m": 100, "address": ""}]) as mock_find:
            fc = _FakeFunctionCall("find_nearby_places", {"query": "pharmacy"})
            await jarvis._execute_tool(fc)
            await jarvis._execute_tool(fc)
        mock_find.assert_called_once()
    asyncio.run(_run())
    print("test_find_nearby_places_tool_uses_cache_for_repeat_query: PASS")


def test_find_nearby_places_tool_propagates_provider_failure() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_location(60.1699, 24.9384, 50.0)
        with patch("main.find_nearby_places", side_effect=RuntimeError("Overpass down")):
            fc = _FakeFunctionCall("find_nearby_places", {"query": "pharmacy"})
            resp = await jarvis._execute_tool(fc)
        assert "failed" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_find_nearby_places_tool_propagates_provider_failure: PASS")


if __name__ == "__main__":
    test_geocode_place_returns_first_match()
    test_geocode_place_no_match_returns_none()
    test_geocode_place_empty_string_returns_none_without_network()
    test_geocode_place_propagates_http_failure()
    test_geocode_place_sends_identifying_user_agent()
    test_reverse_geocode_extracts_city_area_country()
    test_reverse_geocode_falls_back_to_town_or_village()
    test_reverse_geocode_error_response_returns_none()
    test_format_place_joins_area_city_country()
    test_format_place_handles_missing_fields()
    test_haversine_zero_distance_for_same_point()
    test_haversine_known_distance_helsinki_to_tallinn()
    test_format_distance_meters_vs_km()
    test_match_tags_recognizes_common_categories()
    test_find_nearby_places_sorts_by_distance()
    test_find_nearby_places_skips_unnamed_elements()
    test_find_nearby_places_uses_center_for_ways()
    test_find_nearby_places_empty_results()
    test_find_nearby_places_clamps_radius()
    test_find_nearby_places_propagates_http_failure()
    test_format_nearby_places_empty()
    test_format_nearby_places_lists_results()
    test_get_current_place_tool_resolves_and_caches()
    test_get_current_place_tool_without_location_is_honest()
    test_get_current_place_cache_expires_after_max_age()
    test_get_current_place_cache_cleared_on_new_login()
    test_get_current_place_cache_cleared_on_logout()
    test_find_nearby_places_tool_without_location_is_honest()
    test_find_nearby_places_tool_requires_a_query()
    test_find_nearby_places_tool_uses_cache_for_repeat_query()
    test_find_nearby_places_tool_propagates_provider_failure()
    print("\nAll geo-tools tests passed.")
