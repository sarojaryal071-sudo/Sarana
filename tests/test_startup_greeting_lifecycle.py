"""
tests/test_startup_greeting_lifecycle.py — regression tests for the
startup lifecycle fix: login -> short pause -> greeting -> THEN listening,
never "listening" announced while the greeting is still pending or being
spoken.

Root cause fixed: run()'s connect loop used to push
_push_state("LISTENING") immediately after connecting, unconditionally —
BEFORE the startup greeting task was even scheduled. The frontend (mic
auto-start, the "LISTENING" display) would treat SARANA as ready for
normal input while it was still about to greet, which was both misleading
(the mic is actually still being ignored during the greeting — see
_listen_audio()/_relay_phone_audio()'s own "not speaking" gate) and made
the greeting feel like it was talking over the user.

Fix: when a greeting is pending, stay in THINKING (already pushed at the
top of the connect attempt) until set_speaking(False) — called naturally
once the greeting's own audio finishes playing (see _play_audio()) —
pushes the real LISTENING transition itself. No new state, no artificial
delay: the short, purposeful pause is _send_startup_briefing()'s own
existing asyncio.sleep(0.3), unchanged.

Reuses tests/test_voice_reconnect.py's fake Gemini Live session harness
and tests/test_phase7_lifecycle.py's _FakeDashboard, same reasoning as
tests/test_login_greeting.py's own docstring.

Run with:
    python -m pytest tests/test_startup_greeting_lifecycle.py -q
"""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import main
from core.headless_surface import HeadlessSurface
from main import JarvisLive
from users import user_db
from tests.test_voice_reconnect import _FakeGenaiClient
from tests.test_phase7_lifecycle import _FakeDashboard


class _RecordingSurface(HeadlessSurface):
    """HeadlessSurface, plus a plain list of every set_state() call, in
    the order they happened — everything else (write_log, muted, etc.)
    behaves exactly like the real headless surface."""

    def __init__(self) -> None:
        super().__init__()
        self.states: list[str] = []

    def set_state(self, state: str) -> None:
        self.states.append(state)


def _isolated_desktop_config(tmp_dir: str, user_name: str) -> Path:
    cfg_path = Path(tmp_dir) / "api_keys.json"
    cfg_path.write_text(
        json.dumps({"gemini_api_key": "x", "assistant_name": "SARANA", "user_name": user_name}),
        encoding="utf-8",
    )
    return cfg_path


def test_greeting_pending_keeps_state_off_listening_until_it_speaks() -> None:
    """Desktop path (auto_start=True, briefing enabled): right after
    connecting, with a greeting about to fire, the state must NOT be
    LISTENING yet — and must become LISTENING only once the greeting's
    own audio finishes (set_speaking(False), exactly what _play_audio()
    calls naturally at the end of the greeting's turn)."""
    async def _run():
        recorder = []
        sent_messages = []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        surface = _RecordingSurface()
        tmp = tempfile.TemporaryDirectory()
        cfg_path = _isolated_desktop_config(tmp.name, "Saroj")
        db_path = Path(tmp.name) / "sarana.db"

        with tmp, \
             patch.object(main, "API_CONFIG_PATH", cfg_path), \
             patch.object(user_db, "DB_PATH", db_path), \
             patch("main.get_brief_enabled", return_value=True), \
             patch.object(main, "sd", None), \
             patch("dashboard.server.DashboardServer", return_value=_FakeDashboard()), \
             patch("main.genai.Client", side_effect=make_client):
            user_db.init_db()
            jarvis = JarvisLive(surface)   # auto_start=True (desktop default)
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.5)

            assert len(sent_messages) == 1, "the greeting must have been sent"
            assert "LISTENING" not in surface.states, (
                "must not announce LISTENING while the greeting is still pending/being spoken"
            )
            assert surface.states, "some state (e.g. THINKING) must still have been pushed while connecting"

            # Simulate the greeting's own audio finishing — exactly what
            # _play_audio() does naturally once real audio streams back
            # and the turn ends.
            jarvis.set_speaking(False)
            assert surface.states[-1] == "LISTENING", (
                "listening must start once the greeting has actually finished, not before"
            )

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_greeting_pending_keeps_state_off_listening_until_it_speaks: PASS")


def test_no_pending_greeting_still_goes_straight_to_listening() -> None:
    """When there is nothing to greet with (briefing disabled), behavior is
    unchanged from before this fix: LISTENING fires immediately on connect
    — no artificial delay is introduced for the no-greeting case."""
    async def _run():
        recorder = []
        sent_messages = []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        surface = _RecordingSurface()
        tmp = tempfile.TemporaryDirectory()
        cfg_path = _isolated_desktop_config(tmp.name, "Saroj")
        db_path = Path(tmp.name) / "sarana.db"

        with tmp, \
             patch.object(main, "API_CONFIG_PATH", cfg_path), \
             patch.object(user_db, "DB_PATH", db_path), \
             patch("main.get_brief_enabled", return_value=False), \
             patch.object(main, "sd", None), \
             patch("dashboard.server.DashboardServer", return_value=_FakeDashboard()), \
             patch("main.genai.Client", side_effect=make_client):
            user_db.init_db()
            jarvis = JarvisLive(surface)
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.3)

            assert sent_messages == [], "no greeting should have been sent"
            assert "LISTENING" in surface.states, "with nothing to greet, LISTENING must still fire immediately"

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_no_pending_greeting_still_goes_straight_to_listening: PASS")


if __name__ == "__main__":
    test_greeting_pending_keeps_state_off_listening_until_it_speaks()
    test_no_pending_greeting_still_goes_straight_to_listening()
    print("\nAll startup greeting lifecycle tests passed.")
