"""
tests/test_dashboard_phase9.py — Phase 9 tests: auto-wake on username login,
"no sir when a name is known", and the news-free time-aware startup greeting.

Run with:
    .venv/Scripts/python.exe -m tests.test_dashboard_phase9
"""
import asyncio
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer
from core.headless_surface import HeadlessSurface
import main
from main import JarvisLive, _time_of_day_category


def _server() -> DashboardServer:
    return DashboardServer()


# ── auto-wake on username login ──────────────────────────────────────────

def test_username_login_also_fires_wake() -> None:
    server = _server()
    woke = []
    server.set_wake_callback(lambda: woke.append(True))
    client = TestClient(server.app)
    resp = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    assert resp.status_code == 200
    assert woke == [True], "logging in with a username must also start Jarvis (no separate WAKE press)"
    print("test_username_login_also_fires_wake: PASS")


def test_pin_login_does_not_fire_wake() -> None:
    """Remote Access is intentionally left as-is — only username login
    auto-wakes (see dashboard/server.py's Phase 9 docstring addendum)."""
    server = _server()
    woke = []
    server.set_wake_callback(lambda: woke.append(True))
    client = TestClient(server.app)
    key = server.new_key()
    resp = client.post("/login", json={"pin": key})
    assert resp.status_code == 200
    assert woke == [], "PIN-based Remote Access must not be changed by Phase 9"
    print("test_pin_login_does_not_fire_wake: PASS")


# ── _time_of_day_category — pure function ────────────────────────────────

def test_time_of_day_category_boundaries() -> None:
    cases = {
        0: "late_night", 4: "late_night",
        5: "early_morning", 7: "early_morning",
        8: "morning", 11: "morning",
        12: "afternoon", 16: "afternoon",
        17: "evening", 20: "evening",
        21: "night", 23: "night",
    }
    for hour, expected in cases.items():
        assert _time_of_day_category(hour) == expected, hour
    print("test_time_of_day_category_boundaries: PASS")


# ── "no sir when a name is known" ────────────────────────────────────────

def test_address_clause_forbids_sir_for_web_username() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_web_username("Quillan")
    config = jarvis._build_config()
    assert "Always call them 'Quillan'" in config.system_instruction
    assert 'Never say "sir", "efendim"' in config.system_instruction
    print("test_address_clause_forbids_sir_for_web_username: PASS")


def test_address_clause_fallback_unchanged_when_no_name() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._web_user_name is None
    config = jarvis._build_config()
    # Only reachable if config/api_keys.json also has no user_name set —
    # tolerate either case, just confirm no crash and a sane ADDRESS line
    # exists either way (name-known or the original sir/efendim fallback).
    assert "ADDRESS:" in config.system_instruction
    print("test_address_clause_fallback_unchanged_when_no_name: PASS")


def test_current_user_name_prioritizes_web_over_config() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._current_user_name() == jarvis._current_user_name()  # stable, no crash
    jarvis._set_web_username("Alex")
    assert jarvis._current_user_name() == "Alex"
    print("test_current_user_name_prioritizes_web_over_config: PASS")


def test_speak_error_omits_sir_when_name_known() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_web_username("Quillan")
    captured = []
    jarvis.speak = lambda text: captured.append(text)  # bypass the real session requirement
    jarvis.speak_error("web_search", "boom")
    assert captured, "speak() should have been called"
    assert captured[0].startswith("Quillan, "), captured[0]
    assert "Sir" not in captured[0]
    print("test_speak_error_omits_sir_when_name_known: PASS")


def test_speak_error_keeps_sir_fallback_when_no_name() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._current_user_name = lambda: ""  # force the no-name case regardless of local config
    captured = []
    jarvis.speak = lambda text: captured.append(text)
    jarvis.speak_error("web_search", "boom")
    assert captured[0].startswith("Sir, "), captured[0]
    print("test_speak_error_keeps_sir_fallback_when_no_name: PASS")


# ── startup greeting: no news, time-aware, username-aware ───────────────

class _FakeSession:
    def __init__(self):
        self.sent = []

    async def send_client_content(self, turns, turn_complete=True):
        self.sent.append(turns["parts"][0]["text"])


def test_startup_briefing_has_no_news_and_mentions_time_category() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        jarvis._set_web_username("Quillan")

        with patch("main.pop_last_session", return_value=None):
            await jarvis._send_startup_briefing()

        assert len(jarvis.session.sent) == 1, "must be a single-phase greeting, no separate news phase"
        prompt = jarvis.session.sent[0]
        # The old two-phase design's specific news-delivery framing must be
        # gone — the new prompt is allowed to say "no news" (it does, on
        # purpose), just never asks Gemini to report headlines.
        assert "headlines" not in prompt.lower()
        assert "top news" not in prompt.lower()
        assert "no news" in prompt.lower() or "nothing to fetch" in prompt.lower()
        assert "Quillan" in prompt
        assert "never as 'sir' or 'efendim'" in prompt
        assert "category:" in prompt
        print("test_startup_briefing_has_no_news_and_mentions_time_category: PASS")

    asyncio.run(_run())


def test_startup_briefing_no_crash_without_username() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        with patch("main.pop_last_session", return_value=None):
            await jarvis._send_startup_briefing()
        assert len(jarvis.session.sent) == 1
        print("test_startup_briefing_no_crash_without_username: PASS")

    asyncio.run(_run())


def test_headless_still_no_pyqt6() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis is not None
    leaked = [m for m in sys.modules if m == "PyQt6" or m.startswith("PyQt6.")]
    assert not leaked, f"PyQt6 modules leaked into sys.modules: {leaked}"
    print("test_headless_still_no_pyqt6: PASS — sys.modules has no PyQt6 entries")


if __name__ == "__main__":
    test_username_login_also_fires_wake()
    test_pin_login_does_not_fire_wake()
    test_time_of_day_category_boundaries()
    test_address_clause_forbids_sir_for_web_username()
    test_address_clause_fallback_unchanged_when_no_name()
    test_current_user_name_prioritizes_web_over_config()
    test_speak_error_omits_sir_when_name_known()
    test_speak_error_keeps_sir_fallback_when_no_name()
    test_startup_briefing_has_no_news_and_mentions_time_category()
    test_startup_briefing_no_crash_without_username()
    test_headless_still_no_pyqt6()
    print("\nAll Phase 9 tests passed.")
