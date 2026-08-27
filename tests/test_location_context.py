"""
tests/test_location_context.py -- Location foundation (A+B) regression
tests: browser geolocation -> POST /api/location -> JarvisLive session
state -> [LOCATION] Gemini context. No weather/maps/routing/MCP exists
yet -- this only covers the plumbing: receiving, validating, storing
(RAM-only), exposing, and correctly discarding a coordinate fix.

Run with:
    .venv/Scripts/python.exe -m tests.test_location_context
"""
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.headless_surface import HeadlessSurface
from dashboard.server import DashboardServer
from main import JarvisLive
from users import user_db

VALID = {"latitude": 60.1699, "longitude": 24.9384, "accuracy": 50}


def _auth_header(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ── dashboard/server.py: POST /api/location — validation ─────────────────

def test_location_rejected_without_authentication() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/api/location", json=VALID)
    assert resp.status_code == 401
    print("test_location_rejected_without_authentication: PASS")


def test_location_rejected_with_invalid_token() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/api/location", json=VALID, headers=_auth_header("not-a-real-token"))
    assert resp.status_code == 401
    print("test_location_rejected_with_invalid_token: PASS")


def _authed_client_and_token(server: DashboardServer):
    client = TestClient(server.app)
    resp = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    assert resp.status_code == 200, resp.text
    return client, resp.json()["token"]


def test_valid_location_accepted_and_forwarded_to_callback() -> None:
    server = DashboardServer()
    received = []
    server.set_location_callback(
        lambda lat, lon, acc, owner, fix_ts: received.append((lat, lon, acc, owner))
    )
    client, tok = _authed_client_and_token(server)

    resp = client.post("/api/location", json=VALID, headers=_auth_header(tok))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(received) == 1
    lat, lon, acc, owner = received[0]
    assert lat == VALID["latitude"]
    assert lon == VALID["longitude"]
    assert acc == VALID["accuracy"]
    assert owner == "saroj"   # canonical username, not the display name "Saroj"
    print("test_valid_location_accepted_and_forwarded_to_callback: PASS")


def test_location_owner_is_empty_for_pin_based_session() -> None:
    server = DashboardServer()
    received = []
    server.set_location_callback(lambda lat, lon, acc, owner, fix_ts: received.append(owner))
    client = TestClient(server.app)
    key = server.new_key()
    resp = client.post("/login", json={"pin": key})
    tok = resp.json()["token"]

    resp2 = client.post("/api/location", json=VALID, headers=_auth_header(tok))
    assert resp2.status_code == 200
    assert received == [""]
    print("test_location_owner_is_empty_for_pin_based_session: PASS")


def test_location_ignores_extra_identity_fields_in_body() -> None:
    """The authenticated token determines who this belongs to -- the body
    is never trusted for identity, only for coordinates."""
    server = DashboardServer()
    received = []
    server.set_location_callback(lambda lat, lon, acc, owner, fix_ts: received.append(owner))
    client, tok = _authed_client_and_token(server)

    body = dict(VALID, username="someone-else", user_id=999)
    resp = client.post("/api/location", json=body, headers=_auth_header(tok))
    assert resp.status_code == 200
    assert received == ["saroj"]   # from the TOKEN, never the claimed "username" field
    print("test_location_ignores_extra_identity_fields_in_body: PASS")


def test_location_forwards_fix_timestamp_when_provided() -> None:
    server = DashboardServer()
    received = []
    server.set_location_callback(lambda lat, lon, acc, owner, fix_ts: received.append(fix_ts))
    client, tok = _authed_client_and_token(server)

    resp = client.post("/api/location", json=dict(VALID, timestamp=1700000000123.0), headers=_auth_header(tok))
    assert resp.status_code == 200
    assert received == [1700000000123.0]
    print("test_location_forwards_fix_timestamp_when_provided: PASS")


def test_location_missing_timestamp_forwards_none() -> None:
    server = DashboardServer()
    received = []
    server.set_location_callback(lambda lat, lon, acc, owner, fix_ts: received.append(fix_ts))
    client, tok = _authed_client_and_token(server)

    resp = client.post("/api/location", json=VALID, headers=_auth_header(tok))
    assert resp.status_code == 200
    assert received == [None]
    print("test_location_missing_timestamp_forwards_none: PASS")


def _assert_rejected(server, tok, body, client) -> None:
    received_before = []
    server.set_location_callback(lambda *a: received_before.append(a))
    resp = client.post("/api/location", json=body, headers=_auth_header(tok))
    assert resp.status_code == 400, f"expected 400 for {body}, got {resp.status_code}"
    assert not received_before, f"callback must not fire for invalid body {body}"


def test_location_rejects_out_of_range_latitude() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    _assert_rejected(server, tok, dict(VALID, latitude=91), client)
    _assert_rejected(server, tok, dict(VALID, latitude=-91), client)
    print("test_location_rejects_out_of_range_latitude: PASS")


def test_location_rejects_out_of_range_longitude() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    _assert_rejected(server, tok, dict(VALID, longitude=181), client)
    _assert_rejected(server, tok, dict(VALID, longitude=-181), client)
    print("test_location_rejects_out_of_range_longitude: PASS")


def test_location_rejects_negative_accuracy() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    _assert_rejected(server, tok, dict(VALID, accuracy=-1), client)
    print("test_location_rejects_negative_accuracy: PASS")


def test_location_rejects_non_numeric_values() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    _assert_rejected(server, tok, dict(VALID, latitude="north"), client)
    _assert_rejected(server, tok, dict(VALID, longitude=None), client)
    print("test_location_rejects_non_numeric_values: PASS")


def test_location_rejects_nan_and_infinite_values() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    # JSON has no native NaN/Infinity literal, but Python's json module
    # (which FastAPI/starlette use) accepts the non-standard tokens on
    # decode -- send them as raw request content to exercise the same
    # path a permissive/older client library could actually produce.
    import json as _json
    body_nan = _json.dumps(dict(VALID, latitude=float("nan")))
    body_inf = _json.dumps(dict(VALID, longitude=float("inf")))
    resp1 = client.post("/api/location", content=body_nan,
                         headers={**_auth_header(tok), "Content-Type": "application/json"})
    resp2 = client.post("/api/location", content=body_inf,
                         headers={**_auth_header(tok), "Content-Type": "application/json"})
    assert resp1.status_code == 400
    assert resp2.status_code == 400
    print("test_location_rejects_nan_and_infinite_values: PASS")


def test_location_rejects_malformed_json_body() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    resp = client.post("/api/location", content="not json",
                        headers={**_auth_header(tok), "Content-Type": "application/json"})
    assert resp.status_code == 400
    print("test_location_rejects_malformed_json_body: PASS")


def test_set_location_callback_wiring() -> None:
    server = DashboardServer()
    calls = []
    server.set_location_callback(lambda *a: calls.append(a))
    assert server._location_callback is not None
    server._location_callback(1.0, 2.0, 3.0, "saroj", 1234567890.0)
    assert calls == [(1.0, 2.0, 3.0, "saroj", 1234567890.0)]
    print("test_set_location_callback_wiring: PASS")


def test_forget_token_cleans_up_canonical_owner() -> None:
    server = DashboardServer()
    client, tok = _authed_client_and_token(server)
    assert tok in server._session_canonical_owner
    server._forget_token(tok)
    assert tok not in server._session_canonical_owner
    print("test_forget_token_cleans_up_canonical_owner: PASS")


# ── main.py: _set_session_location() ──────────────────────────────────────

def test_session_location_initially_none() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._session_location is None
    print("test_session_location_initially_none: PASS")


def test_valid_location_is_stored_with_monotonic_timestamp() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    before = time.monotonic()
    jarvis._set_session_location(60.1699, 24.9384, 50.0)
    after = time.monotonic()

    loc = jarvis._session_location
    assert loc is not None
    assert loc["latitude"] == 60.1699
    assert loc["longitude"] == 24.9384
    assert loc["accuracy"] == 50.0
    assert before <= loc["timestamp"] <= after
    print("test_valid_location_is_stored_with_monotonic_timestamp: PASS")


def test_invalid_latitude_cannot_be_installed() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(200.0, 24.9384, 50.0)
    assert jarvis._session_location is None
    print("test_invalid_latitude_cannot_be_installed: PASS")


def test_invalid_longitude_cannot_be_installed() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(60.1699, -200.0, 50.0)
    assert jarvis._session_location is None
    print("test_invalid_longitude_cannot_be_installed: PASS")


def test_negative_accuracy_cannot_be_installed() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(60.1699, 24.9384, -1.0)
    assert jarvis._session_location is None
    print("test_negative_accuracy_cannot_be_installed: PASS")


def test_nan_and_infinite_values_cannot_be_installed() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(float("nan"), 24.9384, 50.0)
    assert jarvis._session_location is None
    jarvis._set_session_location(60.1699, float("inf"), 50.0)
    assert jarvis._session_location is None
    print("test_nan_and_infinite_values_cannot_be_installed: PASS")


def test_non_numeric_values_cannot_be_installed() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location("north", 24.9384, 50.0)
    assert jarvis._session_location is None
    print("test_non_numeric_values_cannot_be_installed: PASS")


def test_invalid_update_does_not_clobber_an_existing_valid_one() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(60.1699, 24.9384, 50.0)
    jarvis._set_session_location(999.0, 24.9384, 50.0)   # bad follow-up update
    assert jarvis._session_location["latitude"] == 60.1699
    print("test_invalid_update_does_not_clobber_an_existing_valid_one: PASS")


# ── out-of-order refresh protection (fix_timestamp) ───────────────────────

def test_older_fix_timestamp_is_rejected_even_if_it_arrives_later() -> None:
    """The exact race the location-capabilities task calls out: two
    overlapping refresh attempts complete out of order -- the one whose
    OWN fix is older must not win just because its HTTP response arrived
    second."""
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(60.1699, 24.9384, 50.0, fix_timestamp=2000.0)
    jarvis._set_session_location(61.0, 25.0, 50.0, fix_timestamp=1000.0)   # older fix, arrives later
    assert jarvis._session_location["latitude"] == 60.1699
    print("test_older_fix_timestamp_is_rejected_even_if_it_arrives_later: PASS")


def test_newer_fix_timestamp_is_accepted() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(60.1699, 24.9384, 50.0, fix_timestamp=1000.0)
    jarvis._set_session_location(61.0, 25.0, 50.0, fix_timestamp=2000.0)
    assert jarvis._session_location["latitude"] == 61.0
    print("test_newer_fix_timestamp_is_accepted: PASS")


def test_missing_fix_timestamp_never_blocks_an_update() -> None:
    """A client that doesn't send a fix timestamp at all (or the first
    ever update, with nothing stored yet) must never be refused just
    because ordering can't be determined."""
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(60.1699, 24.9384, 50.0, fix_timestamp=2000.0)
    jarvis._set_session_location(61.0, 25.0, 50.0)   # no fix_timestamp this time
    assert jarvis._session_location["latitude"] == 61.0
    print("test_missing_fix_timestamp_never_blocks_an_update: PASS")


def test_location_never_appears_in_write_log_calls() -> None:
    """Privacy requirement: raw coordinates must never land in the UI/
    Activity Log."""
    logged = []

    class _SpyUI(HeadlessSurface):
        def write_log(self, text):
            logged.append(text)

    jarvis = JarvisLive(_SpyUI())
    jarvis._set_session_location(60.169856, 24.938379, 50.0)
    combined = " ".join(logged)
    assert "60.169856" not in combined
    assert "24.938379" not in combined
    print("test_location_never_appears_in_write_log_calls: PASS")


# ── owner-mismatch race protection ────────────────────────────────────────

def test_requester_owner_mismatch_is_dropped() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = {"username": "sana"}
    jarvis._set_session_location(60.1699, 24.9384, 50.0, requester_owner="saroj")
    assert jarvis._session_location is None
    print("test_requester_owner_mismatch_is_dropped: PASS")


def test_requester_owner_match_is_accepted() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = {"username": "saroj"}
    jarvis._set_session_location(60.1699, 24.9384, 50.0, requester_owner="saroj")
    assert jarvis._session_location is not None
    print("test_requester_owner_match_is_accepted: PASS")


def test_empty_requester_owner_always_accepted() -> None:
    """A Remote Access (PIN) session has no associated username at all --
    must never be blocked by the owner check."""
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = {"username": "saroj"}
    jarvis._set_session_location(60.1699, 24.9384, 50.0, requester_owner="")
    assert jarvis._session_location is not None
    print("test_empty_requester_owner_always_accepted: PASS")


# ── logout / identity-switch clearing ─────────────────────────────────────

def test_clear_memory_session_clears_location() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(60.1699, 24.9384, 50.0)
    assert jarvis._session_location is not None
    with patch("main.clear_active_session"):
        jarvis._clear_memory_session()
    assert jarvis._session_location is None
    print("test_clear_memory_session_clears_location: PASS")


def test_new_login_clears_previous_location() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._set_session_location(60.1699, 24.9384, 50.0)
    assert jarvis._session_location is not None
    with patch("main.set_active_owner"):
        jarvis._set_user_profile(user_db.authenticate("Sana", "2060"))
    assert jarvis._session_location is None
    print("test_new_login_clears_previous_location: PASS")


def test_same_user_relogin_also_clears_location() -> None:
    """Even a same-account relogin starts clean -- the frontend re-requests
    a fresh fix on every login, so there's no benefit to keeping a stale
    one and it removes any staleness ambiguity."""
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = user_db.authenticate("Saroj", "2057")
    jarvis._set_session_location(60.1699, 24.9384, 50.0)
    with patch("main.set_active_owner"):
        jarvis._set_user_profile(user_db.authenticate("Saroj", "2057"))
    assert jarvis._session_location is None
    print("test_same_user_relogin_also_clears_location: PASS")


def test_desktop_never_creates_location_state() -> None:
    jarvis = JarvisLive(HeadlessSurface())   # auto_start=True, desktop's default
    assert jarvis._session_location is None
    print("test_desktop_never_creates_location_state: PASS")


# ── [LOCATION] context in _build_config() ─────────────────────────────────

def test_build_config_location_available_never_leaks_raw_coordinates() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._set_session_location(60.169856, 24.938379, 50.0)
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    assert "[LOCATION]" in instr
    assert "available" in instr.lower()
    assert "60.169856" not in instr
    assert "24.938379" not in instr
    print("test_build_config_location_available_never_leaks_raw_coordinates: PASS")


def test_build_config_location_unavailable_says_so() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    assert jarvis._session_location is None
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    assert "[LOCATION]" in instr
    assert "NOT" in instr.split("[LOCATION]")[1].split("\n\n")[0]
    print("test_build_config_location_unavailable_says_so: PASS")


def test_prompt_documents_location_context() -> None:
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text(encoding="utf-8")
    assert "[LOCATION]" in text
    assert "LOCATION:" in text
    print("test_prompt_documents_location_context: PASS")


# ── end-to-end races, through the real dashboard + JarvisLive wiring ──────

def _wire(server: DashboardServer, jarvis: JarvisLive) -> None:
    server.set_profile_callback(jarvis._set_user_profile)
    server.set_username_callback(jarvis._set_web_username)
    server.set_logout_callback(jarvis._clear_memory_session)
    server.set_location_callback(jarvis._set_session_location)


def test_stale_location_after_explicit_logout_is_rejected_by_auth_alone() -> None:
    server = DashboardServer()
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    _wire(server, jarvis)
    client = TestClient(server.app)

    resp_a = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    tok_a = resp_a.json()["token"]
    client.post("/api/logout", headers=_auth_header(tok_a))

    client.post("/login/username", json={"username": "Bandana", "pin": "2060"})

    resp = client.post("/api/location", json=VALID, headers=_auth_header(tok_a))
    assert resp.status_code == 401, "a logged-out token must be rejected before it ever reaches JarvisLive"
    assert jarvis._session_location is None
    print("test_stale_location_after_explicit_logout_is_rejected_by_auth_alone: PASS")


def test_stale_location_from_previous_identity_dropped_after_direct_switch() -> None:
    """The critical race: User A's browser location request is still
    in-flight (their token was never explicitly invalidated) when User B
    logs in directly, on the same running assistant. A's delayed POST
    must not install into B's session."""
    server = DashboardServer()
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    _wire(server, jarvis)
    client = TestClient(server.app)

    resp_a = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    tok_a = resp_a.json()["token"]

    # Bandana (Sana) logs in on the SAME session WITHOUT Saroj explicitly
    # logging out first -- Saroj's token stays technically valid.
    client.post("/login/username", json={"username": "Bandana", "pin": "2060"})
    assert jarvis._user_profile["username"] == "sana"

    # Saroj's browser geolocation, requested back when HE was still the
    # active identity, finally resolves and posts using his own,
    # still-valid token.
    resp = client.post("/api/location", json=VALID, headers=_auth_header(tok_a))
    assert resp.status_code == 200   # accepted at the HTTP layer -- auth alone can't know about the race

    # It must NOT have been installed -- Sana is the active identity now.
    assert jarvis._session_location is None
    print("test_stale_location_from_previous_identity_dropped_after_direct_switch: PASS")


def test_new_login_starts_with_no_location_after_previous_users_logout() -> None:
    server = DashboardServer()
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    _wire(server, jarvis)
    client = TestClient(server.app)

    resp_a = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    tok_a = resp_a.json()["token"]
    resp = client.post("/api/location", json=VALID, headers=_auth_header(tok_a))
    assert resp.status_code == 200
    assert jarvis._session_location is not None

    client.post("/api/logout", headers=_auth_header(tok_a))
    client.post("/login/username", json={"username": "Bandana", "pin": "2060"})

    assert jarvis._session_location is None
    print("test_new_login_starts_with_no_location_after_previous_users_logout: PASS")


def test_legitimate_location_for_the_new_user_still_works_after_a_switch() -> None:
    """Sanity companion: the owner check blocks STALE updates, not
    legitimate ones for whoever is actually active now."""
    server = DashboardServer()
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    _wire(server, jarvis)
    client = TestClient(server.app)

    client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
    resp_b = client.post("/login/username", json={"username": "Bandana", "pin": "2060"})
    tok_b = resp_b.json()["token"]

    resp = client.post("/api/location", json=VALID, headers=_auth_header(tok_b))
    assert resp.status_code == 200
    assert jarvis._session_location is not None
    print("test_legitimate_location_for_the_new_user_still_works_after_a_switch: PASS")


if __name__ == "__main__":
    test_location_rejected_without_authentication()
    test_location_rejected_with_invalid_token()
    test_valid_location_accepted_and_forwarded_to_callback()
    test_location_owner_is_empty_for_pin_based_session()
    test_location_ignores_extra_identity_fields_in_body()
    test_location_forwards_fix_timestamp_when_provided()
    test_location_missing_timestamp_forwards_none()
    test_location_rejects_out_of_range_latitude()
    test_location_rejects_out_of_range_longitude()
    test_location_rejects_negative_accuracy()
    test_location_rejects_non_numeric_values()
    test_location_rejects_nan_and_infinite_values()
    test_location_rejects_malformed_json_body()
    test_set_location_callback_wiring()
    test_forget_token_cleans_up_canonical_owner()
    test_session_location_initially_none()
    test_valid_location_is_stored_with_monotonic_timestamp()
    test_invalid_latitude_cannot_be_installed()
    test_invalid_longitude_cannot_be_installed()
    test_negative_accuracy_cannot_be_installed()
    test_nan_and_infinite_values_cannot_be_installed()
    test_non_numeric_values_cannot_be_installed()
    test_invalid_update_does_not_clobber_an_existing_valid_one()
    test_older_fix_timestamp_is_rejected_even_if_it_arrives_later()
    test_newer_fix_timestamp_is_accepted()
    test_missing_fix_timestamp_never_blocks_an_update()
    test_location_never_appears_in_write_log_calls()
    test_requester_owner_mismatch_is_dropped()
    test_requester_owner_match_is_accepted()
    test_empty_requester_owner_always_accepted()
    test_clear_memory_session_clears_location()
    test_new_login_clears_previous_location()
    test_same_user_relogin_also_clears_location()
    test_desktop_never_creates_location_state()
    test_build_config_location_available_never_leaks_raw_coordinates()
    test_build_config_location_unavailable_says_so()
    test_prompt_documents_location_context()
    test_stale_location_after_explicit_logout_is_rejected_by_auth_alone()
    test_stale_location_from_previous_identity_dropped_after_direct_switch()
    test_new_login_starts_with_no_location_after_previous_users_logout()
    test_legitimate_location_for_the_new_user_still_works_after_a_switch()
    print("\nAll location-foundation tests passed.")
