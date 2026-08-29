"""
tests/test_dashboard_phase4.py — Phase 4 remote-audio tests.

Two testing strategies are used deliberately:

  - broadcast_audio()'s fan-out/isolation LOGIC (points 2-5 below) is
    tested with lightweight fake WebSocket-like objects, not real network
    connections. This is the actual thing worth proving — that fan-out
    delivers unchanged bytes to every client and that one client's failure
    never affects the others or escapes the method. Driving that through
    real WebSocket connections would only be re-testing Starlette's own
    (already well-tested) transport, while adding real cross-thread
    event-loop complexity for no extra confidence.

  - /ws/audio-out, /ws/phone-audio, and /ws route ACCEPTANCE and lifecycle
    (points 1, 6, 7) are tested with FastAPI's real TestClient, exactly
    like tests/test_dashboard_phase3.py, since those specifically need to
    prove the real ASGI routes behave correctly.

Run with:
    .venv/Scripts/python.exe -m tests.test_dashboard_phase4
"""
import asyncio
import sys
import time

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer


def _server_with_token(token: str) -> DashboardServer:
    server = DashboardServer()
    server._tokens.add(token)
    return server


# ── fake client for broadcast_audio() unit tests ────────────────────────────

class _FakeWS:
    """Minimal WebSocket stand-in: only what broadcast_audio() touches."""

    def __init__(self, fail: bool = False):
        self.received: list[bytes] = []
        self.fail = fail

    async def send_bytes(self, data: bytes) -> None:
        if self.fail:
            raise RuntimeError("simulated send failure")
        self.received.append(data)


def test_broadcast_audio_fanout_and_isolation() -> None:
    """Points 2-5: unchanged delivery, multi-client fan-out, one bad client
    isolated from the others, broadcast_audio() itself never raises.

    Audio-out backpressure fix: delivery now happens on each client's own
    dedicated sender task (see dashboard/server.py's _register_audio_client()/
    _audio_sender_loop()) rather than synchronously inside broadcast_audio()
    itself, so this test registers clients the same way the real
    /ws/audio-out route does and gives the background sender tasks a brief
    moment to actually run before asserting delivery."""

    async def _run():
        server = DashboardServer()
        good1, good2, bad = _FakeWS(), _FakeWS(), _FakeWS(fail=True)
        for ws in (good1, good2, bad):
            server._register_audio_client(ws)

        chunk = bytes(range(256)) * 4   # stand-in PCM16 payload, 1024 bytes

        await server.broadcast_audio(chunk)   # must not raise despite `bad`
        await asyncio.sleep(0.05)             # let the sender tasks actually run

        assert good1.received == [chunk], "chunk must reach client 1 unchanged"
        assert good2.received == [chunk], "chunk must reach client 2 unchanged"
        assert bad not in server._audio_out_clients, "failed client must be pruned"
        assert good1 in server._audio_out_clients
        assert good2 in server._audio_out_clients

        # A second broadcast after pruning: still fine, still isolated.
        chunk2 = b"\x01\x02" * 50
        await server.broadcast_audio(chunk2)
        await asyncio.sleep(0.05)
        assert good1.received == [chunk, chunk2]
        assert good2.received == [chunk, chunk2]

    asyncio.run(_run())
    print("test_broadcast_audio_fanout_and_isolation: PASS")


def test_broadcast_audio_with_no_clients_is_a_safe_noop() -> None:
    async def _run():
        server = DashboardServer()
        await server.broadcast_audio(b"\x00\x00\x00\x00")   # must not raise
    asyncio.run(_run())
    print("test_broadcast_audio_with_no_clients_is_a_safe_noop: PASS")


# ── real WebSocket route tests ───────────────────────────────────────────────

def test_audio_out_endpoint_accepts_connection() -> None:
    """Point 1: /ws/audio-out accepts a connection and its receive-only
    loop tolerates unexpected client-sent bytes without erroring."""
    token  = "test-token-audioout"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws/audio-out?token={token}") as ws:
        ws.send_bytes(b"\x00\x00")   # unusual on an output-only channel, must not break the server
        time.sleep(0.02)
    print("test_audio_out_endpoint_accepts_connection: PASS")


def test_audio_out_rejects_bad_token() -> None:
    server = _server_with_token("real-token")
    client = TestClient(server.app)
    try:
        with client.websocket_connect("/ws/audio-out?token=WRONG"):
            pass
        raised = False
    except Exception:
        raised = True
    assert raised, "an invalid token should close the connection, not accept it"
    print("test_audio_out_rejects_bad_token: PASS")


def test_phone_audio_still_works() -> None:
    """Point 6: existing /ws/phone-audio behavior is unchanged — bytes sent
    by a phone client still land in _phone_audio_queue."""
    token  = "test-token-phoneaudio"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws/phone-audio?token={token}") as ws:
        ws.send_bytes(b"\x11\x22\x33\x44")
        time.sleep(0.05)

    assert not server._phone_audio_queue.empty(), "phone audio chunk should have been queued"
    item = server._phone_audio_queue.get_nowait()
    assert item["data"] == b"\x11\x22\x33\x44"
    assert item["mime_type"] == "audio/pcm"
    print("test_phone_audio_still_works: PASS")


def test_ws_command_still_works() -> None:
    """Point 7: existing /ws command flow is unchanged (Phase 3 already
    covers this in depth; re-verified here as part of Phase 4's own
    regression check, per your validation checklist)."""
    token  = "test-token-cmd-p4"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"type": "command", "text": "phase 4 regression check"})
        time.sleep(0.05)

    assert not server._command_queue.empty()
    assert server._command_queue.get_nowait() == "phase 4 regression check"
    print("test_ws_command_still_works: PASS")


def test_headless_still_no_pyqt6() -> None:
    """Point 8: main.py still imports and constructs JarvisLive with
    HeadlessSurface, and the new broadcast_audio()/audio-out wiring adds
    no PyQt6 dependency anywhere in that path."""
    from main import JarvisLive
    from core.headless_surface import HeadlessSurface

    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis is not None

    leaked = [m for m in sys.modules if m == "PyQt6" or m.startswith("PyQt6.")]
    assert not leaked, f"PyQt6 modules leaked into sys.modules: {leaked}"
    print("test_headless_still_no_pyqt6: PASS — sys.modules has no PyQt6 entries")


if __name__ == "__main__":
    test_broadcast_audio_fanout_and_isolation()
    test_broadcast_audio_with_no_clients_is_a_safe_noop()
    test_audio_out_endpoint_accepts_connection()
    test_audio_out_rejects_bad_token()
    test_phone_audio_still_works()
    test_ws_command_still_works()
    test_headless_still_no_pyqt6()
    print("\nAll Phase 4 tests passed.")
