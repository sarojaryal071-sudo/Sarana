"""
tests/test_audio_out_backpressure.py — regression tests for the audio-out
backpressure fix (per-client bounded queue + one dedicated sender task,
see dashboard/server.py's _register_audio_client()/_audio_sender_loop()/
broadcast_audio()).

Root cause being guarded against: _play_audio() used to fan audio out via
a brand-new, unawaited asyncio.create_task(broadcast_audio(chunk)) per
~200ms batch — no queue, no cap, no serialization. A client whose downlink
was ever slower than real-time audio accumulated concurrent, unordered,
unbounded sends, and got progressively worse turn over turn. These tests
prove the replacement: ordered delivery, a slow client isolated from
everyone else, bounded queue growth (never unbounded), disconnect cleanup,
multi-client fan-out, and — critically — that a given client is never sent
to concurrently.

Run with:
    python -m pytest tests/test_audio_out_backpressure.py -q
"""
import asyncio

from dashboard.server import DashboardServer


class _FakeWS:
    """Minimal WebSocket stand-in with an optional artificial send delay,
    and a re-entrancy guard that fails the test if two sends to the same
    fake client are ever in flight at once."""

    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.received: list[bytes] = []
        self.delay = delay
        self.fail = fail
        self._busy = False
        self.concurrent_send_detected = False

    async def send_bytes(self, data: bytes) -> None:
        if self._busy:
            # A second send arrived while the first was still in flight —
            # exactly what "one dedicated sender task per client" must
            # never allow.
            self.concurrent_send_detected = True
        self._busy = True
        try:
            if self.fail:
                raise RuntimeError("simulated send failure")
            if self.delay:
                await asyncio.sleep(self.delay)
            self.received.append(data)
        finally:
            self._busy = False


def test_ordered_delivery() -> None:
    """Chunks broadcast in sequence must arrive at a client in the same
    order they were sent."""
    async def _run():
        server = DashboardServer()
        ws = _FakeWS()
        server._register_audio_client(ws)

        chunks = [bytes([i]) * 4 for i in range(20)]
        for c in chunks:
            await server.broadcast_audio(c)
        await asyncio.sleep(0.05)

        assert ws.received == chunks, "chunks must be delivered in the order they were sent"
        assert not ws.concurrent_send_detected

    asyncio.run(_run())
    print("test_ordered_delivery: PASS")


def test_slow_client_does_not_block_other_clients() -> None:
    """A client whose send() is slow (simulating a poor mobile downlink)
    must never delay or affect delivery to any other, healthy client —
    this is the exact failure mode the old unbounded-task design allowed."""
    async def _run():
        server = DashboardServer()
        slow = _FakeWS(delay=1.0)      # much slower than real-time audio
        fast = _FakeWS()
        server._register_audio_client(slow)
        server._register_audio_client(fast)

        chunk = b"\x01\x02" * 100
        await server.broadcast_audio(chunk)

        # The fast client must receive its chunk almost immediately,
        # regardless of the slow client still being mid-send.
        for _ in range(20):
            if fast.received:
                break
            await asyncio.sleep(0.01)
        assert fast.received == [chunk], "a slow client must never delay delivery to a fast one"
        assert slow.received == [], "the slow client's own send is still in flight at this point"

    asyncio.run(_run())
    print("test_slow_client_does_not_block_other_clients: PASS")


def test_queue_saturation_is_bounded_and_drops_with_backpressure() -> None:
    """A client that never actually drains (send() blocks forever) must
    have its OWN queue growth bounded — never unbounded memory/task growth
    — and broadcast_audio() must keep returning immediately (never block
    the caller) once that queue is full."""
    async def _run():
        server = DashboardServer()
        stuck = _FakeWS(delay=999)   # effectively never completes a send
        server._register_audio_client(stuck)

        maxsize = server._AUDIO_OUT_QUEUE_MAXSIZE
        before_drop_count = server._audio_out_dropped

        # Send far more chunks than the queue can hold. Every call must
        # return immediately (put_nowait — never awaits network I/O).
        for i in range(maxsize * 5):
            await asyncio.wait_for(server.broadcast_audio(bytes([i % 256])), timeout=0.5)

        queue = server._audio_out_queues[stuck]
        assert queue.qsize() <= maxsize, "a stalled client's queue must never exceed its bound"
        assert server._audio_out_dropped > before_drop_count, (
            "excess chunks for a saturated queue must be dropped (counted), not silently buffered forever"
        )

    asyncio.run(_run())
    print("test_queue_saturation_is_bounded_and_drops_with_backpressure: PASS")


def test_disconnect_cleanup_removes_queue_and_cancels_sender_task() -> None:
    """Unregistering a client (the real /ws/audio-out route's finally block)
    must remove its queue/task bookkeeping and actually cancel its sender
    task — no lingering resources per disconnected client."""
    async def _run():
        server = DashboardServer()
        ws = _FakeWS()
        server._register_audio_client(ws)
        sender_task = server._audio_out_senders[ws]

        await server._unregister_audio_client(ws)

        assert ws not in server._audio_out_clients
        assert ws not in server._audio_out_queues
        assert ws not in server._audio_out_senders
        assert sender_task.done(), "the client's dedicated sender task must actually be cancelled/finished"

        # A broadcast after disconnect must be a safe no-op for this client.
        await server.broadcast_audio(b"\x00\x00")

    asyncio.run(_run())
    print("test_disconnect_cleanup_removes_queue_and_cancels_sender_task: PASS")


def test_failed_send_self_prunes_client() -> None:
    """A client whose send_bytes() raises must be pruned automatically
    (by its own sender task), matching the old per-broadcast isolation
    guarantee, now enforced per-client."""
    async def _run():
        server = DashboardServer()
        bad = _FakeWS(fail=True)
        server._register_audio_client(bad)

        await server.broadcast_audio(b"\x01\x02\x03\x04")
        await asyncio.sleep(0.05)

        assert bad not in server._audio_out_clients
        assert bad not in server._audio_out_queues
        assert bad not in server._audio_out_senders

    asyncio.run(_run())
    print("test_failed_send_self_prunes_client: PASS")


def test_multiple_clients_each_get_independent_ordered_delivery() -> None:
    """Fan-out to several simultaneously-connected clients: each one gets
    every chunk, in order, independent of the others."""
    async def _run():
        server = DashboardServer()
        clients = [_FakeWS() for _ in range(5)]
        for ws in clients:
            server._register_audio_client(ws)

        chunks = [bytes([i]) * 8 for i in range(10)]
        for c in chunks:
            await server.broadcast_audio(c)
        await asyncio.sleep(0.05)

        for ws in clients:
            assert ws.received == chunks
            assert not ws.concurrent_send_detected

    asyncio.run(_run())
    print("test_multiple_clients_each_get_independent_ordered_delivery: PASS")


def test_no_overlapping_sends_to_one_client_under_rapid_broadcast() -> None:
    """Broadcasting many chunks back-to-back (without awaiting delivery in
    between, exactly like _play_audio()'s real loop does) must never result
    in two concurrent ws.send_bytes() calls for the same client — the
    dedicated per-client sender task must serialize them regardless of how
    fast chunks are produced upstream."""
    async def _run():
        server = DashboardServer()
        ws = _FakeWS(delay=0.01)   # slow enough that overlap would be easy to hit if unserialized
        server._register_audio_client(ws)

        for i in range(15):
            await server.broadcast_audio(bytes([i]))
        await asyncio.sleep(0.3)

        assert not ws.concurrent_send_detected, "sends to one client must never overlap"
        assert len(ws.received) == 15

    asyncio.run(_run())
    print("test_no_overlapping_sends_to_one_client_under_rapid_broadcast: PASS")


if __name__ == "__main__":
    test_ordered_delivery()
    test_slow_client_does_not_block_other_clients()
    test_queue_saturation_is_bounded_and_drops_with_backpressure()
    test_disconnect_cleanup_removes_queue_and_cancels_sender_task()
    test_failed_send_self_prunes_client()
    test_multiple_clients_each_get_independent_ordered_delivery()
    test_no_overlapping_sends_to_one_client_under_rapid_broadcast()
    print("\nAll audio-out backpressure tests passed.")
