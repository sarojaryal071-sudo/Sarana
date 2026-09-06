"""
tests/test_dashboard_local_sink.py -- DashboardServer's optional in-process
local sink (set_local_sink()/self._local_sink), the one new plumbing piece
behind desktop consuming the SAME Presentation Engine/semantic-event
messages a browser already receives over /ws. Wired through the existing
single choke point (_send_to_clients()) -- these tests prove every
broadcast_*() method still reaches it, that it never breaks real WS
client delivery, and that its default (None) is a complete no-op.

Run with:
    .venv/Scripts/python.exe -m tests.test_dashboard_local_sink
"""
import asyncio

from dashboard.server import DashboardServer


def test_default_local_sink_is_none() -> None:
    server = DashboardServer()
    assert server._local_sink is None
    print("test_default_local_sink_is_none: PASS")


def test_set_local_sink_wires_it() -> None:
    server = DashboardServer()
    received = []
    server.set_local_sink(received.append)
    assert server._local_sink is not None
    server._local_sink({"type": "sys", "text": "wired"})
    assert received == [{"type": "sys", "text": "wired"}]
    print("test_set_local_sink_wires_it: PASS")


def test_broadcast_reaches_the_local_sink() -> None:
    async def _run():
        server = DashboardServer()
        received = []
        server.set_local_sink(received.append)
        await server.broadcast({"type": "content", "title": "WEATHER", "text": "5.2C"})
        assert received == [{"type": "content", "title": "WEATHER", "text": "5.2C"}]
    asyncio.run(_run())
    print("test_broadcast_reaches_the_local_sink: PASS")


def test_broadcast_state_reaches_the_local_sink_too() -> None:
    """broadcast_state() bypasses broadcast()/self._history entirely (see
    its own docstring) but still funnels through _send_to_clients() —
    confirming the local sink is wired at the one point that's actually
    universal, not re-added per broadcast_*() method."""
    async def _run():
        server = DashboardServer()
        received = []
        server.set_local_sink(received.append)
        await server.broadcast_state("LISTENING")
        assert received == [{"type": "status", "state": "LISTENING"}]
    asyncio.run(_run())
    print("test_broadcast_state_reaches_the_local_sink_too: PASS")


def test_local_sink_receives_content_with_presentation_payload_unmodified() -> None:
    """The actual point of this whole mechanism: a structured presentation
    payload (weather/calendar/etc.) reaches the sink in the exact same
    shape a real WS client gets it in — no re-encoding, no lossy subset."""
    async def _run():
        server = DashboardServer()
        received = []
        server.set_local_sink(received.append)
        await server.broadcast_content("WEATHER", "5.2C", {"type": "weather", "data": {"current": {}}})
        assert len(received) == 1
        assert received[0]["presentation"] == {"type": "weather", "data": {"current": {}}}
    asyncio.run(_run())
    print("test_local_sink_receives_content_with_presentation_payload_unmodified: PASS")


def test_broadcast_audio_cue_reaches_both_a_real_ws_client_and_the_local_sink() -> None:
    """Shared semantic audio event origin: broadcast_audio_cue() (called
    from main.py's _execute_tool() choke point) reaches a real WS client
    AND the local sink through the exact same _send_to_clients() choke
    point every other broadcast_*() method already uses — one origin, two
    consumers, see main.py's _emit_audio_cue_for_result()."""
    async def _run():
        server = DashboardServer()
        received_locally = []
        server.set_local_sink(received_locally.append)

        class _FakeWs:
            def __init__(self):
                self.received = []
            async def send_json(self, msg):
                self.received.append(msg)

        fake_ws = _FakeWs()
        server._clients.add(fake_ws)
        await server.broadcast_audio_cue("blocked")
        assert received_locally == [{"type": "audio_cue", "event": "blocked"}]
        assert fake_ws.received == [{"type": "audio_cue", "event": "blocked"}]
    asyncio.run(_run())
    print("test_broadcast_audio_cue_reaches_both_a_real_ws_client_and_the_local_sink: PASS")


def test_broadcast_audio_cue_is_not_replayed_to_a_later_client() -> None:
    """Same ephemeral, non-history treatment as broadcast_state() — never
    routed through self._history."""
    async def _run():
        server = DashboardServer()
        await server.broadcast_audio_cue("error")
        assert all(m.get("type") != "audio_cue" for m in server._history)
    asyncio.run(_run())
    print("test_broadcast_audio_cue_is_not_replayed_to_a_later_client: PASS")


def test_local_sink_exception_never_breaks_real_client_delivery() -> None:
    """A desktop-side bug (or the embedded view not being ready yet) must
    never take down delivery to a real, connected browser/phone client."""
    async def _run():
        server = DashboardServer()

        def broken_sink(msg):
            raise RuntimeError("desktop bridge exploded")

        server.set_local_sink(broken_sink)

        class _FakeWs:
            def __init__(self):
                self.received = []
            async def send_json(self, msg):
                self.received.append(msg)

        fake_ws = _FakeWs()
        server._clients.add(fake_ws)
        await server.broadcast({"type": "sys", "text": "hello"})
        assert fake_ws.received == [{"type": "sys", "text": "hello"}]
    asyncio.run(_run())
    print("test_local_sink_exception_never_breaks_real_client_delivery: PASS")


def test_no_local_sink_is_a_complete_no_op_default_behavior_unchanged() -> None:
    """The default (None) — every existing deployment (server_main.py on
    Render, or desktop before this wiring exists) behaves exactly as
    before: only real WS clients ever receive anything."""
    async def _run():
        server = DashboardServer()
        assert server._local_sink is None

        class _FakeWs:
            def __init__(self):
                self.received = []
            async def send_json(self, msg):
                self.received.append(msg)

        fake_ws = _FakeWs()
        server._clients.add(fake_ws)
        await server.broadcast({"type": "log", "speaker": "jarvis", "text": "hi", "ts": None})
        assert fake_ws.received == [{"type": "log", "speaker": "jarvis", "text": "hi", "ts": None}]
    asyncio.run(_run())
    print("test_no_local_sink_is_a_complete_no_op_default_behavior_unchanged: PASS")


if __name__ == "__main__":
    test_default_local_sink_is_none()
    test_set_local_sink_wires_it()
    test_broadcast_reaches_the_local_sink()
    test_broadcast_state_reaches_the_local_sink_too()
    test_local_sink_receives_content_with_presentation_payload_unmodified()
    test_broadcast_audio_cue_reaches_both_a_real_ws_client_and_the_local_sink()
    test_broadcast_audio_cue_is_not_replayed_to_a_later_client()
    test_local_sink_exception_never_breaks_real_client_delivery()
    test_no_local_sink_is_a_complete_no_op_default_behavior_unchanged()
    print("\nAll dashboard-local-sink tests passed.")
