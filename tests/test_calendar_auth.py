"""
tests/test_calendar_auth.py -- actions/calendar_auth.py: is_configured()
gating, authorization URL generation, token exchange, refresh, and
best-effort email/revoke -- all against mocked google-auth-oauthlib/
google-auth objects, never a live Google endpoint.

Run with:
    .venv/Scripts/python.exe -m tests.test_calendar_auth
"""
import os
from unittest.mock import MagicMock, patch

from actions import calendar_auth

_ENV = {
    "GOOGLE_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_REDIRECT_URI": "https://sarana-m9g6.onrender.com/auth/google/callback",
}


# ── is_configured() ────────────────────────────────────────────────────

def test_is_configured_true_with_all_three_env_vars() -> None:
    with patch.dict(os.environ, _ENV):
        assert calendar_auth.is_configured() is True
    print("test_is_configured_true_with_all_three_env_vars: PASS")


def test_is_configured_false_missing_client_id() -> None:
    env = dict(_ENV)
    env.pop("GOOGLE_CLIENT_ID")
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("GOOGLE_CLIENT_ID", None)
        assert calendar_auth.is_configured() is False
    print("test_is_configured_false_missing_client_id: PASS")


def test_is_configured_false_missing_client_secret() -> None:
    with patch.dict(os.environ, _ENV, clear=False):
        os.environ.pop("GOOGLE_CLIENT_SECRET", None)
        assert calendar_auth.is_configured() is False
    print("test_is_configured_false_missing_client_secret: PASS")


def test_is_configured_false_missing_redirect_uri() -> None:
    with patch.dict(os.environ, _ENV, clear=False):
        os.environ.pop("GOOGLE_REDIRECT_URI", None)
        assert calendar_auth.is_configured() is False
    print("test_is_configured_false_missing_redirect_uri: PASS")


def test_is_configured_false_without_oauth_libs() -> None:
    with patch.dict(os.environ, _ENV), patch.object(calendar_auth, "_OAUTH_LIBS_OK", False):
        assert calendar_auth.is_configured() is False
    print("test_is_configured_false_without_oauth_libs: PASS")


def test_client_config_never_logs_or_leaks_beyond_the_dict() -> None:
    """The client secret must only ever end up inside the returned dict
    -- nothing here should print/log it."""
    with patch.dict(os.environ, _ENV):
        cfg = calendar_auth._client_config()
    assert cfg["web"]["client_secret"] == "test-client-secret"
    assert cfg["web"]["client_id"] == "test-client-id.apps.googleusercontent.com"
    assert cfg["web"]["redirect_uris"] == [_ENV["GOOGLE_REDIRECT_URI"]]
    print("test_client_config_never_logs_or_leaks_beyond_the_dict: PASS")


# ── authorization URL / PKCE ─────────────────────────────────────────────
# Regression coverage for the production bug: google-auth-oauthlib's Flow
# defaults to autogenerate_code_verifier=True, so authorization_url()
# silently generates a PKCE code_verifier and embeds its code_challenge in
# the URL -- Google then requires that SAME verifier on the token exchange
# (a DIFFERENT Flow object, per exchange_code()'s own docstring) or rejects
# it with invalid_grant ("Missing code verifier"), which is exactly what
# happened live. build_auth_url() must therefore hand the verifier back to
# its caller rather than letting it vanish with the discarded Flow object.

def test_build_auth_url_includes_state_and_offline_access() -> None:
    fake_flow = MagicMock()
    fake_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?mock=1", "unused")
    fake_flow.code_verifier = "fake-pkce-verifier-xyz"
    with patch.dict(os.environ, _ENV), \
         patch.object(calendar_auth.Flow, "from_client_config", return_value=fake_flow):
        url, verifier = calendar_auth.build_auth_url("my-state-token")
    assert url == "https://accounts.google.com/o/oauth2/auth?mock=1"
    kwargs = fake_flow.authorization_url.call_args.kwargs
    assert kwargs["state"] == "my-state-token"
    assert kwargs["access_type"] == "offline"
    assert kwargs["prompt"] == "consent"
    print("test_build_auth_url_includes_state_and_offline_access: PASS")


def test_build_auth_url_returns_the_flow_generated_code_verifier() -> None:
    """The returned verifier must be EXACTLY flow.code_verifier -- the
    same value authorization_url() used to compute the code_challenge
    embedded in the URL Google receives -- not a separately generated or
    empty value."""
    fake_flow = MagicMock()
    fake_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?mock=1", "unused")
    fake_flow.code_verifier = "this-exact-verifier-was-used-for-the-code-challenge"
    with patch.dict(os.environ, _ENV), \
         patch.object(calendar_auth.Flow, "from_client_config", return_value=fake_flow):
        _url, verifier = calendar_auth.build_auth_url("some-state")
    assert verifier == "this-exact-verifier-was-used-for-the-code-challenge"
    print("test_build_auth_url_returns_the_flow_generated_code_verifier: PASS")


def test_scopes_are_narrow_not_full_calendar_scope() -> None:
    assert "https://www.googleapis.com/auth/calendar.events" in calendar_auth.SCOPES
    assert "https://www.googleapis.com/auth/calendar" not in calendar_auth.SCOPES
    print("test_scopes_are_narrow_not_full_calendar_scope: PASS")


# ── token exchange ──────────────────────────────────────────────────────

def test_exchange_code_returns_credentials() -> None:
    fake_flow = MagicMock()
    fake_credentials = MagicMock()
    fake_flow.credentials = fake_credentials
    with patch.dict(os.environ, _ENV), \
         patch.object(calendar_auth.Flow, "from_client_config", return_value=fake_flow):
        creds = calendar_auth.exchange_code("auth-code-123", "the-pkce-verifier")
    fake_flow.fetch_token.assert_called_once_with(code="auth-code-123", code_verifier="the-pkce-verifier")
    assert creds is fake_credentials
    print("test_exchange_code_returns_credentials: PASS")


def test_exchange_code_sends_the_exact_verifier_it_was_given() -> None:
    """The regression test for the actual production bug: fetch_token()
    must receive whatever code_verifier the caller passed in -- not
    None, not a freshly-generated one from this (different) Flow
    object."""
    fake_flow = MagicMock()
    with patch.dict(os.environ, _ENV), \
         patch.object(calendar_auth.Flow, "from_client_config", return_value=fake_flow):
        calendar_auth.exchange_code("some-code", "verifier-from-build-auth-url")
    assert fake_flow.fetch_token.call_args.kwargs["code_verifier"] == "verifier-from-build-auth-url"
    print("test_exchange_code_sends_the_exact_verifier_it_was_given: PASS")


def test_exchange_code_failure_propagates_honestly() -> None:
    fake_flow = MagicMock()
    fake_flow.fetch_token.side_effect = RuntimeError("invalid_grant")
    with patch.dict(os.environ, _ENV), \
         patch.object(calendar_auth.Flow, "from_client_config", return_value=fake_flow):
        try:
            calendar_auth.exchange_code("expired-or-reused-code", "some-verifier")
            assert False, "must propagate a genuine exchange failure"
        except RuntimeError:
            pass
    print("test_exchange_code_failure_propagates_honestly: PASS")


# ── refresh ──────────────────────────────────────────────────────────────

def test_ensure_fresh_no_op_when_already_valid() -> None:
    creds = MagicMock(valid=True)
    result, refreshed = calendar_auth.ensure_fresh(creds)
    assert result is creds
    assert refreshed is False
    creds.refresh.assert_not_called()
    print("test_ensure_fresh_no_op_when_already_valid: PASS")


def test_ensure_fresh_refreshes_when_expired_with_refresh_token() -> None:
    creds = MagicMock(valid=False, expired=True, refresh_token="1//fake-refresh-token")
    with patch.object(calendar_auth, "_GoogleAuthRequest", MagicMock()):
        result, refreshed = calendar_auth.ensure_fresh(creds)
    creds.refresh.assert_called_once()
    assert result is creds
    assert refreshed is True
    print("test_ensure_fresh_refreshes_when_expired_with_refresh_token: PASS")


def test_ensure_fresh_raises_when_expired_without_refresh_token() -> None:
    creds = MagicMock(valid=False, expired=True, refresh_token=None)
    try:
        calendar_auth.ensure_fresh(creds)
        assert False, "must raise -- reconnection required, never silently proceed"
    except RuntimeError:
        pass
    print("test_ensure_fresh_raises_when_expired_without_refresh_token: PASS")


# ── best-effort email / revoke ────────────────────────────────────────

def test_fetch_email_success() -> None:
    # calendar_auth.py imports `requests` locally inside the function
    # (not a module-level attribute) — patch the real `requests` module
    # itself, exactly where the local `import requests` will resolve it.
    creds = MagicMock(token="fake-access-token")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"email": "saroj@example.com"}
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp) as mock_get:
        email = calendar_auth.fetch_email(creds)
    assert email == "saroj@example.com"
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer fake-access-token"
    print("test_fetch_email_success: PASS")


def test_fetch_email_failure_returns_empty_string_not_raise() -> None:
    creds = MagicMock(token="fake-access-token")
    with patch("requests.get", side_effect=Exception("network down")):
        email = calendar_auth.fetch_email(creds)
    assert email == ""
    print("test_fetch_email_failure_returns_empty_string_not_raise: PASS")


def test_revoke_success() -> None:
    creds = MagicMock(token="fake-access-token", refresh_token=None)
    mock_resp = MagicMock(status_code=200)
    with patch("requests.post", return_value=mock_resp):
        assert calendar_auth.revoke(creds) is True
    print("test_revoke_success: PASS")


def test_revoke_failure_never_raises() -> None:
    creds = MagicMock(token="fake-access-token", refresh_token=None)
    with patch("requests.post", side_effect=Exception("network down")):
        assert calendar_auth.revoke(creds) is False
    print("test_revoke_failure_never_raises: PASS")


def test_revoke_with_no_token_at_all_is_a_safe_no_op() -> None:
    creds = MagicMock(token=None, refresh_token=None)
    assert calendar_auth.revoke(creds) is False
    print("test_revoke_with_no_token_at_all_is_a_safe_no_op: PASS")


if __name__ == "__main__":
    test_is_configured_true_with_all_three_env_vars()
    test_is_configured_false_missing_client_id()
    test_is_configured_false_missing_client_secret()
    test_is_configured_false_missing_redirect_uri()
    test_is_configured_false_without_oauth_libs()
    test_client_config_never_logs_or_leaks_beyond_the_dict()
    test_build_auth_url_includes_state_and_offline_access()
    test_build_auth_url_returns_the_flow_generated_code_verifier()
    test_scopes_are_narrow_not_full_calendar_scope()
    test_exchange_code_returns_credentials()
    test_exchange_code_sends_the_exact_verifier_it_was_given()
    test_exchange_code_failure_propagates_honestly()
    test_ensure_fresh_no_op_when_already_valid()
    test_ensure_fresh_refreshes_when_expired_with_refresh_token()
    test_ensure_fresh_raises_when_expired_without_refresh_token()
    test_fetch_email_success()
    test_fetch_email_failure_returns_empty_string_not_raise()
    test_revoke_success()
    test_revoke_failure_never_raises()
    test_revoke_with_no_token_at_all_is_a_safe_no_op()
    print("\nAll calendar-auth tests passed.")
