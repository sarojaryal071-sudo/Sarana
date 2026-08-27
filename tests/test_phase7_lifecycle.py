"""
tests/test_phase7_lifecycle.py — Phase 7 web-lifecycle gating tests.

Proves, without touching the network or real audio hardware:
  1. auto_start=False: JarvisLive.run() starts the dashboard but does NOT
     enter the Gemini connect loop (genai.Client is never constructed)
     until the wake signal fires.
  2. Firing the wake callback (exactly what /api/wake already does) releases
     the gate and the connect loop proceeds (genai.Client gets constructed).
  3. auto_start=True (desktop's default — main() never passes this kwarg)
     skips the gate entirely; the connect loop starts immediately, with no
     wake needed — proving desktop behavior is unaffected by this phase.

DashboardServer itself is replaced with a lightweight fake so this test
never binds a real port or touches firewall/SSL setup — dashboard/server.py
is not modified this phase and is already covered by tests/test_dashboard_
phase3/4/6.py; this test is only about the gate main.py added around it.

Deployment-readiness update: sd.InputStream/RawOutputStream are also
patched to fail fast in every test that lets the connect loop actually
proceed. Before the deployment-readiness audio guard, _listen_audio()/
_play_audio() crashed near-instantly on their own (an accidental side
effect of a since-fixed bug), which is what made these tests' tight
sleep()-then-cancel() timing reliable. Now that those functions correctly
survive a hardware failure and stay alive across TaskGroup reconnects,
letting them open REAL local audio devices repeatedly made these tests
slow/flaky (real device open/close latency) — patching them keeps these
tests exactly as fast and hardware-independent as this docstring always
promised; tests/test_deployment_readiness.py is what actually exercises
the audio-guard behavior itself.

Run with:
    .venv/Scripts/python.exe -m tests.test_phase7_lifecycle
"""
import asyncio
from unittest.mock import patch, MagicMock

from core.headless_surface import HeadlessSurface
import main
from main import JarvisLive


class _FakeDashboard:
    """Just enough surface for run() to call without a real server."""

    def __init__(self):
        self._wake_fn = None
        self._connect_fn = None
        self._username_callback = None  # Phase 8
        self._interrupt_fn = None
        self._timezone_fn = None
        self._profile_fn = None
        self._logout_fn = None   # PostgreSQL memory migration
        self._location_fn = None   # Location foundation
        self._command_queue = asyncio.Queue()
        self._phone_audio_queue = asyncio.Queue()  # touched by _relay_phone_audio

    def set_connect_callback(self, fn):
        self._connect_fn = fn

    def set_wake_callback(self, fn):
        self._wake_fn = fn

    def set_username_callback(self, fn):
        self._username_callback = fn

    def set_interrupt_callback(self, fn):
        self._interrupt_fn = fn

    def set_timezone_callback(self, fn):
        self._timezone_fn = fn

    def set_profile_callback(self, fn):
        self._profile_fn = fn

    def set_logout_callback(self, fn):
        self._logout_fn = fn

    def set_location_callback(self, fn):
        self._location_fn = fn

    async def serve(self):
        await asyncio.Event().wait()  # never returns — mimics a live server

    async def broadcast(self, msg):
        pass


async def _run_briefly(jarvis, seconds=0.3):
    task = asyncio.create_task(jarvis.run())
    await asyncio.sleep(seconds)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return task


def test_auto_start_false_blocks_connect_loop() -> None:
    async def _run():
        surface = HeadlessSurface()
        jarvis = JarvisLive(surface, auto_start=False)
        assert jarvis._auto_start is False

        with patch("dashboard.server.DashboardServer", return_value=_FakeDashboard()), \
             patch("main.genai.Client") as mock_client, \
             patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
             patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")):
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.3)

            assert not task.done(), "run() should still be waiting on the start gate"
            assert mock_client.call_count == 0, (
                "genai.Client must NOT be constructed before wake — "
                "no Gemini connection, no mic, no speaker, no briefing"
            )
            assert jarvis._start_event is not None and not jarvis._start_event.is_set()

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_auto_start_false_blocks_connect_loop: PASS")


def test_wake_callback_releases_the_gate() -> None:
    async def _run():
        surface = HeadlessSurface()
        jarvis = JarvisLive(surface, auto_start=False)
        fake_dashboard = _FakeDashboard()

        with patch("dashboard.server.DashboardServer", return_value=fake_dashboard), \
             patch("main.genai.Client") as mock_client, \
             patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
             patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")):
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.15)
            assert mock_client.call_count == 0

            # Exactly what POST /api/wake already does today — no new route,
            # no new message type, the existing dashboard hook is what fires.
            assert fake_dashboard._wake_fn is not None, (
                "run() must wire dashboard.set_wake_callback() before waiting"
            )
            fake_dashboard._wake_fn()

            await asyncio.sleep(0.15)
            assert jarvis._start_event.is_set()
            assert mock_client.call_count >= 1, (
                "genai.Client should be constructed once the gate releases "
                "and the existing connect loop proceeds"
            )

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_wake_callback_releases_the_gate: PASS")


def test_auto_start_true_preserves_desktop_behavior() -> None:
    """Desktop's main() constructs JarvisLive(ui) with no auto_start kwarg —
    confirms the default keeps run() connecting immediately, no wake needed."""
    async def _run():
        surface = HeadlessSurface()
        jarvis = JarvisLive(surface)   # no auto_start kwarg — desktop's exact call shape
        assert jarvis._auto_start is True

        with patch("dashboard.server.DashboardServer", return_value=_FakeDashboard()), \
             patch("main.genai.Client") as mock_client, \
             patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
             patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")):
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.2)

            assert mock_client.call_count >= 1, (
                "with auto_start=True the connect loop must start immediately, "
                "exactly as it did before Phase 7"
            )

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_auto_start_true_preserves_desktop_behavior: PASS")


if __name__ == "__main__":
    test_auto_start_false_blocks_connect_loop()
    test_wake_callback_releases_the_gate()
    test_auto_start_true_preserves_desktop_behavior()
    print("\nAll Phase 7 tests passed.")
