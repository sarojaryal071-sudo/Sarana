"""
tests/test_lifecycle_no_growth.py — regression tests for the reported
"login works, first conversation works, second/third conversation
becomes noticeably slower" progressive-degradation symptom.

Guards against the specific accumulation patterns the diagnostic and this
implementation targeted: unbounded per-chunk audio-out tasks, tool-call
bookkeeping that never gets cleaned up, and repeated client
register/unregister cycles leaking queues or sender tasks.

Run with:
    python -m pytest tests/test_lifecycle_no_growth.py -q
"""
import asyncio

from google.genai import types as genai_types

from core.headless_surface import HeadlessSurface
from dashboard.server import DashboardServer
from main import JarvisLive


class _FakeWS:
    def __init__(self):
        self.received: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.received.append(data)


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


def test_many_broadcast_chunks_never_grow_task_count_beyond_one_per_client() -> None:
    """Root cause regression guard: _play_audio() used to spawn a brand-new
    asyncio.create_task() PER CHUNK. Simulating many turns' worth of audio
    (hundreds of chunks) must still leave exactly one sender task per
    connected client — never one per chunk, never growing over time."""
    async def _run():
        server = DashboardServer()
        ws = _FakeWS()
        server._register_audio_client(ws)

        tasks_before = len(asyncio.all_tasks())

        for i in range(500):   # several "turns" worth of ~200ms audio batches
            await server.broadcast_audio(bytes([i % 256]) * 4)
            if i % 10 == 0:
                await asyncio.sleep(0)   # let the one sender task actually drain, like real turns spaced over time
        await asyncio.sleep(0.1)

        tasks_after = len(asyncio.all_tasks())
        # Exactly this test's own task plus the one dedicated sender task —
        # never one created per chunk.
        assert tasks_after - tasks_before <= 1, (
            f"broadcasting 500 chunks must not grow live task count "
            f"(before={tasks_before}, after={tasks_after})"
        )
        assert len(server._audio_out_senders) == 1
        assert len(ws.received) == 500, "every chunk must still have been delivered, in order"

        await server._unregister_audio_client(ws)

    asyncio.run(_run())
    print("test_many_broadcast_chunks_never_grow_task_count_beyond_one_per_client: PASS")


def test_repeated_client_connect_disconnect_leaves_no_residue() -> None:
    """Many short-lived clients (simulating repeated reconnects over a long
    session) must never accumulate stale queues/sender tasks once each one
    disconnects."""
    async def _run():
        server = DashboardServer()

        for _ in range(50):
            ws = _FakeWS()
            server._register_audio_client(ws)
            await server.broadcast_audio(b"\x00\x01")
            await asyncio.sleep(0.01)
            await server._unregister_audio_client(ws)

        assert server._audio_out_clients == set()
        assert server._audio_out_queues == {}
        assert server._audio_out_senders == {}

    asyncio.run(_run())
    print("test_repeated_client_connect_disconnect_leaves_no_residue: PASS")


def test_repeated_tool_turns_leave_no_pending_tool_call_residue() -> None:
    """Across many simulated "turns", each with its own tool call,
    self._pending_tool_calls must always end up empty — no per-turn
    residue accumulating over a long conversation."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)

        class _FakeSession:
            async def send_tool_response(self, *, function_responses):
                pass

        jarvis.session = _FakeSession()

        async def _fake_execute(fc):
            return genai_types.FunctionResponse(id=fc.id, name=fc.name, response={"result": "ok"})

        jarvis._execute_tool = _fake_execute

        for turn in range(30):
            fc = _FakeFunctionCall("get_weather", call_id=f"turn-{turn}")
            jarvis._pending_tool_calls[fc.id] = {"name": fc.name, "status": "queued", "cancelled": False}
            await jarvis._handle_tool_batch([fc])

        assert jarvis._pending_tool_calls == {}, "tool-call bookkeeping must never accumulate across turns"
        assert jarvis._active_tool_task is None
        assert jarvis._active_tool_call_id is None

    asyncio.run(_run())
    print("test_repeated_tool_turns_leave_no_pending_tool_call_residue: PASS")


if __name__ == "__main__":
    test_many_broadcast_chunks_never_grow_task_count_beyond_one_per_client()
    test_repeated_client_connect_disconnect_leaves_no_residue()
    test_repeated_tool_turns_leave_no_pending_tool_call_residue()
    print("\nAll lifecycle/no-growth tests passed.")
