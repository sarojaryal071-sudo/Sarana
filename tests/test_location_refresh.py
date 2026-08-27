"""
tests/test_location_refresh.py -- JarvisLive._get_current_location()'s
freshness/refresh mechanism (backend -> browser "location_refresh_request"
-> POST /api/location -> waiter woken), the refresh_location tool, and the
race conditions the location-capabilities task explicitly calls out:
identity switch during refresh, logout during refresh, and multiple
overlapping refreshes.

Run with:
    .venv/Scripts/python.exe -m tests.test_location_refresh
"""
import asyncio
import time
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive, LOCATION_MAX_AGE_S


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


class _FakeDashboardForRefresh:
    """Just enough surface for _get_current_location() to broadcast a
    refresh request without a real dashboard/WebSocket."""
    def __init__(self):
        self.broadcast_calls = 0

    async def broadcast_location_refresh_request(self):
        self.broadcast_calls += 1


def _stale_location(age_s: float) -> dict:
    return {
        "latitude": 1.0, "longitude": 2.0, "accuracy": 10.0,
        "timestamp": time.monotonic() - age_s, "fix_timestamp": None,
    }


# ── freshness / refresh mechanism ─────────────────────────────────────────

def test_fresh_location_returned_without_any_network_activity() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = _FakeDashboardForRefresh()
        jarvis._set_session_location(60.17, 24.94, 50.0)
        loc = await jarvis._get_current_location()
        assert loc is not None
        assert jarvis._dashboard.broadcast_calls == 0
    asyncio.run(_run())
    print("test_fresh_location_returned_without_any_network_activity: PASS")


def test_stale_location_triggers_refresh_and_a_timely_response_is_used() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = _FakeDashboardForRefresh()
        jarvis._session_location = _stale_location(LOCATION_MAX_AGE_S + 1)

        async def _respond_soon():
            await asyncio.sleep(0.05)
            jarvis._set_session_location(9.0, 9.0, 5.0)

        asyncio.create_task(_respond_soon())
        loc = await jarvis._get_current_location()
        assert loc["latitude"] == 9.0
        assert jarvis._dashboard.broadcast_calls == 1
    asyncio.run(_run())
    print("test_stale_location_triggers_refresh_and_a_timely_response_is_used: PASS")


def test_refresh_timeout_falls_back_to_stale_value_when_not_requiring_fresh() -> None:
    async def _run():
        with patch("main.LOCATION_REFRESH_TIMEOUT_S", 0.05):
            jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
            jarvis._dashboard = _FakeDashboardForRefresh()
            jarvis._session_location = _stale_location(LOCATION_MAX_AGE_S + 1)
            loc = await jarvis._get_current_location()
        assert loc is not None
        assert loc["latitude"] == 1.0   # the old (stale) value -- better than nothing
    asyncio.run(_run())
    print("test_refresh_timeout_falls_back_to_stale_value_when_not_requiring_fresh: PASS")


def test_require_fresh_does_not_fall_back_to_stale_on_timeout() -> None:
    """The exact honesty requirement: 'where am I right now' must not
    silently answer with an old fix if refreshing it failed."""
    async def _run():
        with patch("main.LOCATION_REFRESH_TIMEOUT_S", 0.05):
            jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
            jarvis._dashboard = _FakeDashboardForRefresh()
            jarvis._session_location = _stale_location(100)   # older than LOCATION_FRESH_ENOUGH_S
            loc = await jarvis._get_current_location(require_fresh=True)
        assert loc is None
    asyncio.run(_run())
    print("test_require_fresh_does_not_fall_back_to_stale_on_timeout: PASS")


def test_require_fresh_accepts_an_already_recent_fix_without_a_refresh() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = _FakeDashboardForRefresh()
        jarvis._set_session_location(1.0, 2.0, 10.0)   # just set -- very fresh
        loc = await jarvis._get_current_location(require_fresh=True)
        assert loc is not None
        assert jarvis._dashboard.broadcast_calls == 0
    asyncio.run(_run())
    print("test_require_fresh_accepts_an_already_recent_fix_without_a_refresh: PASS")


def test_without_a_dashboard_falls_back_to_existing_or_none() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        assert jarvis._dashboard is None
        jarvis._session_location = _stale_location(LOCATION_MAX_AGE_S + 1)
        loc = await jarvis._get_current_location()
        assert loc is not None   # no dashboard to ask -- stale is still better than nothing
        loc2 = await jarvis._get_current_location(require_fresh=True)
        assert loc2 is None      # but a genuinely fresh check can't be satisfied
    asyncio.run(_run())
    print("test_without_a_dashboard_falls_back_to_existing_or_none: PASS")


def test_no_location_and_no_dashboard_returns_none() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        loc = await jarvis._get_current_location()
        assert loc is None
    asyncio.run(_run())
    print("test_no_location_and_no_dashboard_returns_none: PASS")


def test_waiter_is_removed_after_use_no_leak() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = _FakeDashboardForRefresh()
        jarvis._session_location = _stale_location(LOCATION_MAX_AGE_S + 1)

        async def _respond_soon():
            await asyncio.sleep(0.02)
            jarvis._set_session_location(9.0, 9.0, 5.0)

        asyncio.create_task(_respond_soon())
        await jarvis._get_current_location()
        assert jarvis._location_refresh_waiters == []
    asyncio.run(_run())
    print("test_waiter_is_removed_after_use_no_leak: PASS")


# ── refresh_location tool ──────────────────────────────────────────────

def test_refresh_location_tool_success() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = _FakeDashboardForRefresh()
        jarvis._set_session_location(1.0, 2.0, 10.0)   # already fresh
        fc = _FakeFunctionCall("refresh_location", {})
        resp = await jarvis._execute_tool(fc)
        assert "[LOCATION_REFRESHED]" in resp.response["result"]
    asyncio.run(_run())
    print("test_refresh_location_tool_success: PASS")


def test_refresh_location_tool_honest_failure_when_nothing_arrives() -> None:
    async def _run():
        with patch("main.LOCATION_REFRESH_TIMEOUT_S", 0.05):
            jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
            jarvis._dashboard = _FakeDashboardForRefresh()
            fc = _FakeFunctionCall("refresh_location", {})
            resp = await jarvis._execute_tool(fc)
        assert "[LOCATION_UNAVAILABLE]" in resp.response["result"]
    asyncio.run(_run())
    print("test_refresh_location_tool_honest_failure_when_nothing_arrives: PASS")


# ── race conditions ────────────────────────────────────────────────────

def test_identity_switch_during_refresh_never_leaks_into_waiting_call() -> None:
    """Race #1: a location response belonging to user A must never
    become user B's location, even mid-refresh."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = _FakeDashboardForRefresh()
        jarvis._user_profile = {"username": "saroj"}

        async def _switch_then_stale_update():
            await asyncio.sleep(0.02)
            jarvis._user_profile = {"username": "sana"}   # identity switch mid-refresh
            jarvis._set_session_location(9.0, 9.0, 5.0, requester_owner="saroj")   # dropped

        asyncio.create_task(_switch_then_stale_update())
        with patch("main.LOCATION_REFRESH_TIMEOUT_S", 0.3):
            loc = await jarvis._get_current_location()
        assert loc is None
        assert jarvis._session_location is None
    asyncio.run(_run())
    print("test_identity_switch_during_refresh_never_leaks_into_waiting_call: PASS")


def test_logout_during_refresh_leaves_session_marked_logged_out() -> None:
    """Race #2: logout mid-refresh must leave the session correctly
    logged-out regardless of what an in-flight (already-started before
    logout) browser round trip eventually does -- and a NEXT login still
    starts completely clean either way (see test_location_context.py's
    new-login-clears-everything coverage)."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = _FakeDashboardForRefresh()
        jarvis._user_profile = {"username": "saroj"}

        async def _logout_mid_refresh():
            await asyncio.sleep(0.02)
            jarvis._clear_memory_session()

        asyncio.create_task(_logout_mid_refresh())
        with patch("main.LOCATION_REFRESH_TIMEOUT_S", 0.3):
            await jarvis._get_current_location()
        assert jarvis._logged_out is True
    asyncio.run(_run())
    print("test_logout_during_refresh_leaves_session_marked_logged_out: PASS")


def test_multiple_overlapping_refreshes_newest_fix_wins_regardless_of_arrival_order() -> None:
    """Race #3: an older fix arriving AFTER a newer one must not win --
    see test_location_context.py's dedicated fix_timestamp tests for the
    underlying _set_session_location() behavior; this proves it holds
    through the actual _get_current_location() refresh path too."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = _FakeDashboardForRefresh()
        jarvis._session_location = _stale_location(LOCATION_MAX_AGE_S + 1)

        async def _two_overlapping_responses():
            await asyncio.sleep(0.02)
            # The NEWER fix (higher fix_timestamp) arrives FIRST...
            jarvis._set_session_location(2.0, 2.0, 5.0, fix_timestamp=2000.0)
            # ...then an OLDER fix (lower fix_timestamp) arrives SECOND --
            # must not overwrite the newer one that already landed.
            jarvis._set_session_location(1.0, 1.0, 5.0, fix_timestamp=1000.0)

        asyncio.create_task(_two_overlapping_responses())
        loc = await jarvis._get_current_location()
        assert loc["latitude"] == 2.0
        # And the final stored state agrees, independent of the specific
        # in-flight call above.
        assert jarvis._session_location["latitude"] == 2.0
    asyncio.run(_run())
    print("test_multiple_overlapping_refreshes_newest_fix_wins_regardless_of_arrival_order: PASS")


if __name__ == "__main__":
    test_fresh_location_returned_without_any_network_activity()
    test_stale_location_triggers_refresh_and_a_timely_response_is_used()
    test_refresh_timeout_falls_back_to_stale_value_when_not_requiring_fresh()
    test_require_fresh_does_not_fall_back_to_stale_on_timeout()
    test_require_fresh_accepts_an_already_recent_fix_without_a_refresh()
    test_without_a_dashboard_falls_back_to_existing_or_none()
    test_no_location_and_no_dashboard_returns_none()
    test_waiter_is_removed_after_use_no_leak()
    test_refresh_location_tool_success()
    test_refresh_location_tool_honest_failure_when_nothing_arrives()
    test_identity_switch_during_refresh_never_leaks_into_waiting_call()
    test_logout_during_refresh_leaves_session_marked_logged_out()
    test_multiple_overlapping_refreshes_newest_fix_wins_regardless_of_arrival_order()
    print("\nAll location-refresh tests passed.")
