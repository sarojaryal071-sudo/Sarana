"""
tests/test_backend_logout.py — resource-cleanup regression tests (item 2):
POST /api/logout, the passive stale-token TTL sweep, and that identity
switching (login -> logout -> a different account -> logout -> the
original account) still works correctly through it all.

Run with:
    .venv/Scripts/python.exe -m tests.test_backend_logout
"""
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer, _TOKEN_TTL_SECONDS


def _server_with_username_login(username: str, pin: str, client: TestClient) -> str:
    resp = client.post("/login/username", json={"username": username, "pin": pin})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


# ── successful logout + cleanup ───────────────────────────────────────────

def test_successful_logout_returns_ok() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    token = _server_with_username_login("Saroj", "2057", client)

    resp = client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    print("test_successful_logout_returns_ok: PASS")


def test_logout_invalidates_the_token() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    token = _server_with_username_login("Saroj", "2057", client)
    assert token in server._tokens

    client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert token not in server._tokens
    print("test_logout_invalidates_the_token: PASS")


def test_logout_removes_associated_session_metadata() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    token = _server_with_username_login("Saroj", "2057", client)
    assert token in server._session_auth_mode
    assert token in server._session_usernames

    client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert token not in server._session_auth_mode
    assert token not in server._session_usernames
    assert token not in server._session_timezones
    assert token not in server._token_keys
    assert token not in server._token_created_at
    print("test_logout_removes_associated_session_metadata: PASS")


def test_old_token_rejected_after_logout() -> None:
    """The actual security-relevant behavior: a request using the
    logged-out token must be rejected exactly like any other invalid
    token, not silently accepted."""
    server = DashboardServer()
    client = TestClient(server.app)
    token = _server_with_username_login("Saroj", "2057", client)

    client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})

    resp = client.post("/api/wake", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    resp2 = client.post("/api/interrupt", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 401
    print("test_old_token_rejected_after_logout: PASS")


def test_logout_never_exposes_sensitive_info() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    token = _server_with_username_login("Saroj", "2057", client)
    resp = client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert set(resp.json().keys()) == {"ok"}
    assert "pin_hash" not in resp.text
    assert "Saroj" not in resp.text
    print("test_logout_never_exposes_sensitive_info: PASS")


# ── graceful handling of invalid/expired/missing tokens ──────────────────

def test_logout_of_already_invalid_token_is_a_safe_no_op() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/api/logout", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    print("test_logout_of_already_invalid_token_is_a_safe_no_op: PASS")


def test_logout_twice_is_idempotent() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    token = _server_with_username_login("Saroj", "2057", client)

    resp1 = client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    resp2 = client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp1.status_code == resp2.status_code == 200
    print("test_logout_twice_is_idempotent: PASS")


def test_logout_with_no_authorization_header_is_safe() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/api/logout")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    print("test_logout_with_no_authorization_header_is_safe: PASS")


# ── stale-token TTL sweep ──────────────────────────────────────────────────

def test_purge_stale_tokens_removes_only_expired_tokens() -> None:
    server = DashboardServer()
    now = time.time()
    server._tokens.add("fresh")
    server._token_created_at["fresh"] = now
    server._tokens.add("stale")
    server._token_created_at["stale"] = now - _TOKEN_TTL_SECONDS - 60
    server._session_usernames["stale"] = "SomeoneWhoNeverLoggedOut"

    server._purge_stale_tokens()

    assert "fresh" in server._tokens
    assert "stale" not in server._tokens
    assert "stale" not in server._session_usernames
    print("test_purge_stale_tokens_removes_only_expired_tokens: PASS")


def test_auth_check_lazily_sweeps_stale_tokens() -> None:
    """The sweep is wired into _auth() (rate-limited) rather than a
    separate background task — confirm an authenticated request actually
    triggers cleanup of an old, unrelated stale token."""
    server = DashboardServer()
    client = TestClient(server.app)
    token = _server_with_username_login("Saroj", "2057", client)

    server._tokens.add("ancient")
    server._token_created_at["ancient"] = time.time() - _TOKEN_TTL_SECONDS - 1
    server._last_token_sweep = 0.0   # force the next _auth() call to sweep

    resp = client.post("/api/wake", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200   # the real token is unaffected
    assert "ancient" not in server._tokens
    print("test_auth_check_lazily_sweeps_stale_tokens: PASS")


# ── identity switching still works across logout/login cycles ────────────

def test_identity_switching_still_works_across_logout_cycles() -> None:
    """Saroj -> logout -> Bandana -> logout -> Saroj again, entirely
    through the real HTTP routes -- proves logout cleanup doesn't disturb
    the separate identity-switch mechanism (main.py's _set_user_profile())."""
    server = DashboardServer()
    client = TestClient(server.app)

    saroj_profiles = []
    server.set_profile_callback(lambda p: saroj_profiles.append(p))

    tok1 = _server_with_username_login("Saroj", "2057", client)
    client.post("/api/logout", headers={"Authorization": f"Bearer {tok1}"})
    assert tok1 not in server._tokens

    tok2 = _server_with_username_login("Bandana", "2060", client)
    assert tok2 != tok1
    client.post("/api/logout", headers={"Authorization": f"Bearer {tok2}"})
    assert tok2 not in server._tokens

    tok3 = _server_with_username_login("Saroj", "2057", client)
    assert tok3 in server._tokens

    assert [p["assistant_name"] for p in saroj_profiles] == ["Sara", "Kanha", "Sara"]
    print("test_identity_switching_still_works_across_logout_cycles: PASS")


def test_pin_login_still_works_unaffected_by_logout_changes() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    key = server.new_key()
    resp = client.post("/login", json={"pin": key})
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert token in server._tokens
    assert server._session_auth_mode[token] == "remote"

    logout_resp = client.post("/api/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout_resp.status_code == 200
    assert token not in server._tokens
    print("test_pin_login_still_works_unaffected_by_logout_changes: PASS")


if __name__ == "__main__":
    test_successful_logout_returns_ok()
    test_logout_invalidates_the_token()
    test_logout_removes_associated_session_metadata()
    test_old_token_rejected_after_logout()
    test_logout_never_exposes_sensitive_info()
    test_logout_of_already_invalid_token_is_a_safe_no_op()
    test_logout_twice_is_idempotent()
    test_logout_with_no_authorization_header_is_safe()
    test_purge_stale_tokens_removes_only_expired_tokens()
    test_auth_check_lazily_sweeps_stale_tokens()
    test_identity_switching_still_works_across_logout_cycles()
    test_pin_login_still_works_unaffected_by_logout_changes()
    print("\nAll backend logout tests passed.")
