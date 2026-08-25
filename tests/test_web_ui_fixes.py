"""
tests/test_web_ui_fixes.py — focused tests for the web-dashboard fixes:
  1. Web mic auto-start is a frontend-only change (see frontend/src/App.jsx);
     nothing here to test on the backend.
  2. POST /api/interrupt + DashboardServer.set_interrupt_callback() — reuses
     JarvisLive.interrupt() exactly like set_wake_callback reuses the
     existing wake mechanism.
  7. Web login greeting: _set_web_username() must re-arm the existing
     _send_startup_briefing() every login, not just the first one ever
     (see main.py's _pending_web_greeting), while leaving desktop's
     once-per-process _briefing_sent behavior untouched.
  8. Activity log vs. memory is a frontend-only change (RESET_FOR_LOGOUT
     wired in App.jsx / AssistantContext.jsx); nothing here to test on the
     backend — the persistent memory system (memory/memory_manager.py) is
     untouched by any of this work.

Run with:
    .venv/Scripts/python.exe -m tests.test_web_ui_fixes
"""
import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer
from core.headless_surface import HeadlessSurface
from main import JarvisLive
from tests.test_phase7_lifecycle import _FakeDashboard


def _server_with_token(token: str) -> DashboardServer:
    server = DashboardServer()
    server._tokens.add(token)
    return server


# ── item 2: /api/interrupt ────────────────────────────────────────────────

def test_interrupt_endpoint_requires_auth() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/api/interrupt")
    assert resp.status_code == 401
    print("test_interrupt_endpoint_requires_auth: PASS")


def test_interrupt_endpoint_calls_registered_callback() -> None:
    server = _server_with_token("test-token-interrupt")
    fired = []
    server.set_interrupt_callback(lambda: fired.append(True))
    client = TestClient(server.app)
    resp = client.post("/api/interrupt", headers={"Authorization": "Bearer test-token-interrupt"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert fired == [True]
    print("test_interrupt_endpoint_calls_registered_callback: PASS")


def test_interrupt_endpoint_safe_noop_when_unwired() -> None:
    """No callback registered (e.g. dashboard started standalone) — must not 500."""
    server = _server_with_token("test-token-interrupt2")
    client = TestClient(server.app)
    resp = client.post("/api/interrupt", headers={"Authorization": "Bearer test-token-interrupt2"})
    assert resp.status_code == 200
    print("test_interrupt_endpoint_safe_noop_when_unwired: PASS")


def test_run_wires_interrupt_callback_to_jarvis_interrupt() -> None:
    """run() must wire dashboard.set_interrupt_callback() to the exact same
    interrupt() the desktop UI's INTERRUPT button/Esc key already call —
    not a second interruption mechanism."""
    async def _run():
        surface = HeadlessSurface()
        jarvis = JarvisLive(surface, auto_start=False)
        fake_dashboard = _FakeDashboard()

        with patch("dashboard.server.DashboardServer", return_value=fake_dashboard), \
             patch("main.genai.Client"):
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.1)

            assert fake_dashboard._interrupt_fn is not None, (
                "run() must wire dashboard.set_interrupt_callback() before waiting"
            )
            assert fake_dashboard._interrupt_fn == jarvis.interrupt

            jarvis.audio_in_queue = asyncio.Queue()
            await jarvis.audio_in_queue.put(b"\x00\x01")
            fake_dashboard._interrupt_fn()
            assert jarvis._interrupted is True
            assert jarvis.audio_in_queue.empty(), "interrupt() must drain queued audio"

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_run_wires_interrupt_callback_to_jarvis_interrupt: PASS")


# ── item 7: every web login gets a greeting, not just the first ever ──────

def test_pending_web_greeting_set_when_no_session_yet() -> None:
    """The very first login (gate still waiting, no Gemini session up yet)
    — run()'s existing post-connect check picks this flag up (see run())."""
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    assert jarvis._pending_web_greeting is False
    jarvis._set_web_username("Alex")
    assert jarvis._pending_web_greeting is True
    print("test_pending_web_greeting_set_when_no_session_yet: PASS")


def test_web_greeting_fires_immediately_when_session_already_active() -> None:
    """A second (or later) login on a long-lived process, while a Gemini
    session from an earlier login is still connected — must still greet,
    reusing _send_startup_briefing() unchanged, without waiting for a
    reconnect that may never happen."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._loop = asyncio.get_event_loop()
        jarvis.session = object()  # stand-in for "a Gemini session is connected"

        with patch.object(JarvisLive, "_send_startup_briefing", new=AsyncMock()) as mocked:
            jarvis._set_web_username("Priya")
            # Took the immediate-schedule path, not the "wait for connect" flag.
            assert jarvis._pending_web_greeting is False
            await asyncio.sleep(0.05)
            mocked.assert_called_once()

    asyncio.run(_run())
    print("test_web_greeting_fires_immediately_when_session_already_active: PASS")


def test_desktop_auto_start_briefing_flag_untouched_by_web_username() -> None:
    """Desktop (auto_start=True) never calls _set_web_username — its
    once-per-process _briefing_sent gating must be exactly as before."""
    jarvis = JarvisLive(HeadlessSurface())  # auto_start defaults True
    assert jarvis._auto_start is True
    assert jarvis._briefing_sent is False
    assert jarvis._pending_web_greeting is False
    print("test_desktop_auto_start_briefing_flag_untouched_by_web_username: PASS")


if __name__ == "__main__":
    test_interrupt_endpoint_requires_auth()
    test_interrupt_endpoint_calls_registered_callback()
    test_interrupt_endpoint_safe_noop_when_unwired()
    test_run_wires_interrupt_callback_to_jarvis_interrupt()
    test_pending_web_greeting_set_when_no_session_yet()
    test_web_greeting_fires_immediately_when_session_already_active()
    test_desktop_auto_start_briefing_flag_untouched_by_web_username()
    print("\nAll web UI fix tests passed.")
