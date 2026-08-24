"""
tests/test_dashboard_phase3.py — Phase 3 API/WebSocket boundary tests.

Covers:
  - GET /api/session (new)
  - existing /ws "command" round-trip (regression — must still work exactly
    as before)
  - new message types (device_action_result, and a made-up future type)
    don't break the existing connection or command handling
  - role tracking / desktop_connected bookkeeping (Phase 3 scope only —
    no real desktop client exists yet, this only proves the mechanism)
  - headless compatibility: none of the above ever imports PyQt6, even
    though /api/session's tools field is derived via a lazy `from main
    import TOOL_DECLARATIONS`

Run with:
    .venv/Scripts/python.exe -m pytest tests/test_dashboard_phase3.py -v
or simply:
    .venv/Scripts/python.exe tests/test_dashboard_phase3.py

Uses the project's existing dev venv (.venv) to run — it already has
fastapi/uvicorn installed for desktop development. What's actually being
proven is not "which venv has PyQt6 installed" but "does this code path
ever import it" (checked via sys.modules, not package availability) —
the same proof style used to validate Phases 1-2.
"""
import time
import sys

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer, _get_assistant_name


def _server_with_token(token: str) -> DashboardServer:
    server = DashboardServer()
    server._tokens.add(token)
    return server


def test_api_session() -> None:
    server = _server_with_token("test-token-session")
    client = TestClient(server.app)

    resp = client.get("/api/session")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "assistant_name" in body
    assert "tools" in body
    assert "desktop_connected" in body

    # Compare against the same helper the route uses, not a hardcoded
    # literal — the live config's assistant_name is real user data
    # (currently "Radhe" on this machine), not something this test should
    # assume or depend on.
    assert body["assistant_name"] == _get_assistant_name(), body["assistant_name"]
    assert isinstance(body["tools"], list) and len(body["tools"]) > 0, "tools should be non-empty"
    assert all("name" in t and "description" in t for t in body["tools"]), body["tools"][:1]
    assert body["desktop_connected"] is False   # no desktop client connected in this test

    print(f"test_api_session: PASS — assistant_name={body['assistant_name']!r}, "
          f"{len(body['tools'])} tools, desktop_connected={body['desktop_connected']}")


def test_ws_command_round_trip() -> None:
    """Existing behavior, unchanged: a plaintext 'command' message reaches
    the command queue exactly as it did before this phase."""
    token  = "test-token-cmd"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"type": "command", "text": "hello sarana"})
        time.sleep(0.05)

    assert not server._command_queue.empty(), "command should have been queued"
    queued = server._command_queue.get_nowait()
    assert queued == "hello sarana", queued
    print("test_ws_command_round_trip: PASS")


def test_ws_new_message_types_do_not_break_connection() -> None:
    """device_action_result and a made-up, not-yet-invented future type
    must not crash /ws — and a 'command' sent afterward on the SAME
    connection must still work, proving the existing contract survives
    the new message-type infrastructure being present."""
    token  = "test-token-newtypes"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws?token={token}&role=desktop") as ws:
        ws.send_json({
            "type": "device_action_result",
            "action": "computer_settings",
            "success": True,
            "result": "ok",
        })
        ws.send_json({"type": "totally_made_up_future_type", "whatever": 123})
        ws.send_json({"type": "command", "text": "still alive"})
        time.sleep(0.05)

    assert not server._command_queue.empty(), "command after unknown types should still be queued"
    queued = server._command_queue.get_nowait()
    assert queued == "still alive", queued
    print("test_ws_new_message_types_do_not_break_connection: PASS")


def test_ws_role_tracking_and_desktop_connected() -> None:
    """Phase 3 bookkeeping only — proves the mechanism /api/session's
    desktop_connected field relies on, not that a real desktop client
    exists (it doesn't; that's Phase 6)."""
    token  = "test-token-role"
    server = _server_with_token(token)
    client = TestClient(server.app)

    assert server.has_desktop_connected() is False

    with client.websocket_connect(f"/ws?token={token}&role=desktop") as ws:
        time.sleep(0.05)
        assert server.has_desktop_connected() is True, server._client_roles

    time.sleep(0.05)
    assert server.has_desktop_connected() is False, "role entry should be cleaned up on disconnect"
    print("test_ws_role_tracking_and_desktop_connected: PASS")


def test_headless_compatibility_no_pyqt6() -> None:
    """Must run after the tests above (or independently) — proves that
    exercising /api/session (whose 'tools' field lazily does
    `from main import TOOL_DECLARATIONS`) and the /ws message-type
    dispatch never imports PyQt6, so this API layer is usable by the
    headless architecture from Phases 1-2 with no desktop UI involved."""
    # Exercise /api/session again here too, so this test is meaningful
    # even if run in isolation (e.g. `pytest -k headless`).
    server = _server_with_token("test-token-headless")
    client = TestClient(server.app)
    resp = client.get("/api/session")
    assert resp.status_code == 200

    leaked = [m for m in sys.modules if m == "PyQt6" or m.startswith("PyQt6.")]
    assert not leaked, f"PyQt6 modules leaked into sys.modules: {leaked}"
    print("test_headless_compatibility_no_pyqt6: PASS — sys.modules has no PyQt6 entries")


if __name__ == "__main__":
    test_api_session()
    test_ws_command_round_trip()
    test_ws_new_message_types_do_not_break_connection()
    test_ws_role_tracking_and_desktop_connected()
    test_headless_compatibility_no_pyqt6()
    print("\nAll Phase 3 tests passed.")
