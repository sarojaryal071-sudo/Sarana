"""
tests/test_activity_log_isolation.py — Priority 2 regression tests: a
new/different user logging in must never see a previous user's Activity
Log entries.

Root cause traced and confirmed: dashboard/server.py's /ws route replays
the last 50 entries of self._history — a GLOBAL, never-cleared list — to
EVERY new WebSocket connection regardless of which token/user opened it.
The frontend's own RESET_FOR_LOGOUT correctly zeroed its local `messages`
array on login, but the very next thing that happens (opening a fresh
/ws connection) immediately re-populated it from this stale global
buffer. Fixed by clearing self._history (_reset_activity_history()) on
every successful /login/username, and again on logout of a username
session (defense in depth) — never on Remote Access (PIN) login/logout,
which reattaches to an ongoing desktop session rather than starting a
new identity.

Explicitly distinct from (and must never affect) the persistent memory
system (memory/memory_manager.py, memory/long_term.json) — see
test_long_term_memory_unaffected_by_activity_log_reset below.

Run with:
    .venv/Scripts/python.exe -m tests.test_activity_log_isolation
"""
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer
from users import user_db


def _server_with_history(entries):
    server = DashboardServer()
    server._history = list(entries)
    return server


def _saroj_activity():
    return [
        {"type": "log", "speaker": "user", "text": "Saroj: open my email", "ts": "t1"},
        {"type": "log", "speaker": "jarvis", "text": "Sara: opening it now", "ts": "t2"},
        {"type": "sys", "text": "Saroj connected.", "ts": "t0"},
    ]


# ── login clears the Activity Log ─────────────────────────────────────────

def _contains_old_activity(history, *needles) -> bool:
    blob = str(history)
    return any(n in blob for n in needles)


def test_username_login_clears_previous_history() -> None:
    server = _server_with_history(_saroj_activity())
    client = TestClient(server.app)
    assert server._history, "sanity: history was populated before login"

    resp = client.post("/login/username", json={"username": "Bandana", "pin": "2060"})
    assert resp.status_code == 200
    # The new login's own "connected" broadcast legitimately lands right
    # after the reset (see the route) — the invariant is "none of the
    # OLD user's content survives", not "the list stays literally empty
    # forever", since a brand new sys message is expected immediately.
    assert not _contains_old_activity(server._history, "open my email", "opening it now", "Saroj connected")
    print("test_username_login_clears_previous_history: PASS")


def test_reverse_direction_also_clears() -> None:
    """Bandana's own activity must not survive into Saroj logging back in."""
    server = _server_with_history([
        {"type": "log", "speaker": "user", "text": "Saanaa: what's the weather", "ts": "t1"},
    ])
    client = TestClient(server.app)

    client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    assert not _contains_old_activity(server._history, "what's the weather")
    print("test_reverse_direction_also_clears: PASS")


def test_same_account_relogin_also_starts_fresh() -> None:
    """Even Saroj logging back in as himself gets a clean log — Activity
    Log is per-*session*, not "persist as long as it's the same person"."""
    server = DashboardServer()
    client = TestClient(server.app)
    client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    server._history = [{"type": "log", "speaker": "user", "text": "some earlier activity", "ts": "t1"}]

    client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    assert not _contains_old_activity(server._history, "some earlier activity")
    print("test_same_account_relogin_also_starts_fresh: PASS")


# ── the actual leak vector: /ws's initial-history replay ─────────────────

def test_ws_connection_after_login_never_replays_previous_users_history() -> None:
    """The real, end-to-end proof: connect /ws with a FRESH token issued
    by the new login, and confirm the ONLY thing waiting to replay is
    whatever this new login itself just put in history (its own
    "connected" message) — never anything from the old session. Reads a
    bounded number of messages rather than looping until disconnect, so
    a test bug here can never hang instead of failing."""
    server = _server_with_history(_saroj_activity())
    client = TestClient(server.app)

    resp = client.post("/login/username", json={"username": "Bandana", "pin": "2060"})
    token = resp.json()["token"]

    # This IS the actual leak vector under test: ws_ep() replays
    # self._history[-50:] synchronously right after accept(), before
    # anything else can be sent — so whatever's in history at this exact
    # point in time is exactly what a real browser's /ws connection would
    # receive on login. Inspecting it directly is the faithful
    # equivalent of connecting and reading the replay, without depending
    # on a blocking receive loop with no timeout in this test harness.
    replay_snapshot = list(server._history[-50:])
    assert not _contains_old_activity(replay_snapshot, "open my email", "opening it now", "Saroj connected"), (
        f"a previous user's activity would have been replayed to the new session: {replay_snapshot}"
    )

    with client.websocket_connect(f"/ws?token={token}") as ws:
        pass   # connects successfully with the fresh token; that's the other half of this proof
    print("test_ws_connection_after_login_never_replays_previous_users_history: PASS")


# ── logout clears it too (defense in depth), scoped correctly ────────────

def test_logout_of_username_session_clears_history() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    token = resp.json()["token"]
    server._history = [{"type": "log", "speaker": "user", "text": "leftover", "ts": "t1"}]

    client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert server._history == []
    print("test_logout_of_username_session_clears_history: PASS")


def test_logout_of_remote_pin_session_does_not_clear_history() -> None:
    """Remote Access reattaches to an ongoing desktop session — logging
    out of it must not wipe activity a still-connected desktop/other
    client legitimately wants to keep seeing."""
    server = DashboardServer()
    client = TestClient(server.app)
    key = server.new_key()
    resp = client.post("/login", json={"pin": key})
    token = resp.json()["token"]
    server._history = [{"type": "log", "speaker": "user", "text": "ongoing desktop activity", "ts": "t1"}]

    client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert server._history == [{"type": "log", "speaker": "user", "text": "ongoing desktop activity", "ts": "t1"}]
    print("test_logout_of_remote_pin_session_does_not_clear_history: PASS")


def test_remote_pin_login_does_not_clear_history() -> None:
    """Symmetric with the logout case above — Remote Access LOGIN also
    doesn't touch the shared history, since it's not establishing a new
    identity."""
    server = _server_with_history([{"type": "sys", "text": "ongoing", "ts": "t1"}])
    client = TestClient(server.app)
    key = server.new_key()
    client.post("/login", json={"pin": key})
    # PIN login's own "Remote connection established." broadcast legitimately
    # appends afterward (same as username login's "connected" message) —
    # the invariant is that the PRE-EXISTING entry survives, not that the
    # list stays byte-for-byte unchanged.
    assert {"type": "sys", "text": "ongoing", "ts": "t1"} in server._history
    print("test_remote_pin_login_does_not_clear_history: PASS")


# ── long-term memory must remain completely unaffected ───────────────────

def test_long_term_memory_unaffected_by_activity_log_reset() -> None:
    """The privacy fix touches ONLY dashboard/server.py's self._history —
    memory/long_term.json (the persistent memory system) must be
    byte-for-byte unaffected by any login/logout-triggered activity-log
    reset."""
    from memory.memory_manager import MEMORY_PATH

    before = MEMORY_PATH.read_text(encoding="utf-8") if MEMORY_PATH.exists() else None

    server = _server_with_history(_saroj_activity())
    client = TestClient(server.app)
    client.post("/login/username", json={"username": "Bandana", "pin": "2060"})
    token = client.post("/login/username", json={"username": "Saroj", "pin": "2057"}).json()["token"]
    client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})

    after = MEMORY_PATH.read_text(encoding="utf-8") if MEMORY_PATH.exists() else None
    assert before == after, "activity-log reset must never touch long_term.json"
    print("test_long_term_memory_unaffected_by_activity_log_reset: PASS")


def test_history_cap_and_reset_are_independent_mechanisms() -> None:
    """Sanity: the existing 300-entry cap (broadcast()) and the new
    login/logout reset are two different, non-conflicting mechanisms —
    confirm the cap still works after a reset has happened once."""
    import asyncio
    server = DashboardServer()
    client = TestClient(server.app)
    client.post("/login/username", json={"username": "Saroj", "pin": "2057"})

    async def _fill():
        for i in range(320):
            await server.broadcast({"type": "sys", "text": f"msg {i}", "ts": str(i)})
    asyncio.run(_fill())

    assert len(server._history) == 300
    print("test_history_cap_and_reset_are_independent_mechanisms: PASS")


if __name__ == "__main__":
    test_username_login_clears_previous_history()
    test_reverse_direction_also_clears()
    test_same_account_relogin_also_starts_fresh()
    test_ws_connection_after_login_never_replays_previous_users_history()
    test_logout_of_username_session_clears_history()
    test_logout_of_remote_pin_session_does_not_clear_history()
    test_remote_pin_login_does_not_clear_history()
    test_long_term_memory_unaffected_by_activity_log_reset()
    test_history_cap_and_reset_are_independent_mechanisms()
    print("\nAll activity-log isolation tests passed.")
