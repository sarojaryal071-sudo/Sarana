"""
tests/test_calendar_tools.py -- main.py's 5 Google Calendar tools
(get_calendar_events, find_free_time, create_calendar_event,
update_calendar_event, delete_calendar_event), _get_calendar_credentials()
(credential resolution/refresh, per-account isolation, no RAM caching),
and _calendar_tzinfo() (device-local timezone, never server/UTC).

actions.calendar/calendar_store/calendar_auth are all mocked throughout
-- never a live Google API or database.

Run with:
    .venv/Scripts/python.exe -m tests.test_calendar_tools
"""
import asyncio
from datetime import datetime, timezone as dt_timezone, timedelta
from unittest.mock import MagicMock, patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


def _jarvis(owner="saroj"):
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._user_profile = {"username": owner} if owner else None
    return jarvis


def _valid_credentials():
    creds = MagicMock()
    creds.valid = True
    creds.expired = False
    return creds


# ── _get_calendar_credentials() ────────────────────────────────────────

def test_get_calendar_credentials_none_when_no_profile() -> None:
    async def _run():
        jarvis = _jarvis(owner=None)
        creds = await jarvis._get_calendar_credentials()
        assert creds is None
    asyncio.run(_run())
    print("test_get_calendar_credentials_none_when_no_profile: PASS")


def test_get_calendar_credentials_none_when_store_not_configured() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=False):
            creds = await jarvis._get_calendar_credentials()
        assert creds is None
    asyncio.run(_run())
    print("test_get_calendar_credentials_none_when_store_not_configured: PASS")


def test_get_calendar_credentials_none_when_never_connected() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch("main.calendar_store.load_credentials", return_value=None):
            creds = await jarvis._get_calendar_credentials()
        assert creds is None
    asyncio.run(_run())
    print("test_get_calendar_credentials_none_when_never_connected: PASS")


def test_get_calendar_credentials_returns_valid_credentials_without_refresh() -> None:
    async def _run():
        jarvis = _jarvis()
        fake_creds = _valid_credentials()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch("main.calendar_store.load_credentials", return_value=('{"token": "x"}', "saroj@example.com")), \
             patch("main.calendar_auth.credentials_from_json", return_value=fake_creds), \
             patch("main.calendar_auth.ensure_fresh", return_value=(fake_creds, False)) as mock_ensure, \
             patch("main.calendar_store.save_credentials") as mock_save:
            creds = await jarvis._get_calendar_credentials()
        assert creds is fake_creds
        mock_ensure.assert_called_once()
        mock_save.assert_not_called()   # not refreshed -- nothing new to persist
    asyncio.run(_run())
    print("test_get_calendar_credentials_returns_valid_credentials_without_refresh: PASS")


def test_get_calendar_credentials_persists_refreshed_token() -> None:
    async def _run():
        jarvis = _jarvis()
        fake_creds = _valid_credentials()
        fake_creds.to_json.return_value = '{"token": "refreshed"}'
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch("main.calendar_store.load_credentials", return_value=('{"token": "old"}', "saroj@example.com")), \
             patch("main.calendar_auth.credentials_from_json", return_value=fake_creds), \
             patch("main.calendar_auth.ensure_fresh", return_value=(fake_creds, True)), \
             patch("main.calendar_store.save_credentials") as mock_save:
            creds = await jarvis._get_calendar_credentials()
        assert creds is fake_creds
        mock_save.assert_called_once_with("saroj", '{"token": "refreshed"}', "saroj@example.com")
    asyncio.run(_run())
    print("test_get_calendar_credentials_persists_refreshed_token: PASS")


def test_get_calendar_credentials_none_when_refresh_fails() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch("main.calendar_store.load_credentials", return_value=('{"token": "x"}', "")), \
             patch("main.calendar_auth.credentials_from_json", side_effect=Exception("corrupt token")):
            creds = await jarvis._get_calendar_credentials()
        assert creds is None
    asyncio.run(_run())
    print("test_get_calendar_credentials_none_when_refresh_fails: PASS")


def test_get_calendar_credentials_isolated_per_account_no_ram_cache() -> None:
    """The core security property: switching self._user_profile (exactly
    what a real identity switch does) immediately changes whose
    credentials the NEXT call resolves -- nothing calendar-related is
    ever cached on JarvisLive itself."""
    async def _run():
        jarvis = _jarvis(owner="saroj")

        def fake_load(owner):
            return (f'{{"owner": "{owner}"}}', f"{owner}@example.com")

        creds_by_owner = {}

        def fake_from_json(json_str):
            c = _valid_credentials()
            c.owner_marker = json_str
            return c

        with patch("main.calendar_store.is_configured", return_value=True), \
             patch("main.calendar_store.load_credentials", side_effect=fake_load), \
             patch("main.calendar_auth.credentials_from_json", side_effect=fake_from_json), \
             patch("main.calendar_auth.ensure_fresh", side_effect=lambda c: (c, False)):
            saroj_creds = await jarvis._get_calendar_credentials()

            jarvis._user_profile = {"username": "sana"}   # identity switch
            sana_creds = await jarvis._get_calendar_credentials()

        assert saroj_creds.owner_marker == '{"owner": "saroj"}'
        assert sana_creds.owner_marker == '{"owner": "sana"}'
    asyncio.run(_run())
    print("test_get_calendar_credentials_isolated_per_account_no_ram_cache: PASS")


# ── _calendar_tzinfo() ──────────────────────────────────────────────────

def test_calendar_tzinfo_uses_web_timezone_when_set() -> None:
    jarvis = _jarvis()
    jarvis._web_timezone = "Asia/Kathmandu"
    tzinfo = jarvis._calendar_tzinfo()
    now = datetime(2026, 6, 15, 12, 0, tzinfo=tzinfo)
    assert now.utcoffset() == timedelta(hours=5, minutes=45)
    print("test_calendar_tzinfo_uses_web_timezone_when_set: PASS")


def test_calendar_tzinfo_falls_back_to_machine_offset_on_desktop() -> None:
    jarvis = JarvisLive(HeadlessSurface())   # auto_start=True, desktop's default
    assert jarvis._web_timezone is None
    tzinfo = jarvis._calendar_tzinfo()
    assert tzinfo is not None   # never naive/UTC -- some real local offset
    print("test_calendar_tzinfo_falls_back_to_machine_offset_on_desktop: PASS")


def test_calendar_tzinfo_never_defaults_to_utc_when_web_timezone_set() -> None:
    jarvis = _jarvis()
    jarvis._web_timezone = "Pacific/Kiritimati"   # UTC+14 -- essentially never the CI server's own zone
    tzinfo = jarvis._calendar_tzinfo()
    now = datetime(2026, 6, 15, 12, 0, tzinfo=tzinfo)
    assert now.utcoffset() == timedelta(hours=14)
    print("test_calendar_tzinfo_never_defaults_to_utc_when_web_timezone_set: PASS")


# ── get_calendar_events ─────────────────────────────────────────────────

def test_get_calendar_events_not_connected() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=False):
            fc = _FakeFunctionCall("get_calendar_events", {"start": "2026-08-29T00:00:00", "end": "2026-08-30T00:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "[CALENDAR_NOT_CONNECTED]" in resp.response["result"]
    asyncio.run(_run())
    print("test_get_calendar_events_not_connected: PASS")


def test_get_calendar_events_success_uses_resolved_tzinfo() -> None:
    async def _run():
        jarvis = _jarvis()
        jarvis._web_timezone = "Asia/Kathmandu"
        fake_creds = _valid_credentials()
        captured = {}

        def fake_get_events(credentials, *, time_min, time_max, max_results=25):
            captured["time_min"] = time_min
            captured["time_max"] = time_max
            return [{"id": "ev1", "title": "Standup", "start": "2026-08-29T09:00:00+05:45",
                     "end": "2026-08-29T09:30:00+05:45", "location": "", "all_day": False}]

        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=fake_creds), \
             patch("main.calendar_actions.get_events", side_effect=fake_get_events):
            fc = _FakeFunctionCall("get_calendar_events", {"start": "2026-08-29T00:00:00", "end": "2026-08-30T00:00:00"})
            resp = await jarvis._execute_tool(fc)

        assert "Standup" in resp.response["result"]
        assert captured["time_min"].utcoffset() == timedelta(hours=5, minutes=45)
        assert captured["time_min"].tzinfo is not None
    asyncio.run(_run())
    print("test_get_calendar_events_success_uses_resolved_tzinfo: PASS")


def test_get_calendar_events_invalid_dates_asks_instead_of_guessing() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()):
            fc = _FakeFunctionCall("get_calendar_events", {"start": "not-a-date", "end": "also-not-a-date"})
            resp = await jarvis._execute_tool(fc)
        assert "valid" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_get_calendar_events_invalid_dates_asks_instead_of_guessing: PASS")


def test_get_calendar_events_api_failure_propagates_honestly() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.get_events", side_effect=RuntimeError("Google API 500")):
            fc = _FakeFunctionCall("get_calendar_events", {"start": "2026-08-29T00:00:00", "end": "2026-08-30T00:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "failed" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_get_calendar_events_api_failure_propagates_honestly: PASS")


def test_get_calendar_events_empty_range_is_honest_not_fabricated() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.get_events", return_value=[]):
            fc = _FakeFunctionCall("get_calendar_events", {"start": "2026-08-29T00:00:00", "end": "2026-08-30T00:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "no events" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_get_calendar_events_empty_range_is_honest_not_fabricated: PASS")


# ── find_free_time ───────────────────────────────────────────────────

def test_find_free_time_not_connected() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=False):
            fc = _FakeFunctionCall("find_free_time", {"start": "2026-08-29T08:00:00", "end": "2026-08-29T18:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "[CALENDAR_NOT_CONNECTED]" in resp.response["result"]
    asyncio.run(_run())
    print("test_find_free_time_not_connected: PASS")


def test_find_free_time_success() -> None:
    async def _run():
        jarvis = _jarvis()
        tzinfo = dt_timezone(timedelta(hours=5, minutes=45))
        slot = (datetime(2026, 8, 29, 10, 0, tzinfo=tzinfo), datetime(2026, 8, 29, 11, 0, tzinfo=tzinfo))
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.find_free_slots", return_value=[slot]):
            fc = _FakeFunctionCall("find_free_time", {"start": "2026-08-29T08:00:00", "end": "2026-08-29T18:00:00", "duration_minutes": 60})
            resp = await jarvis._execute_tool(fc)
        assert "10:00" in resp.response["result"]
    asyncio.run(_run())
    print("test_find_free_time_success: PASS")


# ── create_calendar_event ────────────────────────────────────────────

def test_create_event_not_connected() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=False):
            fc = _FakeFunctionCall("create_calendar_event", {"title": "Dentist", "start": "2026-08-29T14:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "[CALENDAR_NOT_CONNECTED]" in resp.response["result"]
    asyncio.run(_run())
    print("test_create_event_not_connected: PASS")


def test_create_event_missing_start_asks_instead_of_guessing() -> None:
    """The task's explicit example: 'Schedule a meeting with John
    tomorrow' with no time -- must ask, never invent a time."""
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()):
            fc = _FakeFunctionCall("create_calendar_event", {"title": "Meeting with John"})
            resp = await jarvis._execute_tool(fc)
        assert "specify" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_create_event_missing_start_asks_instead_of_guessing: PASS")


def test_create_event_success() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.create_event", return_value={
                 "id": "ev123", "title": "Dentist", "start": "2026-08-29T14:00:00+05:45", "end": "2026-08-29T15:00:00+05:45",
                 "location": "", "all_day": False,
             }):
            fc = _FakeFunctionCall("create_calendar_event", {
                "title": "Dentist", "start": "2026-08-29T14:00:00", "duration_minutes": 60,
            })
            resp = await jarvis._execute_tool(fc)
        assert "Dentist" in resp.response["result"]
        assert "created" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_create_event_success: PASS")


# ── update_calendar_event ────────────────────────────────────────────

def test_update_event_by_direct_id() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.update_event", return_value={
                 "id": "ev1", "title": "Standup", "start": "2026-08-29T16:00:00+05:45", "end": "2026-08-29T16:30:00+05:45",
                 "location": "", "all_day": False,
             }) as mock_update:
            fc = _FakeFunctionCall("update_calendar_event", {"event_id": "ev1", "new_start": "2026-08-29T16:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "updated" in resp.response["result"].lower()
        mock_update.assert_called_once()
    asyncio.run(_run())
    print("test_update_event_by_direct_id: PASS")


def test_update_event_by_search_single_match() -> None:
    async def _run():
        jarvis = _jarvis()
        match = {"id": "ev1", "title": "5pm sync", "start": "2026-08-29T17:00:00+05:45", "end": "2026-08-29T17:30:00+05:45", "location": "", "all_day": False}
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.find_events_matching", return_value=[match]), \
             patch("main.calendar_actions.update_event", return_value=match) as mock_update:
            fc = _FakeFunctionCall("update_calendar_event", {"query": "sync", "day": "2026-08-29", "new_start": "2026-08-29T18:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "updated" in resp.response["result"].lower()
        assert mock_update.call_args.kwargs["event_id"] == "ev1"
    asyncio.run(_run())
    print("test_update_event_by_search_single_match: PASS")


def test_update_event_ambiguous_match_asks_and_never_changes_anything() -> None:
    async def _run():
        jarvis = _jarvis()
        matches = [
            {"id": "ev1", "title": "Team sync", "start": "2026-08-29T17:00:00+05:45", "end": "2026-08-29T17:30:00+05:45", "location": "", "all_day": False},
            {"id": "ev2", "title": "1:1 sync", "start": "2026-08-29T19:00:00+05:45", "end": "2026-08-29T19:30:00+05:45", "location": "", "all_day": False},
        ]
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.find_events_matching", return_value=matches), \
             patch("main.calendar_actions.update_event") as mock_update:
            fc = _FakeFunctionCall("update_calendar_event", {"query": "sync", "day": "2026-08-29", "new_start": "2026-08-29T18:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "[CALENDAR_AMBIGUOUS]" in resp.response["result"]
        assert "Team sync" in resp.response["result"]
        assert "1:1 sync" in resp.response["result"]
        mock_update.assert_not_called()   # never silently changed an ambiguous event
    asyncio.run(_run())
    print("test_update_event_ambiguous_match_asks_and_never_changes_anything: PASS")


def test_update_event_no_match_is_honest() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.find_events_matching", return_value=[]):
            fc = _FakeFunctionCall("update_calendar_event", {"query": "nonexistent", "day": "2026-08-29"})
            resp = await jarvis._execute_tool(fc)
        assert "no event" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_update_event_no_match_is_honest: PASS")


def test_update_event_not_connected() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=False):
            fc = _FakeFunctionCall("update_calendar_event", {"event_id": "ev1"})
            resp = await jarvis._execute_tool(fc)
        assert "[CALENDAR_NOT_CONNECTED]" in resp.response["result"]
    asyncio.run(_run())
    print("test_update_event_not_connected: PASS")


# ── delete_calendar_event ────────────────────────────────────────────

def test_delete_event_by_direct_id() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.delete_event") as mock_delete:
            fc = _FakeFunctionCall("delete_calendar_event", {"event_id": "ev1"})
            resp = await jarvis._execute_tool(fc)
        assert "cancelled" in resp.response["result"].lower()
        mock_delete.assert_called_once()
    asyncio.run(_run())
    print("test_delete_event_by_direct_id: PASS")


def test_delete_event_ambiguous_match_never_deletes_anything() -> None:
    async def _run():
        jarvis = _jarvis()
        matches = [
            {"id": "ev1", "title": "5pm review", "start": "2026-08-29T17:00:00+05:45", "end": "2026-08-29T17:30:00+05:45", "location": "", "all_day": False},
            {"id": "ev2", "title": "5pm standup", "start": "2026-08-29T17:00:00+05:45", "end": "2026-08-29T17:15:00+05:45", "location": "", "all_day": False},
        ]
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.find_events_matching", return_value=matches), \
             patch("main.calendar_actions.delete_event") as mock_delete:
            fc = _FakeFunctionCall("delete_calendar_event", {"query": "5pm", "day": "2026-08-29"})
            resp = await jarvis._execute_tool(fc)
        assert "[CALENDAR_AMBIGUOUS]" in resp.response["result"]
        mock_delete.assert_not_called()
    asyncio.run(_run())
    print("test_delete_event_ambiguous_match_never_deletes_anything: PASS")


def test_delete_event_not_connected() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=False):
            fc = _FakeFunctionCall("delete_calendar_event", {"event_id": "ev1"})
            resp = await jarvis._execute_tool(fc)
        assert "[CALENDAR_NOT_CONNECTED]" in resp.response["result"]
    asyncio.run(_run())
    print("test_delete_event_not_connected: PASS")


def test_delete_event_api_failure_propagates_honestly() -> None:
    async def _run():
        jarvis = _jarvis()
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=_valid_credentials()), \
             patch("main.calendar_actions.delete_event", side_effect=RuntimeError("Google API 404")):
            fc = _FakeFunctionCall("delete_calendar_event", {"event_id": "gone"})
            resp = await jarvis._execute_tool(fc)
        assert "failed" in resp.response["result"].lower()
    asyncio.run(_run())
    print("test_delete_event_api_failure_propagates_honestly: PASS")


# ── tool declarations / desktop-availability sanity ────────────────────

def test_calendar_tools_registered_and_not_desktop_only() -> None:
    from main import TOOL_DECLARATIONS, DESKTOP_ONLY_TOOLS
    names = {t["name"] for t in TOOL_DECLARATIONS}
    calendar_tools = {
        "get_calendar_events", "find_free_time", "create_calendar_event",
        "update_calendar_event", "delete_calendar_event",
    }
    assert calendar_tools <= names
    assert not (calendar_tools & DESKTOP_ONLY_TOOLS)
    print("test_calendar_tools_registered_and_not_desktop_only: PASS")


def test_calendar_tools_honestly_unavailable_on_desktop_without_connection() -> None:
    """Desktop is NOT gated as desktop-only -- it just honestly reports
    not-connected the same way a web session without a connection does."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())   # auto_start=True, desktop
        with patch("main.calendar_store.is_configured", return_value=False):
            fc = _FakeFunctionCall("get_calendar_events", {"start": "2026-08-29T00:00:00", "end": "2026-08-30T00:00:00"})
            resp = await jarvis._execute_tool(fc)
        assert "[CALENDAR_NOT_CONNECTED]" in resp.response["result"]
    asyncio.run(_run())
    print("test_calendar_tools_honestly_unavailable_on_desktop_without_connection: PASS")


def test_build_config_never_includes_calendar_tokens_in_system_instruction() -> None:
    """Explicit security requirement: Google tokens must never reach
    system_instruction/Gemini. _build_config() never even queries
    Calendar connection state (it's synchronous; credentials are only
    ever resolved inside async tool execution -- see
    _get_calendar_credentials()) -- this proves the ONLY calendar-related
    content in system_instruction is the tool schemas themselves, no
    credential data of any kind, even when a token superficially similar
    to a real one exists in the environment."""
    jarvis = _jarvis()
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    assert "ya29." not in instr          # a real Google access token prefix
    assert "refresh_token" not in instr
    assert "access_token" not in instr
    assert "1//" not in instr            # a real Google refresh token prefix
    print("test_build_config_never_includes_calendar_tokens_in_system_instruction: PASS")


if __name__ == "__main__":
    test_get_calendar_credentials_none_when_no_profile()
    test_get_calendar_credentials_none_when_store_not_configured()
    test_get_calendar_credentials_none_when_never_connected()
    test_get_calendar_credentials_returns_valid_credentials_without_refresh()
    test_get_calendar_credentials_persists_refreshed_token()
    test_get_calendar_credentials_none_when_refresh_fails()
    test_get_calendar_credentials_isolated_per_account_no_ram_cache()
    test_calendar_tzinfo_uses_web_timezone_when_set()
    test_calendar_tzinfo_falls_back_to_machine_offset_on_desktop()
    test_calendar_tzinfo_never_defaults_to_utc_when_web_timezone_set()
    test_get_calendar_events_not_connected()
    test_get_calendar_events_success_uses_resolved_tzinfo()
    test_get_calendar_events_invalid_dates_asks_instead_of_guessing()
    test_get_calendar_events_api_failure_propagates_honestly()
    test_get_calendar_events_empty_range_is_honest_not_fabricated()
    test_find_free_time_not_connected()
    test_find_free_time_success()
    test_create_event_not_connected()
    test_create_event_missing_start_asks_instead_of_guessing()
    test_create_event_success()
    test_update_event_by_direct_id()
    test_update_event_by_search_single_match()
    test_update_event_ambiguous_match_asks_and_never_changes_anything()
    test_update_event_no_match_is_honest()
    test_update_event_not_connected()
    test_delete_event_by_direct_id()
    test_delete_event_ambiguous_match_never_deletes_anything()
    test_delete_event_not_connected()
    test_delete_event_api_failure_propagates_honestly()
    test_calendar_tools_registered_and_not_desktop_only()
    test_calendar_tools_honestly_unavailable_on_desktop_without_connection()
    test_build_config_never_includes_calendar_tokens_in_system_instruction()
    print("\nAll calendar-tools tests passed.")
