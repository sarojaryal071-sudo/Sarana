"""
tests/test_accomplish.py — actions/computer_control.py's accomplish():
the single goal-oriented entry point for Tier 3-6 "make this application
show some state" requests (see the Universal JARVIS Computer Control
Architecture design).

Testing approach: mock the REAL UIA boundary (_ui_find — the function
that actually walks pywinauto's tree) with a controllable fake, but let
the REAL classification machinery (_snapshot, _control_signature,
_classify_click_result, _classify_type_result) run unmocked against
plain fake control objects — i.e. mock the observation mechanism, not
merely accomplish()'s final return value, per this project's own testing
principle ("no exception" must never silently become VERIFIED_SUCCESS).

Run with:
    .venv/Scripts/python.exe -m tests.test_accomplish
"""
import inspect
import re
from contextlib import contextmanager
from unittest.mock import patch

import actions.computer_control as cc
from actions.computer_control import accomplish


class _FakeCtrl:
    """A fake pywinauto control with just enough surface for accomplish()
    to run its REAL logic against: window_text/rectangle (used by
    _control_signature/_snapshot), toggle/selection state, is_enabled
    (the pre-action re-check), and click_input/set_text (the real
    action)."""

    def __init__(self, name, toggle=None, selected=None, enabled=True, raise_on_click=False):
        self._name = name
        self._toggle = toggle
        self._selected = selected
        self._enabled = enabled
        self._raise_on_click = raise_on_click
        self.click_count = 0

    def window_text(self):
        return self._name

    def get_toggle_state(self):
        if self._toggle is None:
            raise RuntimeError("no toggle pattern on this control")
        return self._toggle

    def is_selected(self):
        if self._selected is None:
            raise RuntimeError("no selection pattern on this control")
        return self._selected

    def is_enabled(self):
        return self._enabled

    def rectangle(self):
        R = type("R", (), {})()
        R.left, R.top, R.right, R.bottom = (0, 0, 10, 10)
        return R

    def click_input(self):
        self.click_count += 1
        if self._raise_on_click:
            raise RuntimeError("simulated click failure")
        if self._toggle is not None:
            self._toggle = 0 if self._toggle else 1
        if self._selected is not None:
            self._selected = True

    def set_text(self, text):
        self._name = text


@contextmanager
def _patched():
    """Common patch set: get_active_window_title/top-level windows/screen
    signature held constant (so only the CONTROL's own state drives the
    classification in most tests), _settle_poll made instant (its own
    timing behavior is tested separately, in test_computer_control.py's
    module — no need to re-test it here, just not slow these tests down)."""
    with patch.object(cc, "get_active_window_title", return_value="TestApp"), \
         patch.object(cc, "_top_level_window_titles", return_value={"TestApp"}), \
         patch.object(cc, "_screen_signature", return_value=None), \
         patch.object(cc, "_settle_poll", lambda read_fn, **kw: read_fn()):
        yield


def test_unique_target_click_success_via_toggle_change() -> None:
    ctrl = _FakeCtrl("Bluetooth", toggle=0)
    with patch.object(cc, "_ui_find", return_value=("found", ctrl, "exact match")), \
         _patched():
        result = accomplish(goal="turn on the toggle", target="Bluetooth")
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    assert ctrl.click_count == 1
    print("test_unique_target_click_success_via_toggle_change: PASS")


def test_ambiguous_target_returns_ui_ambiguous_and_never_clicks() -> None:
    with patch.object(cc, "_ui_find", return_value=("ambiguous", None, "2 distinct elements match")) as m:
        result = accomplish(goal="click Saroj", target="Saroj")
    assert result.startswith("[UI_AMBIGUOUS]"), result
    assert "do not guess" in result.lower()
    m.assert_called_once()
    print("test_ambiguous_target_returns_ui_ambiguous_and_never_clicks: PASS")


def test_not_found_target_returns_inconclusive() -> None:
    with patch.object(cc, "_ui_find", return_value=("not_found", None, "no element matches")):
        result = accomplish(goal="open the Foo tab", target="Foo")
    assert result.startswith("[INCONCLUSIVE]"), result
    print("test_not_found_target_returns_inconclusive: PASS")


def test_disabled_target_is_caught_by_pre_action_recheck_never_clicked() -> None:
    ctrl = _FakeCtrl("Connect", enabled=False)
    with patch.object(cc, "_ui_find", return_value=("found", ctrl, "exact match")):
        result = accomplish(goal="connect the device", target="Connect")
    assert result.startswith("[INCONCLUSIVE]"), result
    assert "disabled" in result.lower()
    assert ctrl.click_count == 0, "a disabled target must never actually be clicked"
    print("test_disabled_target_is_caught_by_pre_action_recheck_never_clicked: PASS")


def test_consequential_goal_without_confirmation_never_reaches_ui_find() -> None:
    with patch.object(cc, "_ui_find") as m:
        result = accomplish(goal="delete this file", target="report.docx")
    assert result.startswith("[CONFIRMATION_REQUIRED]"), result
    assert "confirmed=true" in result
    m.assert_not_called()
    print("test_consequential_goal_without_confirmation_never_reaches_ui_find: PASS")


def test_consequential_goal_with_confirmed_proceeds() -> None:
    ctrl = _FakeCtrl("Delete", selected=False)
    with patch.object(cc, "_ui_find", return_value=("found", ctrl, "exact match")), \
         _patched():
        result = accomplish(goal="delete this file", target="Delete", confirmed=True)
    assert not result.startswith("[CONFIRMATION_REQUIRED]"), result
    assert ctrl.click_count == 1
    print("test_consequential_goal_with_confirmed_proceeds: PASS")


def test_click_raising_exception_is_verified_failure_never_success() -> None:
    ctrl = _FakeCtrl("Connect", raise_on_click=True)
    with patch.object(cc, "_ui_find", return_value=("found", ctrl, "exact match")):
        result = accomplish(goal="connect", target="Connect")
    assert result.startswith("[VERIFIED_FAILURE]"), result
    print("test_click_raising_exception_is_verified_failure_never_success: PASS")


def test_typing_success_via_real_read_back() -> None:
    ctrl = _FakeCtrl("")  # an empty edit field
    with patch.object(cc, "_ui_find", return_value=("found", ctrl, "exact match")), \
         _patched():
        result = accomplish(goal="type the title", target="Title field", text="Marketing Plan")
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    assert ctrl._name == "Marketing Plan"
    # Privacy: the typed content itself must never be echoed in the result.
    assert "Marketing Plan" not in result
    print("test_typing_success_via_real_read_back: PASS")


def test_no_observable_change_without_target_stays_inconclusive() -> None:
    # A control with no toggle/selection pattern and a title that never
    # changes — genuinely nothing for local signals to detect (the real
    # Calculator-button scenario from the prior arc). No `target` given,
    # so the expected_state recheck must not fire either.
    ctrl = _FakeCtrl("Seven")
    with patch.object(cc, "_ui_find", return_value=("found", ctrl, "exact match")) as m, \
         _patched():
        result = accomplish(goal="press seven")
    assert result.startswith("[INCONCLUSIVE]"), result
    assert m.call_count == 1, "no target was given, so no expected_state recheck should fire"
    print("test_no_observable_change_without_target_stays_inconclusive: PASS")


def test_expected_state_recheck_upgrades_no_change_to_verified_success() -> None:
    # First _ui_find call (initial resolution) returns a startswith match
    # (ambiguous-ish precision); the click itself produces no local
    # signal; the SECOND _ui_find call (the post-action recheck) now
    # resolves as an EXACT match — the real, live-observed WhatsApp
    # signal (a header showing the contact's name exactly, vs. a longer
    # compound sidebar label before the click).
    ctrl = _FakeCtrl("Saroj Thursday")
    calls = [
        ("found", ctrl, "startswith match"),
        ("found", ctrl, "exact match"),
    ]
    with patch.object(cc, "_ui_find", side_effect=calls) as m, \
         _patched():
        result = accomplish(
            goal="open the conversation with Saroj",
            target="Saroj",
            expected_state="the conversation header shows Saroj",
        )
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    assert "exact match" in result
    assert m.call_count == 2
    print("test_expected_state_recheck_upgrades_no_change_to_verified_success: PASS")


def test_expected_state_recheck_that_still_does_not_match_stays_inconclusive() -> None:
    ctrl = _FakeCtrl("Saroj Thursday")
    calls = [
        ("found", ctrl, "startswith match"),
        ("found", ctrl, "startswith match"),  # recheck still not an exact match
    ]
    with patch.object(cc, "_ui_find", side_effect=calls), \
         _patched():
        result = accomplish(goal="open Saroj's chat", target="Saroj", expected_state="header shows Saroj")
    assert result.startswith("[INCONCLUSIVE]"), result
    print("test_expected_state_recheck_that_still_does_not_match_stays_inconclusive: PASS")


def test_constraints_supplies_an_implicit_control_type() -> None:
    captured = {}

    def _fake_ui_find(term, control_type=None):
        captured["control_type"] = control_type
        return ("not_found", None, "no match")

    with patch.object(cc, "_ui_find", side_effect=_fake_ui_find):
        accomplish(goal="click Downloads", target="Downloads", constraints="it's a listitem in the sidebar")
    assert captured["control_type"] == "listitem"
    print("test_constraints_supplies_an_implicit_control_type: PASS")


def test_no_goal_or_target_is_inconclusive_not_a_crash() -> None:
    result = accomplish(goal="")
    assert result.startswith("[INCONCLUSIVE]"), result
    print("test_no_goal_or_target_is_inconclusive_not_a_crash: PASS")


# ── genericity: no application-name routing logic ──────────────────────

_APP_NAME_LITERALS = (
    "whatsapp", "facebook", "word", "excel", "outlook", "telegram",
    "discord", "slack", "spotify", "chrome", "firefox", "instagram",
)


def test_accomplish_source_contains_no_application_name_literals() -> None:
    src = inspect.getsource(accomplish).lower()
    # Check for the app name as an actual QUOTED STRING LITERAL — the
    # real shape application-name routing logic would take (e.g.
    # if "word" in goal.lower(): ...). A bare word-boundary match isn't
    # precise enough: this function legitimately has a loop variable and
    # ordinary prose that can contain these tokens unquoted without being
    # app-specific routing (e.g. "in the user's own words") — those must
    # not fail this check; only an actual string literal comparison would.
    hits = [name for name in _APP_NAME_LITERALS if re.search(rf"['\"]{name}['\"]", src)]
    assert not hits, f"accomplish() must stay application-agnostic — found quoted literal(s): {hits}"
    print("test_accomplish_source_contains_no_application_name_literals: PASS")


if __name__ == "__main__":
    test_unique_target_click_success_via_toggle_change()
    test_ambiguous_target_returns_ui_ambiguous_and_never_clicks()
    test_not_found_target_returns_inconclusive()
    test_disabled_target_is_caught_by_pre_action_recheck_never_clicked()
    test_consequential_goal_without_confirmation_never_reaches_ui_find()
    test_consequential_goal_with_confirmed_proceeds()
    test_click_raising_exception_is_verified_failure_never_success()
    test_typing_success_via_real_read_back()
    test_no_observable_change_without_target_stays_inconclusive()
    test_expected_state_recheck_upgrades_no_change_to_verified_success()
    test_expected_state_recheck_that_still_does_not_match_stays_inconclusive()
    test_constraints_supplies_an_implicit_control_type()
    test_no_goal_or_target_is_inconclusive_not_a_crash()
    test_accomplish_source_contains_no_application_name_literals()
    print("\nAll accomplish() tests passed.")
