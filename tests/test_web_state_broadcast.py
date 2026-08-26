"""
tests/test_web_state_broadcast.py -- Phase 1 (web UI state) regression
tests: JarvisLive._push_state() must set the SAME authoritative state on
the UI surface desktop already uses (ui.py's set_state(), unchanged) AND
broadcast it to the web dashboard over /ws — through
DashboardServer.broadcast_state(), never through broadcast() (which would
pollute the Activity Log's history buffer with non-conversational state
pings — see that method's docstring).

Run with:
    .venv/Scripts/python.exe -m tests.test_web_state_broadcast
"""
import asyncio

from unittest.mock import PropertyMock, patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive


class _RecordingDashboard:
    """Fake dashboard: records every broadcast_state()/broadcast() call
    separately, so a test can assert state pushes go through the right
    one."""

    def __init__(self):
        self.states: list[str] = []
        self.broadcasts: list[dict] = []

    async def broadcast_state(self, state: str) -> None:
        self.states.append(state)

    async def broadcast(self, msg: dict) -> None:
        self.broadcasts.append(msg)


def _jarvis_with_dashboard(loop) -> tuple[JarvisLive, _RecordingDashboard]:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._loop = loop
    jarvis._dashboard = _RecordingDashboard()
    return jarvis, jarvis._dashboard


# ── _push_state() basics ─────────────────────────────────────────────────

def test_push_state_sets_ui_state_exactly_like_before() -> None:
    """Desktop's own ui.set_state() call must still happen, unchanged —
    _push_state() is additive, not a replacement of that mechanism."""
    jarvis = JarvisLive(HeadlessSurface())
    calls = []
    jarvis.ui.set_state = lambda s: calls.append(s)
    jarvis._push_state("THINKING")
    assert calls == ["THINKING"]
    print("test_push_state_sets_ui_state_exactly_like_before: PASS")


def test_push_state_broadcasts_via_broadcast_state_not_broadcast() -> None:
    async def _run():
        loop = asyncio.get_event_loop()
        jarvis, dash = _jarvis_with_dashboard(loop)
        jarvis._push_state("LISTENING")
        await asyncio.sleep(0.05)   # run_coroutine_threadsafe needs a tick
        assert dash.states == ["LISTENING"]
        assert dash.broadcasts == [], (
            "state pushes must never go through broadcast() — that would "
            "pollute the Activity Log's history with non-conversational pings"
        )
    asyncio.run(_run())
    print("test_push_state_broadcasts_via_broadcast_state_not_broadcast: PASS")


def test_push_state_with_no_dashboard_does_not_raise() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._dashboard = None
    jarvis._push_state("SLEEPING")   # must not raise
    print("test_push_state_with_no_dashboard_does_not_raise: PASS")


def test_push_state_survives_broadcast_exception() -> None:
    async def _run():
        loop = asyncio.get_event_loop()
        jarvis, dash = _jarvis_with_dashboard(loop)

        async def _boom(state):
            raise RuntimeError("client disconnected mid-send")
        dash.broadcast_state = _boom

        jarvis._push_state("THINKING")   # must not raise, even though the broadcast itself fails
        await asyncio.sleep(0.05)
    asyncio.run(_run())
    print("test_push_state_survives_broadcast_exception: PASS")


# ── set_speaking() -> the right state, respecting mute ───────────────────

def test_set_speaking_true_pushes_speaking() -> None:
    async def _run():
        loop = asyncio.get_event_loop()
        jarvis, dash = _jarvis_with_dashboard(loop)
        jarvis.set_speaking(True)
        await asyncio.sleep(0.05)
        assert dash.states == ["SPEAKING"]
    asyncio.run(_run())
    print("test_set_speaking_true_pushes_speaking: PASS")


def test_set_speaking_false_pushes_listening_when_not_muted() -> None:
    async def _run():
        loop = asyncio.get_event_loop()
        jarvis, dash = _jarvis_with_dashboard(loop)
        jarvis.set_speaking(False)   # HeadlessSurface.muted is always False
        await asyncio.sleep(0.05)
        assert dash.states == ["LISTENING"]
    asyncio.run(_run())
    print("test_set_speaking_false_pushes_listening_when_not_muted: PASS")


def test_set_speaking_false_pushes_nothing_when_muted() -> None:
    """Desktop's own muted-suppression logic (set_speaking()'s `elif not
    self.ui.muted`) must keep working exactly as before — a muted surface
    never gets told to show LISTENING."""
    async def _run():
        loop = asyncio.get_event_loop()
        jarvis, dash = _jarvis_with_dashboard(loop)
        with patch.object(HeadlessSurface, "muted", new_callable=PropertyMock, return_value=True):
            jarvis.set_speaking(False)
            await asyncio.sleep(0.05)
        assert dash.states == []
    asyncio.run(_run())
    print("test_set_speaking_false_pushes_nothing_when_muted: PASS")


# ── DashboardServer.broadcast_state() itself ─────────────────────────────

def test_broadcast_state_never_touches_history() -> None:
    async def _run():
        from dashboard.server import DashboardServer
        server = DashboardServer()
        server._history = []
        await server.broadcast_state("THINKING")
        await server.broadcast_state("LISTENING")
        assert server._history == [], (
            "broadcast_state() must never append to self._history — see its own docstring"
        )
    asyncio.run(_run())
    print("test_broadcast_state_never_touches_history: PASS")


def test_broadcast_still_appends_to_history() -> None:
    """Sanity: the ORIGINAL broadcast() (used for real conversation/log
    messages) must be completely unaffected by adding broadcast_state()."""
    async def _run():
        from dashboard.server import DashboardServer
        server = DashboardServer()
        server._history = []
        await server.broadcast({"type": "sys", "text": "hello"})
        assert server._history == [{"type": "sys", "text": "hello"}]
    asyncio.run(_run())
    print("test_broadcast_still_appends_to_history: PASS")


if __name__ == "__main__":
    test_push_state_sets_ui_state_exactly_like_before()
    test_push_state_broadcasts_via_broadcast_state_not_broadcast()
    test_push_state_with_no_dashboard_does_not_raise()
    test_push_state_survives_broadcast_exception()
    test_set_speaking_true_pushes_speaking()
    test_set_speaking_false_pushes_listening_when_not_muted()
    test_set_speaking_false_pushes_nothing_when_muted()
    test_broadcast_state_never_touches_history()
    test_broadcast_still_appends_to_history()
    print("\nAll web-state-broadcast tests passed.")
