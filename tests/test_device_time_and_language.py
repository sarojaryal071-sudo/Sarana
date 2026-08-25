"""
tests/test_device_time_and_language.py — focused tests for two fixes:

  1. Device-local time: JarvisLive must report the user's actual device
     time (desktop: the local machine's own clock; web: the browser's
     IANA timezone, reported at /login/username and validated against the
     real zoneinfo database) for every user-facing/Gemini-facing time
     context — never the backend server's own clock/timezone, and never a
     hardcoded zone or offset.
  2. Natural Nepali-default language + a non-Sanskritized morning greeting
     (no more "Subha Prabhat").

Run with:
    .venv/Scripts/python.exe -m tests.test_device_time_and_language
"""
import asyncio
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer
from core.headless_surface import HeadlessSurface
from main import JarvisLive


# ── _local_now() / _set_web_timezone() ────────────────────────────────────

def test_local_now_defaults_to_server_local_time_on_desktop() -> None:
    """Desktop never calls _set_web_timezone — _local_now() must behave
    exactly like the old bare datetime.now() (naive, machine-local)."""
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._web_timezone is None
    now = jarvis._local_now()
    assert now.tzinfo is None
    assert abs((now - datetime.now()).total_seconds()) < 5
    print("test_local_now_defaults_to_server_local_time_on_desktop: PASS")


def test_set_web_timezone_accepts_real_iana_zone() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_web_timezone("Asia/Kathmandu")
    assert jarvis._web_timezone == "Asia/Kathmandu"
    print("test_set_web_timezone_accepts_real_iana_zone: PASS")


def test_set_web_timezone_rejects_invalid_zone_without_crashing() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_web_timezone("Not/ARealZone")
    assert jarvis._web_timezone is None, "an invalid zone must never be trusted"
    print("test_set_web_timezone_rejects_invalid_zone_without_crashing: PASS")


def test_local_now_uses_the_configured_timezone_not_server_clock() -> None:
    """Proves _local_now() actually looks the zone up via zoneinfo (so DST
    is handled by the IANA database, not a hardcoded offset) rather than
    just returning server time regardless."""
    jarvis = JarvisLive(HeadlessSurface())
    # UTC+14 — picked specifically because it's essentially never the CI
    # server's own zone, so a match here can only come from real lookup.
    jarvis._set_web_timezone("Pacific/Kiritimati")
    now = jarvis._local_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.now(ZoneInfo("Pacific/Kiritimati")).utcoffset()
    print("test_local_now_uses_the_configured_timezone_not_server_clock: PASS")


def test_build_config_current_time_context_uses_local_now() -> None:
    """_build_config()'s [CURRENT DATE & TIME] block — what answers "what
    time is it" — must come from _local_now(), not a bare server clock."""
    jarvis = JarvisLive(HeadlessSurface())
    fixed = datetime(2026, 6, 15, 3, 30)  # a time very unlikely to match "now" by accident
    with patch.object(JarvisLive, "_local_now", return_value=fixed):
        config = jarvis._build_config()
    assert fixed.strftime("%A, %B %d, %Y") in config.system_instruction
    assert "03:30 AM" in config.system_instruction
    print("test_build_config_current_time_context_uses_local_now: PASS")


class _FakeSession:
    def __init__(self):
        self.sent = []

    async def send_client_content(self, turns, turn_complete=True):
        self.sent.append(turns["parts"][0]["text"])


def test_startup_briefing_time_of_day_uses_local_now() -> None:
    """The greeting's time-of-day classification (late_night/morning/...)
    must be computed from _local_now(), not the server's own clock."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        fixed = datetime(2026, 6, 15, 2, 0)  # 2 AM → late_night, unambiguous
        with patch("main.pop_last_session", return_value=None), \
             patch.object(JarvisLive, "_local_now", return_value=fixed):
            await jarvis._send_startup_briefing()
        prompt = jarvis.session.sent[0]
        assert "late_night" in prompt
        print("test_startup_briefing_time_of_day_uses_local_now: PASS")

    asyncio.run(_run())


def test_run_wires_timezone_callback() -> None:
    from tests.test_phase7_lifecycle import _FakeDashboard

    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        fake_dashboard = _FakeDashboard()

        with patch("dashboard.server.DashboardServer", return_value=fake_dashboard), \
             patch("main.genai.Client"):
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.1)

            assert fake_dashboard._timezone_fn is not None, (
                "run() must wire dashboard.set_timezone_callback() before waiting"
            )
            assert fake_dashboard._timezone_fn == jarvis._set_web_timezone

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_run_wires_timezone_callback: PASS")


# ── /login/username's optional "timezone" field ───────────────────────────

def test_login_username_stores_and_forwards_valid_timezone() -> None:
    server = DashboardServer()
    received = []
    server.set_timezone_callback(lambda tz: received.append(tz))
    client = TestClient(server.app)

    resp = client.post(
        "/login/username",
        json={"username": "Saroj", "pin": "2057", "timezone": "Asia/Kathmandu"},
    )
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert server._session_timezones.get(token) == "Asia/Kathmandu"
    assert received == ["Asia/Kathmandu"]
    print("test_login_username_stores_and_forwards_valid_timezone: PASS")


def test_login_username_ignores_malformed_timezone_without_failing_login() -> None:
    server = DashboardServer()
    received = []
    server.set_timezone_callback(lambda tz: received.append(tz))
    client = TestClient(server.app)

    resp = client.post(
        "/login/username",
        json={"username": "Saroj", "pin": "2057", "timezone": "<script>bad</script>"},
    )
    assert resp.status_code == 200, "a malformed timezone must not break login itself"
    token = resp.json()["token"]
    assert token not in server._session_timezones
    assert received == []
    print("test_login_username_ignores_malformed_timezone_without_failing_login: PASS")


def test_login_username_works_unchanged_without_timezone_field() -> None:
    """Backward compatible — a client that never sends "timezone" at all
    (it's optional) must still log in exactly as before."""
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    print("test_login_username_works_unchanged_without_timezone_field: PASS")


# ── natural Nepali-default language + non-Sanskritized greeting ──────────

def test_identity_language_line_sets_natural_nepali_default() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    config = jarvis._build_config()
    instr = config.system_instruction
    assert "LANGUAGE:" in instr
    assert "Nepali" in instr
    assert "Sanskritized" in instr
    assert "backend" in instr and "API" in instr  # technical terms explicitly kept in English
    print("test_identity_language_line_sets_natural_nepali_default: PASS")


def test_identity_language_line_forbids_subha_prabhat() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    config = jarvis._build_config()
    assert "Subha Prabhat" in config.system_instruction  # named explicitly as what NOT to do
    print("test_identity_language_line_forbids_subha_prabhat: PASS")


def test_startup_briefing_prompt_discourages_subha_prabhat() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        with patch("main.pop_last_session", return_value=None):
            await jarvis._send_startup_briefing()
        prompt = jarvis.session.sent[0]
        assert "Subha Prabhat" in prompt
        print("test_startup_briefing_prompt_discourages_subha_prabhat: PASS")

    asyncio.run(_run())


if __name__ == "__main__":
    test_local_now_defaults_to_server_local_time_on_desktop()
    test_set_web_timezone_accepts_real_iana_zone()
    test_set_web_timezone_rejects_invalid_zone_without_crashing()
    test_local_now_uses_the_configured_timezone_not_server_clock()
    test_build_config_current_time_context_uses_local_now()
    test_startup_briefing_time_of_day_uses_local_now()
    test_run_wires_timezone_callback()
    test_login_username_stores_and_forwards_valid_timezone()
    test_login_username_ignores_malformed_timezone_without_failing_login()
    test_login_username_works_unchanged_without_timezone_field()
    test_identity_language_line_sets_natural_nepali_default()
    test_identity_language_line_forbids_subha_prabhat()
    test_startup_briefing_prompt_discourages_subha_prabhat()
    print("\nAll device-time/language tests passed.")
