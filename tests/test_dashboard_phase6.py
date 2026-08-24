"""
tests/test_dashboard_phase6.py — Phase 6 web-frontend integration tests.

Covers what Phase 6 actually added to the backend (CORS + startup PIN
print) plus a regression check that everything the new frontend depends on
(GET /api/session, /ws command round-trip, /ws/audio-out) still behaves
exactly as Phase 3/4 already proved — the frontend is a new CONSUMER of
that contract, not a reason to re-litigate it.

Run with:
    .venv/Scripts/python.exe -m tests.test_dashboard_phase6
"""
import os
import sys
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer, _cors_allowed_origins


def _server_with_token(token: str) -> DashboardServer:
    server = DashboardServer()
    server._tokens.add(token)
    return server


def test_cors_allows_configured_dev_origin() -> None:
    """A preflight/actual request from the Vite dev origin gets an
    Access-Control-Allow-Origin header back — this is the one new thing
    the browser-based frontend actually needs that dashboard/static/app.html
    never did (same-origin)."""
    server = _server_with_token("test-token-cors")
    client = TestClient(server.app)

    resp = client.get("/api/session", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173", resp.headers
    print("test_cors_allows_configured_dev_origin: PASS")


def test_cors_rejects_unlisted_origin() -> None:
    """An origin NOT in the allowlist gets no CORS header — proves this
    isn't a bare wildcard opening the backend to arbitrary origins."""
    server = _server_with_token("test-token-cors2")
    client = TestClient(server.app)

    resp = client.get("/api/session", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 200   # request itself still succeeds (no Origin check server-side)
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers.keys()}
    print("test_cors_rejects_unlisted_origin: PASS")


def test_cors_origin_list_has_no_wildcard() -> None:
    origins = _cors_allowed_origins()
    assert "*" not in origins, origins
    assert any("5173" in o for o in origins), origins
    print(f"test_cors_origin_list_has_no_wildcard: PASS — {origins}")


# ── Production CORS fix (live bug: sarana-psi.vercel.app got no
# Access-Control-Allow-Origin header at all) ─────────────────────────────

def test_cors_allows_confirmed_production_origin_by_default() -> None:
    """The live, confirmed Vercel frontend must work out of the box, not
    only if Render's env var happens to be set/spelled correctly."""
    server = _server_with_token("test-token-cors-prod")
    client = TestClient(server.app)
    resp = client.get("/api/session", headers={"Origin": "https://sarana-psi.vercel.app"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://sarana-psi.vercel.app", resp.headers
    print("test_cors_allows_confirmed_production_origin_by_default: PASS")


def test_cors_accepts_singular_env_var_alias() -> None:
    """SARANA_ALLOWED_ORIGIN (singular) must work exactly like the
    originally-documented SARANA_ALLOWED_ORIGINS (plural) — removes the
    exact-name-guessing risk as a way for this to silently misconfigure."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SARANA_ALLOWED_ORIGIN")}
    env["SARANA_ALLOWED_ORIGIN"] = "https://singular-alias-test.example.com"
    with patch.dict(os.environ, env, clear=True):
        origins = _cors_allowed_origins()
    assert "https://singular-alias-test.example.com" in origins, origins
    print("test_cors_accepts_singular_env_var_alias: PASS")


def test_cors_normalizes_trailing_slash() -> None:
    """A configured origin with a trailing slash must still match — CORS
    origin comparison is an exact string match, so this is a real, common
    way to configure it "correctly" and have it silently never match."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SARANA_ALLOWED_ORIGIN")}
    env["SARANA_ALLOWED_ORIGINS"] = "https://trailing-slash-test.example.com/"
    with patch.dict(os.environ, env, clear=True):
        origins = _cors_allowed_origins()
    assert "https://trailing-slash-test.example.com" in origins, origins
    assert "https://trailing-slash-test.example.com/" not in origins, origins
    print("test_cors_normalizes_trailing_slash: PASS")


def test_cors_headers_present_on_error_response() -> None:
    """CORSMiddleware wraps the whole response cycle, including error
    responses — /api/command without auth (401) from an allowed origin
    must still carry the CORS header, not just the 200 success path."""
    server = _server_with_token("test-token-cors-err")
    client = TestClient(server.app)
    resp = client.post(
        "/api/command",
        json={"text": "hello"},
        headers={"Origin": "https://sarana-psi.vercel.app"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.headers.get("access-control-allow-origin") == "https://sarana-psi.vercel.app", resp.headers
    print("test_cors_headers_present_on_error_response: PASS")


def test_session_endpoint_matches_frontend_expectations() -> None:
    """Exactly the shape src/lib/api.js's fetchSession() and App.jsx assume."""
    server = _server_with_token("test-token-session6")
    client = TestClient(server.app)
    resp = client.get("/api/session")
    body = resp.json()
    assert set(["assistant_name", "tools", "desktop_connected"]).issubset(body.keys())
    assert isinstance(body["tools"], list) and len(body["tools"]) > 0
    assert all(set(t.keys()) == {"name", "description"} for t in body["tools"][:3])
    print("test_session_endpoint_matches_frontend_expectations: PASS")


def test_ws_command_round_trip_still_works() -> None:
    """The frontend's JarvisSocket.sendCommand() sends exactly this shape."""
    token = "test-token-ws6"
    server = _server_with_token(token)
    client = TestClient(server.app)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"type": "command", "text": "phase 6 integration check"})
        time.sleep(0.05)
    assert not server._command_queue.empty()
    assert server._command_queue.get_nowait() == "phase 6 integration check"
    print("test_ws_command_round_trip_still_works: PASS")


def test_ws_rejects_bad_token_closes_immediately() -> None:
    """Proves the exact failure mode src/lib/websocket.js's onAuthFailure
    path depends on: an invalid token closes before ever accepting, with no
    partial/garbage handshake."""
    server = _server_with_token("real-token-6")
    client = TestClient(server.app)
    raised = False
    try:
        with client.websocket_connect("/ws?token=WRONG"):
            pass
    except Exception:
        raised = True
    assert raised
    print("test_ws_rejects_bad_token_closes_immediately: PASS")


def test_audio_out_still_reachable_for_frontend_player() -> None:
    token = "test-token-audio6"
    server = _server_with_token(token)
    client = TestClient(server.app)
    with client.websocket_connect(f"/ws/audio-out?token={token}") as ws:
        time.sleep(0.02)
        assert len(server._audio_out_clients) == 1
    print("test_audio_out_still_reachable_for_frontend_player: PASS")


def test_headless_still_no_pyqt6() -> None:
    """Phase 6 touched only dashboard/server.py — re-confirm the headless
    brain path is still PyQt6-free after those edits."""
    from main import JarvisLive
    from core.headless_surface import HeadlessSurface

    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis is not None
    leaked = [m for m in sys.modules if m == "PyQt6" or m.startswith("PyQt6.")]
    assert not leaked, f"PyQt6 modules leaked into sys.modules: {leaked}"
    print("test_headless_still_no_pyqt6: PASS — sys.modules has no PyQt6 entries")


if __name__ == "__main__":
    test_cors_allows_configured_dev_origin()
    test_cors_rejects_unlisted_origin()
    test_cors_origin_list_has_no_wildcard()
    test_cors_allows_confirmed_production_origin_by_default()
    test_cors_accepts_singular_env_var_alias()
    test_cors_normalizes_trailing_slash()
    test_cors_headers_present_on_error_response()
    test_session_endpoint_matches_frontend_expectations()
    test_ws_command_round_trip_still_works()
    test_ws_rejects_bad_token_closes_immediately()
    test_audio_out_still_reachable_for_frontend_player()
    test_headless_still_no_pyqt6()
    print("\nAll Phase 6 tests passed.")
