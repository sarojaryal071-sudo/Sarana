"""
tests/test_tool_call_async.py — regression tests for decoupling tool
execution from the Gemini receive loop.

Root cause fixed: _receive_audio() used to await each tool call in-line,
sequentially, on the same loop that drains Gemini's `session.receive()`
stream — so a slow tool (network I/O) paused that loop for as long as the
tool took, delaying everything else the loop is responsible for noticing
(new turns, and critically, Gemini's own barge-in signal — see
tests/test_barge_in.py).

Fix: tool-call batches are now handed to a dedicated background consumer
(_process_tool_calls()/_handle_tool_batch(), fed by self._tool_call_queue)
— the same "bounded queue + one consumer" pattern already used for
out_queue/audio_in_queue/_phone_audio_queue in this file. Function calls
within one batch are still executed strictly in order, and every response
in a batch is still sent together in one send_tool_response() call —
exactly the original function-response contract, just off the receive
loop.

Run with:
    python -m pytest tests/test_tool_call_async.py -q
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from google.genai import types as genai_types

from core.headless_surface import HeadlessSurface
from main import JarvisLive


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


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
    """Feeds a fixed sequence of fake responses through .receive(), then
    parks forever (mirroring the real SDK's behavior between turns) so
    _receive_audio() never raises once the script is exhausted — tests
    cancel the task themselves when done. Records every send_tool_response()
    call, in the order it happened."""

    def __init__(self, responses):
        self._responses = responses
        self.tool_responses: list[list] = []

    async def receive(self):
        for r in self._responses:
            yield r
        while True:
            await asyncio.sleep(3600)
            yield None

    async def send_tool_response(self, *, function_responses):
        if not isinstance(function_responses, list):
            function_responses = [function_responses]
        self.tool_responses.append(function_responses)


def _jarvis_with_session(responses) -> tuple[JarvisLive, _FakeSession]:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis.audio_in_queue   = asyncio.Queue()
    jarvis.out_queue        = asyncio.Queue(maxsize=200)
    jarvis._tool_call_queue = asyncio.Queue()
    jarvis._turn_done_event = asyncio.Event()
    session = _FakeSession(responses)
    jarvis.session = session
    return jarvis, session


async def _run_receive_and_worker(jarvis, seconds):
    recv_task   = asyncio.create_task(jarvis._receive_audio())
    worker_task = asyncio.create_task(jarvis._process_tool_calls())
    await asyncio.sleep(seconds)
    for t in (recv_task, worker_task):
        t.cancel()
    await asyncio.gather(recv_task, worker_task, return_exceptions=True)


def test_receive_loop_continues_while_tool_runs() -> None:
    """A slow tool call must not block the receive loop from processing
    events that arrive after it — proven here by a plain audio chunk
    arriving right after the tool_call event, which must be queued for
    playback well before the (deliberately slow) tool finishes."""
    async def _run():
        fc = _FakeFunctionCall("get_weather", {"place": "Kathmandu"}, "fc1")
        responses = [
            _resp(tool_call=SimpleNamespace(function_calls=[fc])),
            _resp(data=b"\x00\x01" * 100),   # arrives "while the tool is running"
        ]
        jarvis, session = _jarvis_with_session(responses)

        started = asyncio.Event()

        async def _slow_execute(fc):
            started.set()
            await asyncio.sleep(0.4)
            return genai_types.FunctionResponse(id=fc.id, name=fc.name, response={"result": "ok"})

        jarvis._execute_tool = _slow_execute

        recv_task   = asyncio.create_task(jarvis._receive_audio())
        worker_task = asyncio.create_task(jarvis._process_tool_calls())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)

            # The tool is still running (0.4s sleep, barely started) — but
            # the SECOND response (plain audio) must already have been
            # drained by the receive loop, proving it was never blocked.
            await asyncio.sleep(0.05)
            assert not jarvis.audio_in_queue.empty(), (
                "receive loop must keep draining subsequent events while a tool runs in the background"
            )
            assert session.tool_responses == [], "the tool hasn't finished yet — no response sent"

            await asyncio.sleep(0.5)   # let the slow tool finish
            assert len(session.tool_responses) == 1
            assert session.tool_responses[0][0].id == "fc1"
        finally:
            recv_task.cancel()
            worker_task.cancel()
            await asyncio.gather(recv_task, worker_task, return_exceptions=True)

    asyncio.run(_run())
    print("test_receive_loop_continues_while_tool_runs: PASS")


def test_tool_response_delivered_correctly_with_matching_id_and_name() -> None:
    async def _run():
        fc = _FakeFunctionCall("get_weather", {"place": "Pokhara"}, "abc-123")
        jarvis, session = _jarvis_with_session([])

        async def _fake_execute(fc):
            return genai_types.FunctionResponse(id=fc.id, name=fc.name, response={"result": "sunny"})

        jarvis._execute_tool = _fake_execute
        await jarvis._handle_tool_batch([fc])

        assert len(session.tool_responses) == 1
        batch = session.tool_responses[0]
        assert len(batch) == 1
        assert batch[0].id == "abc-123"
        assert batch[0].name == "get_weather"
        assert batch[0].response["result"] == "sunny"

    asyncio.run(_run())
    print("test_tool_response_delivered_correctly_with_matching_id_and_name: PASS")


def test_tool_ordering_preserved_within_a_batch() -> None:
    """Multiple function calls in ONE batch must still execute strictly in
    order (never uncontrolled parallel execution), and their responses
    must be sent together, in that same order."""
    async def _run():
        order: list[str] = []
        fcs = [
            _FakeFunctionCall("tool_a", call_id="a"),
            _FakeFunctionCall("tool_b", call_id="b"),
            _FakeFunctionCall("tool_c", call_id="c"),
        ]
        jarvis, session = _jarvis_with_session([])

        async def _fake_execute(fc):
            order.append(fc.id)
            # tool_a is the slowest — if execution were parallel, it would
            # finish LAST despite starting first; sequential execution
            # means it still finishes (and is recorded) first.
            await asyncio.sleep(0.05 if fc.id == "a" else 0.0)
            return genai_types.FunctionResponse(id=fc.id, name=fc.name, response={"result": "ok"})

        jarvis._execute_tool = _fake_execute
        await jarvis._handle_tool_batch(fcs)

        assert order == ["a", "b", "c"], "function calls in a batch must execute strictly in order"
        assert [r.id for r in session.tool_responses[0]] == ["a", "b", "c"]

    asyncio.run(_run())
    print("test_tool_ordering_preserved_within_a_batch: PASS")


def test_calendar_tool_still_works_through_the_async_pathway() -> None:
    """Calendar is regression-sensitive: prove get_calendar_events still
    works correctly when dispatched through _handle_tool_batch() (the new
    pathway) instead of being awaited directly, using the exact same
    mocking approach as tests/test_calendar_tools.py."""
    async def _run():
        jarvis, session = _jarvis_with_session([])
        jarvis._user_profile = {"username": "saroj"}
        jarvis._web_timezone = "Asia/Kathmandu"

        fake_creds = SimpleNamespace(valid=True, expired=False)

        def fake_get_events(credentials, *, time_min, time_max, max_results=25):
            return [{"id": "ev1", "title": "Standup", "start": "2026-08-29T09:00:00+05:45",
                     "end": "2026-08-29T09:30:00+05:45", "location": "", "all_day": False}]

        fc = _FakeFunctionCall(
            "get_calendar_events",
            {"start": "2026-08-29T00:00:00", "end": "2026-08-30T00:00:00"},
            call_id="cal1",
        )

        with patch("main.calendar_store.is_configured", return_value=True), \
             patch.object(JarvisLive, "_get_calendar_credentials", return_value=fake_creds), \
             patch("main.calendar_actions.get_events", side_effect=fake_get_events):
            await jarvis._handle_tool_batch([fc])

        assert len(session.tool_responses) == 1
        result = session.tool_responses[0][0].response["result"]
        assert "Standup" in result

    asyncio.run(_run())
    print("test_calendar_tool_still_works_through_the_async_pathway: PASS")


def test_model_withdrawn_call_is_skipped_and_no_response_sent() -> None:
    """response.tool_call_cancellation for a call still queued (never
    reached by the worker) must be skipped entirely — no execution, no
    send_tool_response for it."""
    async def _run():
        executed = []
        fc = _FakeFunctionCall("get_weather", call_id="withdrawn-1")
        jarvis, session = _jarvis_with_session([])
        jarvis._pending_tool_calls["withdrawn-1"] = {
            "name": "get_weather", "status": "queued", "cancelled": True,
        }

        async def _fake_execute(fc):
            executed.append(fc.id)
            return genai_types.FunctionResponse(id=fc.id, name=fc.name, response={"result": "ok"})

        jarvis._execute_tool = _fake_execute
        await jarvis._handle_tool_batch([fc])

        assert executed == [], "a call withdrawn before it started must never be executed"
        assert session.tool_responses == [], "nothing to respond with once every call in the batch was withdrawn"
        assert "withdrawn-1" not in jarvis._pending_tool_calls, "bookkeeping must be cleaned up either way"

    asyncio.run(_run())
    print("test_model_withdrawn_call_is_skipped_and_no_response_sent: PASS")


if __name__ == "__main__":
    test_receive_loop_continues_while_tool_runs()
    test_tool_response_delivered_correctly_with_matching_id_and_name()
    test_tool_ordering_preserved_within_a_batch()
    test_calendar_tool_still_works_through_the_async_pathway()
    test_model_withdrawn_call_is_skipped_and_no_response_sent()
    print("\nAll tool-call async tests passed.")
