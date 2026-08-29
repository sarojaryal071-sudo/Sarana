"""
actions/calendar_auth.py -- Google OAuth 2.0 flow for Calendar access.

Uses Google's own official libraries (google-auth, google-auth-oauthlib)
exactly as instructed -- no hand-rolled OAuth/JWT/signature code. Builds
the OAuth client config programmatically from environment variables
(GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET/GOOGLE_REDIRECT_URI, already
configured on Render) rather than a client_secrets.json file, matching
this project's existing "secrets live in env vars, never in a committed
file" convention (see main.py's _get_api_key()).

Scope: calendar.events only (read/write individual events -- covers every
tool this feature implements: list, create, update, delete, and free-time
search, which is computed from listed events rather than the separate
freebusy API) plus openid/userinfo.email (just enough to show which
Google account is connected in the UI -- see GET /api/calendar/status).
Deliberately NOT the broad `calendar` scope, which would also grant
calendar list/settings/ACL management this feature never needs.

Nothing in this module ever logs a token or client secret.
"""
from __future__ import annotations

import json
import os

try:
    from google_auth_oauthlib.flow import Flow
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as _GoogleAuthRequest
    _OAUTH_LIBS_OK = True
except ImportError:                      # pragma: no cover — optional dependency
    Flow = None
    Credentials = None
    _GoogleAuthRequest = None
    _OAUTH_LIBS_OK = False

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


def is_configured() -> bool:
    """True only when the OAuth libraries are installed AND all three
    Google env vars are set. Callers must treat False as "Calendar OAuth
    unavailable" rather than raising -- see dashboard/server.py's
    /auth/google."""
    return (
        _OAUTH_LIBS_OK
        and bool(os.environ.get("GOOGLE_CLIENT_ID"))
        and bool(os.environ.get("GOOGLE_CLIENT_SECRET"))
        and bool(os.environ.get("GOOGLE_REDIRECT_URI"))
    )


def _client_config() -> dict:
    """Built fresh from env vars on every call -- never cached, never
    written to disk. The "web" key is what google-auth-oauthlib expects
    for a server-side (not installed-app) OAuth client, which is exactly
    what the already-created Google Cloud OAuth Web Client is."""
    return {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.environ["GOOGLE_REDIRECT_URI"]],
        }
    }


def build_auth_url(state: str) -> tuple[str, str]:
    """Builds the Google consent-screen URL for GET /auth/google to
    redirect the browser to. `state` is our OWN CSRF/identity-binding
    token (see dashboard/server.py's _google_oauth_states) -- Google
    echoes it back unchanged to the callback, where we look it up
    ourselves; this module treats it as an opaque string, never
    generates or validates it.

    Returns (auth_url, code_verifier). google-auth-oauthlib's Flow
    defaults to autogenerate_code_verifier=True -- authorization_url()
    below silently generates a PKCE code_verifier and embeds its
    corresponding code_challenge in auth_url, binding this specific
    authorization request to that verifier. Google then REQUIRES the
    same code_verifier on the token exchange (exchange_code() below) or
    rejects it with invalid_grant ("Missing code verifier") -- which is
    exactly what happened in production, because a *different* Flow
    object (with no code_verifier at all) was used for that exchange.
    The verifier must therefore be threaded through to exchange_code()
    by the caller (dashboard/server.py stores it in the same
    _google_oauth_states entry as `state` -- see /auth/google) -- it
    cannot be recovered from `state` or regenerated later; a mismatched
    or missing verifier is rejected by Google the same way a missing one
    is."""
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = os.environ["GOOGLE_REDIRECT_URI"]
    auth_url, _ = flow.authorization_url(
        access_type="offline",        # required to receive a refresh_token
        include_granted_scopes="true",
        prompt="consent",             # ensures a refresh_token is (re)issued even on a repeat connect
        state=state,
    )
    return auth_url, flow.code_verifier


def exchange_code(code: str, code_verifier: str) -> "Credentials":
    """Exchanges an authorization code (from GET /auth/google/callback)
    for real Credentials. `code_verifier` must be the exact value
    build_auth_url() returned for the SAME authorization request (see
    that function's own docstring for why) -- passing it explicitly here
    rather than relying on Flow's own state means this works correctly
    even though this is a brand new Flow object from the one
    build_auth_url() used. Raises on any failure (expired/invalid code,
    missing/wrong verifier, network error) -- callers must not treat a
    failed exchange as success."""
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = os.environ["GOOGLE_REDIRECT_URI"]
    flow.fetch_token(code=code, code_verifier=code_verifier)
    return flow.credentials


def credentials_from_json(credentials_json: str) -> "Credentials":
    """Reconstructs a Credentials object from what
    Credentials.to_json()/actions/calendar_store.py's storage produced."""
    return Credentials.from_authorized_user_info(json.loads(credentials_json), scopes=SCOPES)


def ensure_fresh(credentials: "Credentials") -> tuple["Credentials", bool]:
    """Refreshes `credentials` in place if it's expired (a local check --
    no network call unless actually necessary). Returns (credentials,
    was_refreshed) so the caller knows whether to re-persist the updated
    access token (see main.py's _get_calendar_credentials()). Raises if
    the credentials are invalid and there's no refresh_token to recover
    with -- the user must reconnect."""
    if credentials.valid:
        return credentials, False
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(_GoogleAuthRequest())
        return credentials, True
    raise RuntimeError(
        "Google Calendar credentials are invalid and cannot be refreshed -- reconnection required."
    )


def fetch_email(credentials: "Credentials") -> str:
    """Best-effort only -- returns "" on any failure rather than breaking
    the connect flow over a non-essential display detail (see GET
    /api/calendar/status, the only consumer of this)."""
    try:
        import requests
        resp = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=6,
        )
        resp.raise_for_status()
        return resp.json().get("email", "") or ""
    except Exception:
        return ""


def revoke(credentials: "Credentials") -> bool:
    """Best-effort revocation of Google's own authorization (in addition
    to deleting our locally-stored copy — see actions/calendar_store.py's
    delete_credentials()). Returns True on a confirmed revoke; a failure
    here must never block a local disconnect from succeeding (matches
    the existing project's "an external call failing doesn't trap the
    user" precedent, e.g. App.jsx's own logout handler)."""
    try:
        import requests
        token = credentials.token or credentials.refresh_token
        if not token:
            return False
        resp = requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=6,
        )
        return resp.status_code == 200
    except Exception:
        return False
