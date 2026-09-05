"""
tests/test_open_app.py — J2 (Universal Actions) of the locked JARVIS
roadmap: open_app() migrated onto the general controller
(computer_control.py's get_active_window_title(), reused unchanged) and
now returns a real Result Envelope instead of a bare "Opened X." string.

Per this project's own established convention: no test here launches a
real application — the OS-specific launcher functions and
get_active_window_title() are always mocked. What's verified is that
open_app() correctly distinguishes "a launch command was issued" from
"the app is actually confirmed open", and never fabricates
VERIFIED_SUCCESS from the former alone.

Run with:
    .venv/Scripts/python.exe -m tests.test_open_app
"""
from unittest.mock import patch, MagicMock

import actions.open_app as oa


def _launcher(**overrides):
    """Same dict-capture pitfall as _HANDLERS/_OS_LAUNCHERS elsewhere in
    this project — patch.dict on the dict ENTRY for THIS machine's real
    _SYSTEM, not patch.object on a bare function name."""
    return patch.dict(oa._OS_LAUNCHERS, overrides)


# ── _window_confirms_app: the real verification signal ──────────────────

def test_window_confirms_app_matches_full_name_substring() -> None:
    assert oa._window_confirms_app("New Tab - Google Chrome", "Chrome") is True
    assert oa._window_confirms_app("Untitled - Notepad", "Notepad") is True
    print("test_window_confirms_app_matches_full_name_substring: PASS")


def test_window_confirms_app_matches_first_significant_word() -> None:
    assert oa._window_confirms_app("Welcome - Visual Studio Code", "Visual Studio Code") is True
    print("test_window_confirms_app_matches_first_significant_word: PASS")


def test_window_confirms_app_rejects_an_unrelated_title() -> None:
    assert oa._window_confirms_app("Calculator", "Spotify") is False
    assert oa._window_confirms_app("", "Spotify") is False
    print("test_window_confirms_app_rejects_an_unrelated_title: PASS")


# ── open_app(): honest Result Envelope classification ───────────────────

def test_open_app_verified_success_when_window_title_confirms_it() -> None:
    with _launcher(**{oa._SYSTEM: MagicMock(return_value=True)}), \
         patch.object(oa, "get_active_window_title", side_effect=["", "Untitled - Notepad"]), \
         patch.object(oa.time, "sleep"):
        result = oa.open_app(parameters={"app_name": "Notepad"})
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "Notepad" in result
    print("test_open_app_verified_success_when_window_title_confirms_it: PASS")


def test_open_app_inconclusive_when_launch_succeeds_but_title_does_not_confirm() -> None:
    with _launcher(**{oa._SYSTEM: MagicMock(return_value=True)}), \
         patch.object(oa, "get_active_window_title", side_effect=["Explorer", "Explorer"]), \
         patch.object(oa.time, "sleep"):
        result = oa.open_app(parameters={"app_name": "Spotify"})
    assert result.startswith("[INCONCLUSIVE]")
    print("test_open_app_inconclusive_when_launch_succeeds_but_title_does_not_confirm: PASS")


def test_open_app_verified_failure_when_no_launch_method_succeeds() -> None:
    m_launcher = MagicMock(return_value=False)
    with _launcher(**{oa._SYSTEM: m_launcher}), \
         patch.object(oa, "get_active_window_title", return_value=""):
        result = oa.open_app(parameters={"app_name": "TotallyFakeApp12345"})
    assert result.startswith("[VERIFIED_FAILURE]")
    assert m_launcher.call_count >= 1
    print("test_open_app_verified_failure_when_no_launch_method_succeeds: PASS")


def test_open_app_verified_failure_on_exception() -> None:
    with _launcher(**{oa._SYSTEM: MagicMock(side_effect=RuntimeError("boom"))}):
        result = oa.open_app(parameters={"app_name": "Chrome"})
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "boom" in result
    print("test_open_app_verified_failure_on_exception: PASS")


def test_open_app_no_app_name_is_inconclusive_and_launches_nothing() -> None:
    m_launcher = MagicMock()
    with _launcher(**{oa._SYSTEM: m_launcher}):
        result = oa.open_app(parameters={"app_name": ""})
    m_launcher.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_open_app_no_app_name_is_inconclusive_and_launches_nothing: PASS")


def test_open_app_unsupported_os_is_verified_failure() -> None:
    with patch.object(oa, "_SYSTEM", "PlayStation5"):
        result = oa.open_app(parameters={"app_name": "Chrome"})
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_open_app_unsupported_os_is_verified_failure: PASS")


def _run() -> None:
    test_window_confirms_app_matches_full_name_substring()
    test_window_confirms_app_matches_first_significant_word()
    test_window_confirms_app_rejects_an_unrelated_title()
    test_open_app_verified_success_when_window_title_confirms_it()
    test_open_app_inconclusive_when_launch_succeeds_but_title_does_not_confirm()
    test_open_app_verified_failure_when_no_launch_method_succeeds()
    test_open_app_verified_failure_on_exception()
    test_open_app_no_app_name_is_inconclusive_and_launches_nothing()
    test_open_app_unsupported_os_is_verified_failure()
    print("\nAll open_app tests passed.")


if __name__ == "__main__":
    _run()
