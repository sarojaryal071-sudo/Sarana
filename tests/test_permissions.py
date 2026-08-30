"""
tests/test_permissions.py -- Permissions foundation regression tests:
browser Permissions-API/permission-request state -> POST /api/capabilities
-> JarvisLive session state -> honest [LOCATION]/[CAPABILITIES] Gemini
context and honest [LOCATION_UNAVAILABLE] tool results. Mirrors
tests/test_location_context.py's own conventions -- this only covers the
plumbing: receiving, validating, storing (RAM-only), exposing, and
correctly discarding a reported permission state; no fake permission
state, no browser mocking.

Run with:
    .venv/Scripts/python.exe -m tests.test_permissions
"""
import asyncio
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.headless_surface import HeadlessSurface
from dashboard.server import DashboardServer
from main import JarvisLive, LOCATION_DENIED_RESULT, LOCATION_UNAVAILABLE_RESULT
from users import user_db


def _auth_header(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _authed_client_and_token(server: DashboardServer):
    client = TestClient(server.app)
    resp = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    assert resp.status_code == 200, resp.text
    return client, resp.json()["token"]


# ── dashboard/server.py: POST /api/capabilities — validation ─────────────

def test_capabilities_rejected_without_authentication() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/api/capabilities", json={"microphone": "denied"})
    assert resp.status_code == 401
    print("test_capabilities_rejected_without_authentication: PASS")


def test_capabilities_rejected_with_invalid_token() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post(
        "/api/capabilities", json={"microphone": "denied"},
        headers=_auth_header("not-a-real-token"),
    )
    assert resp.status_code == 401
    print("test_capabilities_rejected_with_invalid_token: PASS")


def test_valid_capabilities_accepted_and_forwarded_to_callback() -> None:
    server = DashboardServer()
    received = []
    server.set_capabilities_callback(
        lambda microphone, location, requester_owner: received.append(
            (microphone, location, requester_owner)
        )
    )
    client, tok = _authed_client_and_token(server)

    resp = client.post(
        "/api/capabilities", json={"microphone": "granted", "location": "denied"},
        headers=_auth_header(tok),
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert received == [("granted", "denied", "saroj")]
    print("test_valid_capabilities_accepted_and_forwarded_to_callback: PASS")


def test_capabilities_allows_reporting_only_one_key() -> None:
    server = DashboardServer()
    received = []
    server.set_capabilities_callback(
        lambda microphone, location, requester_owner: received.append((microphone, location))
    )
    client, tok = _authed_client_and_token(server)

    resp = client.post(
        "/api/capabilities", json={"location": "prompt"}, headers=_auth_header(tok)
    )
    assert resp.status_code == 200
    assert received == [(None, "prompt")]
    print("test_capabilities_allows_reporting_only_one_key: PASS")


def test_capabilities_rejects_unknown_microphone_state() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    resp = client.post(
        "/api/capabilities", json={"microphone": "always-on"}, headers=_auth_header(tok)
    )
    assert resp.status_code == 400
    print("test_capabilities_rejects_unknown_microphone_state: PASS")


def test_capabilities_rejects_unknown_location_state() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    resp = client.post(
        "/api/capabilities", json={"location": "sometimes"}, headers=_auth_header(tok)
    )
    assert resp.status_code == 400
    print("test_capabilities_rejects_unknown_location_state: PASS")


def test_capabilities_rejects_empty_body() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    resp = client.post("/api/capabilities", json={}, headers=_auth_header(tok))
    assert resp.status_code == 400
    print("test_capabilities_rejects_empty_body: PASS")


def test_capabilities_rejects_malformed_json_body() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    resp = client.post(
        "/api/capabilities", content="not json",
        headers={**_auth_header(tok), "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    print("test_capabilities_rejects_malformed_json_body: PASS")


def test_capabilities_owner_is_empty_for_pin_based_session() -> None:
    server = DashboardServer()
    received = []
    server.set_capabilities_callback(
        lambda microphone, location, requester_owner: received.append(requester_owner)
    )
    client = TestClient(server.app)
    key = server.new_key()
    resp = client.post("/login", json={"pin": key})
    tok = resp.json()["token"]

    resp2 = client.post(
        "/api/capabilities", json={"microphone": "denied"}, headers=_auth_header(tok)
    )
    assert resp2.status_code == 200
    assert received == [""]
    print("test_capabilities_owner_is_empty_for_pin_based_session: PASS")


def test_set_capabilities_callback_wiring() -> None:
    server = DashboardServer()
    calls = []
    server.set_capabilities_callback(lambda *a: calls.append(a))
    assert server._capabilities_callback is not None
    server._capabilities_callback("denied", None, "saroj")
    assert calls == [("denied", None, "saroj")]
    print("test_set_capabilities_callback_wiring: PASS")


# ── main.py: _set_session_capabilities() ──────────────────────────────────

def test_session_permissions_initially_empty() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._session_permissions == {}
    print("test_session_permissions_initially_empty: PASS")


def test_valid_permission_states_are_stored() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_capabilities(microphone="granted", location="denied")
    assert jarvis._session_permissions == {"microphone": "granted", "location": "denied"}
    print("test_valid_permission_states_are_stored: PASS")


def test_unknown_permission_state_is_dropped_not_stored() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_capabilities(microphone="always-on")
    assert "microphone" not in jarvis._session_permissions
    print("test_unknown_permission_state_is_dropped_not_stored: PASS")


def test_omitted_capability_leaves_the_other_untouched() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_capabilities(microphone="granted", location="denied")
    jarvis._set_session_capabilities(location="granted")
    assert jarvis._session_permissions == {"microphone": "granted", "location": "granted"}
    print("test_omitted_capability_leaves_the_other_untouched: PASS")


def test_capabilities_requester_owner_mismatch_is_dropped() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = {"username": "sana"}
    jarvis._set_session_capabilities(location="denied", requester_owner="saroj")
    assert jarvis._session_permissions == {}
    print("test_capabilities_requester_owner_mismatch_is_dropped: PASS")


def test_capabilities_requester_owner_match_is_accepted() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = {"username": "saroj"}
    jarvis._set_session_capabilities(location="denied", requester_owner="saroj")
    assert jarvis._session_permissions == {"location": "denied"}
    print("test_capabilities_requester_owner_match_is_accepted: PASS")


def test_capabilities_empty_requester_owner_always_accepted() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = {"username": "saroj"}
    jarvis._set_session_capabilities(location="denied", requester_owner="")
    assert jarvis._session_permissions == {"location": "denied"}
    print("test_capabilities_empty_requester_owner_always_accepted: PASS")


# ── logout / identity-switch clearing ─────────────────────────────────────

def test_clear_memory_session_clears_permissions() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_capabilities(microphone="denied", location="denied")
    with patch("main.clear_active_session"):
        jarvis._clear_memory_session()
    assert jarvis._session_permissions == {}
    print("test_clear_memory_session_clears_permissions: PASS")


def test_new_login_clears_previous_permissions() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_capabilities(microphone="denied", location="denied")
    with patch("main.set_active_owner"):
        jarvis._set_user_profile(user_db.authenticate("Sana", "2060"))
    assert jarvis._session_permissions == {}
    print("test_new_login_clears_previous_permissions: PASS")


# ── _location_unavailable_result() honesty ────────────────────────────────

def test_location_unavailable_result_defaults_to_generic_message() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._location_unavailable_result() == LOCATION_UNAVAILABLE_RESULT
    print("test_location_unavailable_result_defaults_to_generic_message: PASS")


def test_location_unavailable_result_names_settings_when_denied() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_capabilities(location="denied")
    result = jarvis._location_unavailable_result()
    assert result == LOCATION_DENIED_RESULT
    assert result.startswith("[LOCATION_UNAVAILABLE]")
    assert "Settings" in result
    print("test_location_unavailable_result_names_settings_when_denied: PASS")


def test_location_unavailable_result_stays_generic_when_only_prompt() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_capabilities(location="prompt")
    assert jarvis._location_unavailable_result() == LOCATION_UNAVAILABLE_RESULT
    print("test_location_unavailable_result_stays_generic_when_only_prompt: PASS")


# ── [LOCATION] / [CAPABILITIES] context in _build_config() ────────────────

def test_build_config_location_denied_names_settings() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._set_session_capabilities(location="denied")
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    section = instr.split("[LOCATION]")[1].split("\n\n")[0]
    assert "off" in section.lower()
    assert "Settings" in section
    print("test_build_config_location_denied_names_settings: PASS")


def test_build_config_location_unavailable_without_denial_stays_generic() -> None:
    """Not denied, just never reported (the common case, e.g. desktop
    or a browser session before Permissions API state has arrived
    yet) -- must NOT claim "off"/Settings when that isn't actually known."""
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    section = instr.split("[LOCATION]")[1].split("\n\n")[0]
    assert "Settings" not in section
    print("test_build_config_location_unavailable_without_denial_stays_generic: PASS")


def test_build_config_mic_denied_adds_honest_capability_note() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._set_session_capabilities(microphone="denied")
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    section = instr.split("[CAPABILITIES]")[1].split("[LOCATION]")[0]
    assert "microphone" in section.lower()
    assert "Settings" in section
    print("test_build_config_mic_denied_adds_honest_capability_note: PASS")


def test_build_config_mic_not_denied_adds_no_extra_note() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    section = instr.split("[CAPABILITIES]")[1].split("[LOCATION]")[0]
    assert "microphone" not in section.lower()
    print("test_build_config_mic_not_denied_adds_no_extra_note: PASS")


def test_build_config_desktop_never_mentions_microphone_denial() -> None:
    """Desktop is auto_start=True -- the whole [CAPABILITIES] section is
    the one-liner branch that never even looks at _session_permissions."""
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_capabilities(microphone="denied")
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    section = instr.split("[CAPABILITIES]")[1].split("[LOCATION]")[0]
    assert "microphone" not in section.lower()
    print("test_build_config_desktop_never_mentions_microphone_denial: PASS")


# ── capability-aware tool routing: no pointless call when denied ──────────

class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


def test_get_weather_does_not_hit_the_network_when_location_denied() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_session_capabilities(location="denied")
        with patch("main.get_weather_text") as mock_weather:
            fc = _FakeFunctionCall("get_weather", {})
            resp = await jarvis._execute_tool(fc)
            mock_weather.assert_not_called()
        result = resp.response["result"]
        assert result.startswith("[LOCATION_UNAVAILABLE]")
        assert "Settings" in result
    asyncio.run(_run())
    print("test_get_weather_does_not_hit_the_network_when_location_denied: PASS")


if __name__ == "__main__":
    test_capabilities_rejected_without_authentication()
    test_capabilities_rejected_with_invalid_token()
    test_valid_capabilities_accepted_and_forwarded_to_callback()
    test_capabilities_allows_reporting_only_one_key()
    test_capabilities_rejects_unknown_microphone_state()
    test_capabilities_rejects_unknown_location_state()
    test_capabilities_rejects_empty_body()
    test_capabilities_rejects_malformed_json_body()
    test_capabilities_owner_is_empty_for_pin_based_session()
    test_set_capabilities_callback_wiring()
    test_session_permissions_initially_empty()
    test_valid_permission_states_are_stored()
    test_unknown_permission_state_is_dropped_not_stored()
    test_omitted_capability_leaves_the_other_untouched()
    test_capabilities_requester_owner_mismatch_is_dropped()
    test_capabilities_requester_owner_match_is_accepted()
    test_capabilities_empty_requester_owner_always_accepted()
    test_clear_memory_session_clears_permissions()
    test_new_login_clears_previous_permissions()
    test_location_unavailable_result_defaults_to_generic_message()
    test_location_unavailable_result_names_settings_when_denied()
    test_location_unavailable_result_stays_generic_when_only_prompt()
    test_build_config_location_denied_names_settings()
    test_build_config_location_unavailable_without_denial_stays_generic()
    test_build_config_mic_denied_adds_honest_capability_note()
    test_build_config_mic_not_denied_adds_no_extra_note()
    test_build_config_desktop_never_mentions_microphone_denial()
    test_get_weather_does_not_hit_the_network_when_location_denied()
    print("\nAll permissions-foundation tests passed.")
