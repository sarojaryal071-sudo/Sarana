"""
tests/test_calendar_config_diagnostic.py -- the [CALENDAR_CONFIG] startup
diagnostic in dashboard/server.py's _build_app(): printed exactly once per
process (when DashboardServer() is constructed), and provably never
contains a credential value -- only the five booleans it's meant to report.

Run with:
    .venv/Scripts/python.exe -m tests.test_calendar_config_diagnostic
"""
import contextlib
import io
import os
from unittest.mock import patch

from dashboard.server import DashboardServer

_FAKE_SECRETS = {
    "GOOGLE_CLIENT_ID": "123-fake.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "GOCSPX-fake-super-secret-value",
    "GOOGLE_REDIRECT_URI": "https://sarana-m9g6.onrender.com/auth/google/callback",
}


def _build_and_capture() -> str:
    """Constructs a DashboardServer (which calls _build_app() exactly
    once, per dashboard/server.py:680) and returns everything printed to
    stdout during construction."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        DashboardServer()
    return buf.getvalue()


def _diagnostic_line(output: str) -> str:
    lines = [ln for ln in output.splitlines() if ln.startswith("[CALENDAR_CONFIG]")]
    assert len(lines) == 1, f"expected exactly one [CALENDAR_CONFIG] line, got {len(lines)}: {lines}"
    return lines[0]


# ── never leaks a value ─────────────────────────────────────────────────

def test_diagnostic_never_contains_any_configured_secret_value() -> None:
    """The core requirement: however the booleans come out, none of the
    THREE real env var values (client id, client secret, redirect uri)
    may ever appear as a substring of the printed line."""
    with patch.dict(os.environ, _FAKE_SECRETS):
        output = _build_and_capture()
    line = _diagnostic_line(output)
    for key, value in _FAKE_SECRETS.items():
        assert value not in line, f"{key}'s value leaked into the diagnostic line!"
    print("test_diagnostic_never_contains_any_configured_secret_value: PASS")


def test_diagnostic_never_contains_secret_even_when_unconfigured() -> None:
    """Also true in the (arguably more likely, given the bug report) case
    where the vars are UNSET -- nothing resembling a credential appears."""
    with patch.dict(os.environ, {}, clear=False):
        for key in _FAKE_SECRETS:
            os.environ.pop(key, None)
        output = _build_and_capture()
    line = _diagnostic_line(output)
    assert "GOCSPX" not in line
    assert ".apps.googleusercontent.com" not in line
    print("test_diagnostic_never_contains_secret_even_when_unconfigured: PASS")


# ── reports exactly what it claims to, as booleans ──────────────────────

def test_diagnostic_reports_all_configured() -> None:
    with patch.dict(os.environ, _FAKE_SECRETS), \
         patch("dashboard.server.calendar_auth._OAUTH_LIBS_OK", True), \
         patch("dashboard.server.calendar_store.is_configured", return_value=True):
        output = _build_and_capture()
    line = _diagnostic_line(output)
    assert "oauth_libs_imported=True" in line
    assert "client_id_configured=True" in line
    assert "client_secret_configured=True" in line
    assert "redirect_uri_configured=True" in line
    assert "calendar_store_configured=True" in line
    print("test_diagnostic_reports_all_configured: PASS")


def test_diagnostic_reports_missing_client_secret_only() -> None:
    env = dict(_FAKE_SECRETS)
    env.pop("GOOGLE_CLIENT_SECRET")
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("GOOGLE_CLIENT_SECRET", None)
        with patch("dashboard.server.calendar_auth._OAUTH_LIBS_OK", True), \
             patch("dashboard.server.calendar_store.is_configured", return_value=True):
            output = _build_and_capture()
    line = _diagnostic_line(output)
    assert "client_id_configured=True" in line
    assert "client_secret_configured=False" in line
    assert "redirect_uri_configured=True" in line
    print("test_diagnostic_reports_missing_client_secret_only: PASS")


def test_diagnostic_reports_failed_oauth_lib_import() -> None:
    """The exact scenario this diagnostic exists to catch: env vars all
    present, but google-auth-oauthlib silently failed to import."""
    with patch.dict(os.environ, _FAKE_SECRETS), \
         patch("dashboard.server.calendar_auth._OAUTH_LIBS_OK", False), \
         patch("dashboard.server.calendar_store.is_configured", return_value=True):
        output = _build_and_capture()
    line = _diagnostic_line(output)
    assert "oauth_libs_imported=False" in line
    assert "client_id_configured=True" in line
    print("test_diagnostic_reports_failed_oauth_lib_import: PASS")


def test_diagnostic_reports_calendar_store_unconfigured() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DATABASE_URL", None)
        with patch("dashboard.server.calendar_store.is_configured", return_value=False):
            output = _build_and_capture()
    line = _diagnostic_line(output)
    assert "calendar_store_configured=False" in line
    print("test_diagnostic_reports_calendar_store_unconfigured: PASS")


# ── printed exactly once per process, doesn't change other behavior ─────

def test_diagnostic_printed_exactly_once_per_server_construction() -> None:
    output = _build_and_capture()
    lines = [ln for ln in output.splitlines() if ln.startswith("[CALENDAR_CONFIG]")]
    assert len(lines) == 1
    print("test_diagnostic_printed_exactly_once_per_server_construction: PASS")


def test_building_the_app_still_returns_a_working_fastapi_app() -> None:
    """Sanity check that adding the print didn't disturb app construction
    itself -- routes are still registered normally."""
    server = DashboardServer()
    paths = {route.path for route in server.app.routes}
    assert "/auth/google" in paths
    assert "/auth/google/callback" in paths
    assert "/api/calendar/status" in paths
    assert "/api/calendar/disconnect" in paths
    print("test_building_the_app_still_returns_a_working_fastapi_app: PASS")


if __name__ == "__main__":
    test_diagnostic_never_contains_any_configured_secret_value()
    test_diagnostic_never_contains_secret_even_when_unconfigured()
    test_diagnostic_reports_all_configured()
    test_diagnostic_reports_missing_client_secret_only()
    test_diagnostic_reports_failed_oauth_lib_import()
    test_diagnostic_reports_calendar_store_unconfigured()
    test_diagnostic_printed_exactly_once_per_server_construction()
    test_building_the_app_still_returns_a_working_fastapi_app()
    print("\nAll calendar-config-diagnostic tests passed.")
