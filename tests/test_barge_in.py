"""
tests/test_barge_in.py — regression tests for speech barge-in (the user
talking over SARANA mid-response).

Root cause fixed: response.server_content.interrupted (Gemini Live's own
server-side VAD barge-in signal) was never read anywhere in main.py. When
the user talked over SARANA, the backend kept queuing/streaming the
interrupted response's own audio exactly as if nothing had happened —
"SARANA continues speaking after the user has interrupted" / "old audio
continues playing after a new turn starts".

Fix: _receive_audio() now reacts to sc.interrupted by (1) discarding any
further audio for the interrupted response, (2) draining whatever's
already queued for local/browser playback, (3) telling the browser to
flush anything it already received (dashboard/server.py's
broadcast_audio_stop() -> audioOut.js's stopPlayback()), and (4) returning
to LISTENING immediately — all WITHOUT touching any active backend task
(self._active_tool_task), which is a completely separate concept (see
tests/test_task_cancellation.py).

Run with:
    python -m pytest tests/test_barge_in.py -q
"""
import asyncio
from types import SimpleNamespace

from core.headless_surface import HeadlessSurface
from main import JarvisLive


def _sc(**kw):
    defaults = dict(output_transcription=None, input_transcription=None,
                     turn_complete=False, interrupted=False)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _resp(**kw):
    defaults = dict(data=None, server_content=None, tool_call=None, tool_call_cancellation=None)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class _FakeSession:
    def __init__(self, responses):
        self._responses = responses

    async def receive(self):
        for r in self._responses:
            yield r
        while True:
            await asyncio.sleep(3600)
            yield None

    async def send_tool_response(self, *, function_responses):
        pass


class _RecordingSurface(HeadlessSurface):
    def __init__(self):
        super().__init__()
        self.states: list[str] = []

    def set_state(self, state: str) -> None:
        self.states.append(state)


class _FakeDashboardAudioStop:
    def __init__(self):
        self.stop_calls = 0

    async def broadcast_audio_stop(self):
        self.stop_calls += 1


def _jarvis_with_session(responses):
    surface = _RecordingSurface()
    jarvis = JarvisLive(surface, auto_start=False)
    jarvis.audio_in_queue   = asyncio.Queue()
    jarvis.out_queue        = asyncio.Queue(maxsize=200)
    jarvis._tool_call_queue = asyncio.Queue()
    jarvis._turn_done_event = asyncio.Event()
    jarvis.session = _FakeSession(responses)
    dashboard = _FakeDashboardAudioStop()
    jarvis._dashboard = dashboard
    return jarvis, surface, dashboard


async def _pump(jarvis, seconds=0.15):
    task = asyncio.create_task(jarvis._receive_audio())
    await asyncio.sleep(seconds)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_interrupted_stops_audio_and_signals_browser() -> None:
    """Some audio queues up, then Gemini reports the user barged in — the
    queued audio must be drained, the assistant must return to LISTENING,
    and the browser must be told to flush its own already-scheduled audio."""
    async def _run():
        responses = [
            _resp(data=b"\x00\x01" * 500),          # SARANA already speaking
            _resp(server_content=_sc(interrupted=True)),   # user barges in
        ]
        jarvis, surface, dashboard = _jarvis_with_session(responses)

        await _pump(jarvis)

        assert jarvis.audio_in_queue.empty(), "already-queued audio must be discarded on barge-in"
        assert jarvis._interrupted is True
        assert surface.states[-1] == "LISTENING", "must return to listening immediately, not wait for the old turn"
        assert dashboard.stop_calls == 1, "the browser must be told to flush its own scheduled audio exactly once"

    asyncio.run(_run())
    print("test_interrupted_stops_audio_and_signals_browser: PASS")


def test_stale_audio_not_played_after_interruption() -> None:
    """Audio that arrives AFTER the interruption signal (e.g. the tail end
    of the response Gemini was still flushing) must be discarded, not
    queued for playback."""
    async def _run():
        responses = [
            _resp(server_content=_sc(interrupted=True)),
            _resp(data=b"\x02\x03" * 500),   # stale trailing audio for the interrupted response
        ]
        jarvis, surface, dashboard = _jarvis_with_session(responses)

        await _pump(jarvis)

        assert jarvis.audio_in_queue.empty(), "stale audio for an interrupted response must never be queued"

    asyncio.run(_run())
    print("test_stale_audio_not_played_after_interruption: PASS")


def test_new_turn_processed_normally_after_barge_in() -> None:
    """Once the interrupted turn's own turn_complete arrives, self._interrupted
    resets and a genuinely NEW turn's audio is queued normally."""
    async def _run():
        responses = [
            _resp(server_content=_sc(interrupted=True)),
            _resp(server_content=_sc(turn_complete=True)),   # closes out the interrupted turn
            _resp(data=b"\x09\x0a" * 500),                    # brand-new turn's own audio
        ]
        jarvis, surface, dashboard = _jarvis_with_session(responses)

        await _pump(jarvis)

        assert jarvis._interrupted is False, "the flag must clear once the interrupted turn's turn_complete arrives"
        assert not jarvis.audio_in_queue.empty(), "a genuinely new turn's audio must be queued normally"

    asyncio.run(_run())
    print("test_new_turn_processed_normally_after_barge_in: PASS")


def test_barge_in_never_touches_active_tool_task() -> None:
    """Ordinary speech interruption must NEVER cancel or otherwise touch an
    in-flight backend task — that is a completely separate concept (see
    tests/test_task_cancellation.py's explicit-cancellation tests)."""
    async def _run():
        responses = [_resp(server_content=_sc(interrupted=True))]
        jarvis, surface, dashboard = _jarvis_with_session(responses)

        active = asyncio.ensure_future(asyncio.sleep(10))
        jarvis._active_tool_task = active
        jarvis._active_tool_name = "create_calendar_event"

        try:
            await _pump(jarvis)
            assert not active.done(), "an ordinary speech interruption must never cancel an active backend task"
            assert jarvis._active_tool_task is active, "barge-in handling must not touch task bookkeeping at all"
        finally:
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)

    asyncio.run(_run())
    print("test_barge_in_never_touches_active_tool_task: PASS")


if __name__ == "__main__":
    test_interrupted_stops_audio_and_signals_browser()
    test_stale_audio_not_played_after_interruption()
    test_new_turn_processed_normally_after_barge_in()
    test_barge_in_never_touches_active_tool_task()
    print("\nAll barge-in tests passed.")
