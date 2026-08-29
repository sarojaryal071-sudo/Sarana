"""
tests/test_calendar_oauth_routes.py -- dashboard/server.py's 4 new Google
Calendar routes (/auth/google, /auth/google/callback,
/api/calendar/status, /api/calendar/disconnect), exercised through the
real FastAPI app via TestClient. calendar_auth/calendar_store are mocked
throughout -- never a live Google endpoint or database.

Run with:
    .venv/Scripts/python.exe -m tests.test_calendar_oauth_routes
"""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer


def _server_with_user(username: str, pin: str):
    server = DashboardServer()
    client = TestClient(server.app, follow_redirects=False)
    resp = client.post("/login/username", json={"username": username, "pin": pin})
    assert resp.status_code == 200, resp.text
    return server, client, resp.json()["token"]


def _configured():
    """Patches both calendar_auth.is_configured()/calendar_store.is_
    configured() to True, since the routes gate on both."""
    return (
        patch("dashboard.server.calendar_auth.is_configured", return_value=True),
        patch("dashboard.server.calendar_store.is_configured", return_value=True),
    )


# ── GET /auth/google — authorization URL generation ───────────────────

def test_auth_google_rejects_unauthenticated() -> None:
    server = DashboardServer()
    client = TestClient(server.app, follow_redirects=False)
    resp = client.get("/auth/google", params={"token": "not-a-real-token"})
    assert resp.status_code == 401
    print("test_auth_google_rejects_unauthenticated: PASS")


def test_auth_google_rejects_missing_token() -> None:
    server = DashboardServer()
    client = TestClient(server.app, follow_redirects=False)
    resp = client.get("/auth/google")
    assert resp.status_code == 401
    print("test_auth_google_rejects_missing_token: PASS")


def test_auth_google_rejects_pin_based_remote_session() -> None:
    """Google Calendar connects to a specific SARANA ACCOUNT — a Remote
    Access (PIN) session has no canonical username at all."""
    server = DashboardServer()
    client = TestClient(server.app, follow_redirects=False)
    key = server.new_key()
    resp = client.post("/login", json={"pin": key})
    tok = resp.json()["token"]
    resp2 = client.get("/auth/google", params={"token": tok})
    assert resp2.status_code == 400
    print("test_auth_google_rejects_pin_based_remote_session: PASS")


def test_auth_google_returns_503_when_not_configured() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    with patch("dashboard.server.calendar_auth.is_configured", return_value=False):
        resp = client.get("/auth/google", params={"token": tok})
    assert resp.status_code == 503
    print("test_auth_google_returns_503_when_not_configured: PASS")


def test_auth_google_missing_env_vars_reported_honestly() -> None:
    """Missing GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI -- is_configured()
    itself already covers this (see test_calendar_auth.py); this proves
    the ROUTE actually checks it rather than crashing."""
    server, client, tok = _server_with_user("Saroj", "2057")
    with patch("dashboard.server.calendar_auth.is_configured", return_value=False), \
         patch("dashboard.server.calendar_store.is_configured", return_value=True):
        resp = client.get("/auth/google", params={"token": tok})
    assert resp.status_code == 503
    print("test_auth_google_missing_env_vars_reported_honestly: PASS")


def test_auth_google_success_redirects_to_google_and_stores_state() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    p1, p2 = _configured()
    with p1, p2, patch(
        "dashboard.server.calendar_auth.build_auth_url",
        return_value=("https://accounts.google.com/o/oauth2/auth?mock=1", "fake-pkce-verifier"),
    ) as mock_build:
        resp = client.get("/auth/google", params={"token": tok})
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "https://accounts.google.com/o/oauth2/auth?mock=1"
    state = mock_build.call_args.args[0]
    assert state in server._google_oauth_states
    assert server._google_oauth_states[state]["owner"] == "saroj"
    print("test_auth_google_success_redirects_to_google_and_stores_state: PASS")


def test_auth_google_stores_the_pkce_verifier_alongside_state() -> None:
    """PKCE regression coverage: /auth/google must persist EXACTLY the
    verifier build_auth_url() returned -- see calendar_auth.build_auth_url()'s
    own docstring for why a mismatch/absence breaks the token exchange."""
    server, client, tok = _server_with_user("Saroj", "2057")
    p1, p2 = _configured()
    with p1, p2, patch(
        "dashboard.server.calendar_auth.build_auth_url",
        return_value=("https://accounts.google.com/o/oauth2/auth?mock=1", "the-real-generated-verifier"),
    ) as mock_build:
        client.get("/auth/google", params={"token": tok})
    state = mock_build.call_args.args[0]
    assert server._google_oauth_states[state]["code_verifier"] == "the-real-generated-verifier"
    print("test_auth_google_stores_the_pkce_verifier_alongside_state: PASS")


def test_auth_google_validates_return_to_against_cors_allowlist() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    p1, p2 = _configured()
    with p1, p2, patch("dashboard.server.calendar_auth.build_auth_url", return_value=("https://x/", "v")):
        client.get("/auth/google", params={"token": tok, "return_to": "https://evil.example.com"})
        state = next(iter(server._google_oauth_states))
        assert server._google_oauth_states[state]["return_to"] == ""   # untrusted origin ignored

        server._google_oauth_states.clear()
        client.get("/auth/google", params={"token": tok, "return_to": "https://sarana-psi.vercel.app"})
        state2 = next(iter(server._google_oauth_states))
        assert server._google_oauth_states[state2]["return_to"] == "https://sarana-psi.vercel.app"
    print("test_auth_google_validates_return_to_against_cors_allowlist: PASS")


# ── GET /auth/google/callback ──────────────────────────────────────────

def _fake_credentials(token="access-tok", refresh_token="refresh-tok"):
    creds = MagicMock()
    creds.token = token
    creds.refresh_token = refresh_token
    creds.to_json.return_value = '{"token": "access-tok", "refresh_token": "refresh-tok"}'
    return creds


def test_callback_success_stores_credentials_and_redirects_connected() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "test-state-123"
    server._google_oauth_states[state] = {"owner": "saroj", "return_to": "https://sarana-psi.vercel.app", "expires": __import__("time").time() + 600, "code_verifier": "verifier-abc"}

    saved = {}
    with patch("dashboard.server.calendar_auth.exchange_code", return_value=_fake_credentials()), \
         patch("dashboard.server.calendar_auth.fetch_email", return_value="saroj@example.com"), \
         patch("dashboard.server.calendar_store.init_schema"), \
         patch("dashboard.server.calendar_store.save_credentials", side_effect=lambda o, j, e: saved.update(owner=o, json=j, email=e)):
        resp = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})

    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "https://sarana-psi.vercel.app/?calendar=connected"
    assert saved["owner"] == "saroj"
    assert saved["email"] == "saroj@example.com"
    assert state not in server._google_oauth_states   # single-use — consumed
    print("test_callback_success_stores_credentials_and_redirects_connected: PASS")


# ── PKCE: the exact production bug (invalid_grant "Missing code
# verifier") and its fix ────────────────────────────────────────────────

def test_callback_passes_stored_code_verifier_to_exchange_code() -> None:
    """The core regression test: whatever code_verifier was stored
    alongside `state` at /auth/google time must be exactly what reaches
    calendar_auth.exchange_code() at callback time -- not omitted, not
    a different value."""
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "pkce-state"
    server._google_oauth_states[state] = {
        "owner": "saroj", "return_to": "", "expires": __import__("time").time() + 600,
        "code_verifier": "the-exact-verifier-generated-at-auth-time",
    }
    with patch("dashboard.server.calendar_auth.exchange_code", return_value=_fake_credentials()) as mock_exchange, \
         patch("dashboard.server.calendar_auth.fetch_email", return_value=""), \
         patch("dashboard.server.calendar_store.init_schema"), \
         patch("dashboard.server.calendar_store.save_credentials"):
        resp = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert resp.status_code in (302, 307)
    mock_exchange.assert_called_once_with("auth-code", "the-exact-verifier-generated-at-auth-time")
    print("test_callback_passes_stored_code_verifier_to_exchange_code: PASS")


def test_callback_verifier_removed_with_state_cannot_be_reused() -> None:
    """The verifier is stored inside the same single-use state entry, so
    popping `state` (see the existing replay-protection check) removes
    the verifier too, by construction -- a second callback attempt with
    the same state has no verifier to reuse, not just no state."""
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "pkce-reuse-state"
    server._google_oauth_states[state] = {
        "owner": "saroj", "return_to": "", "expires": __import__("time").time() + 600,
        "code_verifier": "one-time-verifier",
    }
    with patch("dashboard.server.calendar_auth.exchange_code", return_value=_fake_credentials()), \
         patch("dashboard.server.calendar_auth.fetch_email", return_value=""), \
         patch("dashboard.server.calendar_store.init_schema"), \
         patch("dashboard.server.calendar_store.save_credentials"):
        first = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert first.status_code in (302, 307)
    assert state not in server._google_oauth_states

    second = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert second.status_code == 400   # no state left to find a verifier in at all
    print("test_callback_verifier_removed_with_state_cannot_be_reused: PASS")


def test_callback_missing_code_verifier_is_a_controlled_failure_not_a_crash() -> None:
    """A state entry with no code_verifier key at all (e.g. one created
    by a pre-fix version of the code surviving a redeploy) must not
    raise a KeyError -- it degrades to the same existing ?calendar=error
    path a real Google rejection already uses."""
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "no-verifier-state"
    server._google_oauth_states[state] = {
        "owner": "saroj", "return_to": "https://sarana-psi.vercel.app", "expires": __import__("time").time() + 600,
        # deliberately no "code_verifier" key
    }
    captured = {}

    def _fake_exchange(code, code_verifier):
        captured["code_verifier"] = code_verifier
        raise RuntimeError("invalid_grant: Missing code verifier.")

    with patch("dashboard.server.calendar_auth.exchange_code", side_effect=_fake_exchange):
        resp = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert resp.status_code in (302, 307)
    assert "calendar=error" in resp.headers["location"]
    assert captured["code_verifier"] == ""   # .get(..., "") fallback, never a crash
    print("test_callback_missing_code_verifier_is_a_controlled_failure_not_a_crash: PASS")


def test_callback_missing_state_rejected() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    resp = client.get("/auth/google/callback", params={"code": "auth-code"})
    assert resp.status_code == 400
    print("test_callback_missing_state_rejected: PASS")


def test_callback_unrecognized_state_rejected() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    resp = client.get("/auth/google/callback", params={"code": "auth-code", "state": "never-issued"})
    assert resp.status_code == 400
    print("test_callback_unrecognized_state_rejected: PASS")


def test_callback_expired_state_rejected() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "expired-state"
    server._google_oauth_states[state] = {"owner": "saroj", "return_to": "", "expires": __import__("time").time() - 1}
    resp = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert resp.status_code == 400
    print("test_callback_expired_state_rejected: PASS")


def test_callback_replayed_state_rejected_second_time() -> None:
    """A state is single-use — even a genuinely valid one must not work
    twice (the exact 'stale/replayed callback' protection)."""
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "one-time-state"
    server._google_oauth_states[state] = {"owner": "saroj", "return_to": "", "expires": __import__("time").time() + 600}
    with patch("dashboard.server.calendar_auth.exchange_code", return_value=_fake_credentials()), \
         patch("dashboard.server.calendar_auth.fetch_email", return_value=""), \
         patch("dashboard.server.calendar_store.init_schema"), \
         patch("dashboard.server.calendar_store.save_credentials"):
        first = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert first.status_code in (302, 307)

    second = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert second.status_code == 400
    print("test_callback_replayed_state_rejected_second_time: PASS")


def test_callback_google_error_param_redirects_cancelled_not_success() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "declined-state"
    server._google_oauth_states[state] = {"owner": "saroj", "return_to": "https://sarana-psi.vercel.app", "expires": __import__("time").time() + 600}
    resp = client.get("/auth/google/callback", params={"state": state, "error": "access_denied"})
    assert resp.status_code in (302, 307)
    assert "calendar=cancelled" in resp.headers["location"]
    assert state not in server._google_oauth_states
    print("test_callback_google_error_param_redirects_cancelled_not_success: PASS")


def test_callback_token_exchange_failure_redirects_error_not_success() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "exchange-fails"
    server._google_oauth_states[state] = {"owner": "saroj", "return_to": "https://sarana-psi.vercel.app", "expires": __import__("time").time() + 600}
    with patch("dashboard.server.calendar_auth.exchange_code", side_effect=RuntimeError("invalid_grant")):
        resp = client.get("/auth/google/callback", params={"code": "bad-code", "state": state})
    assert resp.status_code in (302, 307)
    assert "calendar=error" in resp.headers["location"]
    print("test_callback_token_exchange_failure_redirects_error_not_success: PASS")


def test_callback_never_logs_the_authorization_code_or_token() -> None:
    # No pytest fixtures in this project's test convention (plain
    # `python -m tests.test_X` execution) — capture stdout manually.
    import io
    import contextlib

    server, client, tok = _server_with_user("Saroj", "2057")
    state = "state-for-log-check"
    secret_verifier = "SUPER-SECRET-PKCE-VERIFIER-DO-NOT-LOG"
    server._google_oauth_states[state] = {
        "owner": "saroj", "return_to": "", "expires": __import__("time").time() + 600,
        "code_verifier": secret_verifier,
    }
    secret_code = "SUPER-SECRET-AUTH-CODE-DO-NOT-LOG"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), \
         patch("dashboard.server.calendar_auth.exchange_code", side_effect=RuntimeError("boom")):
        client.get("/auth/google/callback", params={"code": secret_code, "state": state})
    assert secret_code not in buf.getvalue()
    assert secret_verifier not in buf.getvalue()
    print("test_callback_never_logs_the_authorization_code_or_token: PASS")


def test_stale_callback_from_superseded_identity_still_binds_to_original_owner() -> None:
    """The concrete cross-user race: Saroj starts the connect flow
    (state bound to 'saroj' at that moment); before he finishes, someone
    else's action on the SAME running dashboard does not change which
    owner THIS state resolves to — the identity is fixed at state-
    creation time, never re-derived from 'whoever is active now'. This
    is what stops a delayed/replayed callback from ever attaching
    credentials to the wrong account."""
    server, client, tok = _server_with_user("Saroj", "2057")
    state = "saroj-started-this"
    server._google_oauth_states[state] = {"owner": "saroj", "return_to": "", "expires": __import__("time").time() + 600}

    # Sana logs in on the same server in the meantime (a second browser/device).
    client.post("/login/username", json={"username": "Bandana", "pin": "2060"})

    saved = {}
    with patch("dashboard.server.calendar_auth.exchange_code", return_value=_fake_credentials()), \
         patch("dashboard.server.calendar_auth.fetch_email", return_value=""), \
         patch("dashboard.server.calendar_store.init_schema"), \
         patch("dashboard.server.calendar_store.save_credentials", side_effect=lambda o, j, e: saved.update(owner=o)):
        client.get("/auth/google/callback", params={"code": "auth-code", "state": state})

    assert saved["owner"] == "saroj"   # never "sana", regardless of who else logged in meanwhile
    print("test_stale_callback_from_superseded_identity_still_binds_to_original_owner: PASS")


# ── GET /api/calendar/status ────────────────────────────────────────────

def test_status_requires_authentication() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.get("/api/calendar/status")
    assert resp.status_code == 401
    print("test_status_requires_authentication: PASS")


def test_status_never_returns_a_token() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    with patch("dashboard.server.calendar_store.is_configured", return_value=True), \
         patch("dashboard.server.calendar_store.get_status", return_value={"connected": True, "email": "saroj@example.com"}):
        resp = client.get("/api/calendar/status", headers={"Authorization": f"Bearer {tok}"})
    body = resp.json()
    assert set(body.keys()) == {"connected", "email"}
    assert body == {"connected": True, "email": "saroj@example.com"}
    print("test_status_never_returns_a_token: PASS")


def test_status_scoped_to_the_authenticated_user_only() -> None:
    """User A cannot see User B's Calendar status."""
    server, client, tok_a = _server_with_user("Saroj", "2057")
    resp_b = client.post("/login/username", json={"username": "Bandana", "pin": "2060"})
    tok_b = resp_b.json()["token"]

    def fake_get_status(owner):
        return {"connected": owner == "sana", "email": "sana@example.com" if owner == "sana" else ""}

    with patch("dashboard.server.calendar_store.is_configured", return_value=True), \
         patch("dashboard.server.calendar_store.get_status", side_effect=fake_get_status):
        status_a = client.get("/api/calendar/status", headers={"Authorization": f"Bearer {tok_a}"}).json()
        status_b = client.get("/api/calendar/status", headers={"Authorization": f"Bearer {tok_b}"}).json()

    assert status_a == {"connected": False, "email": ""}
    assert status_b == {"connected": True, "email": "sana@example.com"}
    print("test_status_scoped_to_the_authenticated_user_only: PASS")


# ── POST /api/calendar/disconnect ──────────────────────────────────────

def test_disconnect_requires_authentication() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    resp = client.post("/api/calendar/disconnect")
    assert resp.status_code == 401
    print("test_disconnect_requires_authentication: PASS")


def test_disconnect_only_removes_the_requesting_users_own_credentials() -> None:
    server, client, tok_a = _server_with_user("Saroj", "2057")
    resp_b = client.post("/login/username", json={"username": "Bandana", "pin": "2060"})
    tok_b = resp_b.json()["token"]

    deleted_for = []
    with patch("dashboard.server.calendar_store.is_configured", return_value=True), \
         patch("dashboard.server.calendar_store.load_credentials", return_value=None), \
         patch("dashboard.server.calendar_store.delete_credentials", side_effect=lambda o: deleted_for.append(o)):
        client.post("/api/calendar/disconnect", headers={"Authorization": f"Bearer {tok_a}"})

    assert deleted_for == ["saroj"]   # never "sana"
    print("test_disconnect_only_removes_the_requesting_users_own_credentials: PASS")


def test_disconnect_revoke_failure_does_not_block_local_disconnect() -> None:
    server, client, tok = _server_with_user("Saroj", "2057")
    deleted = []
    with patch("dashboard.server.calendar_store.is_configured", return_value=True), \
         patch("dashboard.server.calendar_store.load_credentials", return_value=('{"token": "x"}', "")), \
         patch("dashboard.server.calendar_auth.credentials_from_json", side_effect=Exception("bad token")), \
         patch("dashboard.server.calendar_store.delete_credentials", side_effect=lambda o: deleted.append(o)):
        resp = client.post("/api/calendar/disconnect", headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert deleted == ["saroj"]
    print("test_disconnect_revoke_failure_does_not_block_local_disconnect: PASS")


def test_disconnect_for_pin_session_is_a_safe_no_op() -> None:
    server = DashboardServer()
    client = TestClient(server.app)
    key = server.new_key()
    tok = client.post("/login", json={"pin": key}).json()["token"]
    resp = client.post("/api/calendar/disconnect", headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    print("test_disconnect_for_pin_session_is_a_safe_no_op: PASS")


if __name__ == "__main__":
    test_auth_google_rejects_unauthenticated()
    test_auth_google_rejects_missing_token()
    test_auth_google_rejects_pin_based_remote_session()
    test_auth_google_returns_503_when_not_configured()
    test_auth_google_missing_env_vars_reported_honestly()
    test_auth_google_success_redirects_to_google_and_stores_state()
    test_auth_google_stores_the_pkce_verifier_alongside_state()
    test_auth_google_validates_return_to_against_cors_allowlist()
    test_callback_success_stores_credentials_and_redirects_connected()
    test_callback_passes_stored_code_verifier_to_exchange_code()
    test_callback_verifier_removed_with_state_cannot_be_reused()
    test_callback_missing_code_verifier_is_a_controlled_failure_not_a_crash()
    test_callback_missing_state_rejected()
    test_callback_unrecognized_state_rejected()
    test_callback_expired_state_rejected()
    test_callback_replayed_state_rejected_second_time()
    test_callback_google_error_param_redirects_cancelled_not_success()
    test_callback_token_exchange_failure_redirects_error_not_success()
    test_callback_never_logs_the_authorization_code_or_token()
    test_stale_callback_from_superseded_identity_still_binds_to_original_owner()
    test_status_requires_authentication()
    test_status_never_returns_a_token()
    test_status_scoped_to_the_authenticated_user_only()
    test_disconnect_requires_authentication()
    test_disconnect_only_removes_the_requesting_users_own_credentials()
    test_disconnect_revoke_failure_does_not_block_local_disconnect()
    test_disconnect_for_pin_session_is_a_safe_no_op()
    print("\nAll calendar-OAuth-route tests passed.")
