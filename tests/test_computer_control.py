"""
tests/test_computer_control.py — JARVIS Mode UI Automation reliability.

Covers the two real, live-reproduced bugs fixed in actions/computer_control.py:

1. _pick_best_match() (the pure matching logic behind ui_find/ui_click/
   ui_type): a naive "first substring match wins" scan could match an
   INCIDENTAL occurrence of the needle buried inside unrelated text (a
   real WhatsApp message preview containing the contact's name
   mid-sentence, appearing earlier in tree-walk order than the actual
   contact) instead of the real target — live-reproduced against a real
   running WhatsApp Desktop window, fixed by preferring a startswith
   match over a substring-anywhere match.

2. _new_window_note() (the generic post-click "did something unexpected
   pop up" signal): a real, live-reproduced case where an unrelated modal
   dialog silently absorbed a click that was otherwise correctly targeted
   and physically performed (right element found, right coordinate
   clicked, no exception raised) — "no exception" alone is not evidence
   the click actually did what was intended.

These are tested with plain fake objects (anything with .window_text())
rather than a real pywinauto/UIA session — no live desktop needed, no
screenshots, no window manipulation. See computer_control.py's own
docstrings for the live evidence these fixes are based on.

Run with:
    .venv/Scripts/python.exe -m tests.test_computer_control
"""
from actions.computer_control import (
    _pick_best_match, _new_window_note,
    _classify_click_result, _classify_type_result, _screen_changed,
    VERIFY_TAG_SUCCESS, VERIFY_TAG_AMBIGUOUS, VERIFY_TAG_NO_CHANGE,
    TYPE_TAG_SUCCESS, TYPE_TAG_FAILURE, TYPE_TAG_AMBIGUOUS,
    INCONCLUSIVE_TAGS,
)


class _FakeCtrl:
    def __init__(self, name):
        self._name = name

    def window_text(self):
        return self._name

    def __repr__(self):
        return f"_FakeCtrl({self._name!r})"


class _RaisingCtrl:
    """A control whose window_text() raises — _pick_best_match must skip
    it rather than crash (a real pywinauto element can go stale/raise
    mid-walk if the UI redraws during a scan)."""

    def window_text(self):
        raise RuntimeError("element went stale")


# ── _pick_best_match: the real WhatsApp bug, reproduced with fakes ───────

def test_startswith_match_wins_over_earlier_incidental_substring_match() -> None:
    # Mirrors the EXACT real bug: an unrelated conversation's message
    # preview ("...Bro it's me saroj") appears BEFORE the real contact's
    # own entry ("Saroj Thursday") in tree-walk order.
    wrong = _FakeCtrl("+358 40 7483585 Saturday Bro it's me saroj")
    right = _FakeCtrl("Saroj Thursday")
    candidates = [wrong, right]
    result = _pick_best_match("Saroj", candidates)
    assert result is right, f"expected the real contact, got {result!r}"
    print("test_startswith_match_wins_over_earlier_incidental_substring_match: PASS")


def test_exact_match_wins_immediately_regardless_of_order() -> None:
    decoy = _FakeCtrl("Saroj Thursday Photo — New messages will disappear")
    exact = _FakeCtrl("Saroj")
    result = _pick_best_match("Saroj", [decoy, exact])
    assert result is exact
    print("test_exact_match_wins_immediately_regardless_of_order: PASS")


def test_case_insensitive_matching() -> None:
    ctrl = _FakeCtrl("SAROJ Thursday")
    assert _pick_best_match("saroj", [ctrl]) is ctrl
    assert _pick_best_match("Saroj", [ctrl]) is ctrl
    print("test_case_insensitive_matching: PASS")


def test_shortest_startswith_match_preferred_over_a_longer_one() -> None:
    # Two legitimate startswith matches for the same contact (a real
    # pattern observed live — WhatsApp exposes both an outer container
    # AND a nested "name + timestamp" label, both starting with the
    # contact's name) — prefer the shorter, more precise one.
    long_container = _FakeCtrl("Saroj Thursday Photo New messages will disappear from this chat")
    short_label = _FakeCtrl("Saroj Thursday")
    result = _pick_best_match("Saroj", [long_container, short_label])
    assert result is short_label
    print("test_shortest_startswith_match_preferred_over_a_longer_one: PASS")


def test_falls_back_to_substring_only_when_no_startswith_match_exists() -> None:
    only_substring = _FakeCtrl("Message from Saroj's friend")
    result = _pick_best_match("Saroj", [only_substring])
    assert result is only_substring
    print("test_falls_back_to_substring_only_when_no_startswith_match_exists: PASS")


def test_no_match_returns_none() -> None:
    assert _pick_best_match("Nobody Here", [_FakeCtrl("Ankit"), _FakeCtrl("Hikaru")]) is None
    print("test_no_match_returns_none: PASS")


def test_empty_description_returns_none() -> None:
    assert _pick_best_match("", [_FakeCtrl("Ankit")]) is None
    assert _pick_best_match("   ", [_FakeCtrl("Ankit")]) is None
    print("test_empty_description_returns_none: PASS")


def test_blank_and_stale_candidates_are_skipped_not_fatal() -> None:
    blank = _FakeCtrl("")
    stale = _RaisingCtrl()
    real = _FakeCtrl("Saroj")
    result = _pick_best_match("Saroj", [blank, stale, real])
    assert result is real
    print("test_blank_and_stale_candidates_are_skipped_not_fatal: PASS")


def test_max_candidates_bound_is_respected() -> None:
    # The real target sits just past the cap — must NOT be found; proves
    # the loop genuinely stops rather than silently scanning everything
    # (see _UI_FIND_MAX_CANDIDATES's own docstring on why this exists).
    candidates = [_FakeCtrl(f"filler {i}") for i in range(5)] + [_FakeCtrl("Target")]
    assert _pick_best_match("Target", candidates, max_candidates=5) is None
    assert _pick_best_match("Target", candidates, max_candidates=6) is not None
    print("test_max_candidates_bound_is_respected: PASS")


# ── _new_window_note: the real Notepad dialog-swallowed-click bug ────────

def test_new_window_note_fires_when_a_new_window_appears() -> None:
    before = {"Untitled - Notepad", "Chrome"}
    after = {"Untitled - Notepad", "Chrome", "Notepad"}
    note = _new_window_note(before, after)
    assert "Notepad" in note
    assert "dialog" in note.lower()
    print("test_new_window_note_fires_when_a_new_window_appears: PASS")


def test_new_window_note_is_empty_when_nothing_changed() -> None:
    same = {"Untitled - Notepad", "Chrome"}
    assert _new_window_note(same, same) == ""
    print("test_new_window_note_is_empty_when_nothing_changed: PASS")


def test_new_window_note_ignores_blank_titles() -> None:
    before = {"Notepad"}
    after = {"Notepad", ""}
    assert _new_window_note(before, after) == ""
    print("test_new_window_note_ignores_blank_titles: PASS")


def test_new_window_note_lists_multiple_new_windows() -> None:
    before = set()
    after = {"Dialog A", "Dialog B"}
    note = _new_window_note(before, after)
    assert "Dialog A" in note and "Dialog B" in note
    print("test_new_window_note_lists_multiple_new_windows: PASS")


# ── _classify_click_result / _classify_type_result: automatic local ─────
#   click/type verification (see actions/computer_control.py's own module
#   docstring for the four-tier design this implements). Pure functions,
#   tested with plain dicts shaped like _snapshot()'s real output — no
#   live UI needed.

def _snap(active_title="App", top_level=None, alive=True, toggle=None, selected=None, screen=None):
    return {
        "active_title": active_title,
        "top_level": top_level if top_level is not None else {"App"},
        "ctrl": {"alive": alive, "toggle": toggle, "selected": selected},
        "screen": screen,
    }


def test_click_new_dialog_is_ambiguous_not_silently_success_or_failure() -> None:
    before = _snap(top_level={"App"})
    after = _snap(top_level={"App", "Error — file not found"})
    tag, reason = _classify_click_result("Connect", before, after)
    assert tag == VERIFY_TAG_AMBIGUOUS
    assert "new" in reason.lower() or "window" in reason.lower()
    print("test_click_new_dialog_is_ambiguous_not_silently_success_or_failure: PASS")


def test_click_active_window_change_matching_target_is_success() -> None:
    before = _snap(active_title="WhatsApp")
    after = _snap(active_title="Saroj Thursday - WhatsApp")
    tag, reason = _classify_click_result("Saroj Thursday", before, after)
    assert tag == VERIFY_TAG_SUCCESS
    print("test_click_active_window_change_matching_target_is_success: PASS")


def test_click_active_window_change_not_matching_target_is_ambiguous() -> None:
    before = _snap(active_title="WhatsApp")
    after = _snap(active_title="Some Other Chat - WhatsApp")
    tag, _ = _classify_click_result("Saroj Thursday", before, after)
    assert tag == VERIFY_TAG_AMBIGUOUS
    print("test_click_active_window_change_not_matching_target_is_ambiguous: PASS")


def test_click_target_control_going_stale_is_success() -> None:
    # The clicked list-item control itself vanishing from the tree — a
    # real pattern for list-navigation UIs (WhatsApp's conversation list)
    # where the click causes the surrounding view to rebuild.
    before = _snap(alive=True)
    after = _snap(alive=False)
    tag, _ = _classify_click_result("Saroj Thursday", before, after)
    assert tag == VERIFY_TAG_SUCCESS
    print("test_click_target_control_going_stale_is_success: PASS")


def test_click_toggle_state_change_is_success() -> None:
    before = _snap(toggle="off")
    after = _snap(toggle="on")
    tag, _ = _classify_click_result("Bluetooth", before, after)
    assert tag == VERIFY_TAG_SUCCESS
    print("test_click_toggle_state_change_is_success: PASS")


def test_click_selection_state_change_is_success() -> None:
    before = _snap(selected=False)
    after = _snap(selected=True)
    tag, _ = _classify_click_result("My Headphones", before, after)
    assert tag == VERIFY_TAG_SUCCESS
    print("test_click_selection_state_change_is_success: PASS")


def test_click_nothing_detectable_is_no_observable_change() -> None:
    before = _snap()
    after = _snap()
    tag, _ = _classify_click_result("Something", before, after)
    assert tag == VERIFY_TAG_NO_CHANGE
    print("test_click_nothing_detectable_is_no_observable_change: PASS")


def test_click_screen_pixels_changed_alone_is_ambiguous() -> None:
    before = _snap(screen=tuple([0] * 384))
    after = _snap(screen=tuple([255] * 384))
    tag, _ = _classify_click_result("Something", before, after)
    assert tag == VERIFY_TAG_AMBIGUOUS
    print("test_click_screen_pixels_changed_alone_is_ambiguous: PASS")


def test_screen_changed_detects_real_difference() -> None:
    before = tuple([0] * 384)
    after = tuple([255] * 384)
    assert _screen_changed(before, after) is True
    print("test_screen_changed_detects_real_difference: PASS")


def test_screen_changed_ignores_tiny_noise() -> None:
    before = tuple([100] * 384)
    after = tuple([101] * 384)  # within the abs(a-b) > 20 per-pixel threshold
    assert _screen_changed(before, after) is False
    print("test_screen_changed_ignores_tiny_noise: PASS")


def test_screen_changed_returns_none_when_uncomparable() -> None:
    assert _screen_changed(None, (1, 2, 3)) is None
    assert _screen_changed((1, 2), (1, 2, 3)) is None
    print("test_screen_changed_returns_none_when_uncomparable: PASS")


def test_type_matching_text_is_success() -> None:
    tag, _ = _classify_type_result("hello world", "", "hello world")
    assert tag == TYPE_TAG_SUCCESS
    print("test_type_matching_text_is_success: PASS")


def test_type_unreadable_field_is_ambiguous_not_failure() -> None:
    # e.g. a masked password field — must NOT be reported as a false FAILURE.
    tag, _ = _classify_type_result("hunter2", None, None)
    assert tag == TYPE_TAG_AMBIGUOUS
    print("test_type_unreadable_field_is_ambiguous_not_failure: PASS")


def test_type_unchanged_field_is_failure() -> None:
    tag, _ = _classify_type_result("hello", "", "")
    assert tag == TYPE_TAG_FAILURE
    print("test_type_unchanged_field_is_failure: PASS")


def test_type_changed_but_not_matching_is_ambiguous() -> None:
    tag, _ = _classify_type_result("hello", "", "xyz")
    assert tag == TYPE_TAG_AMBIGUOUS
    print("test_type_changed_but_not_matching_is_ambiguous: PASS")


def test_type_reason_never_echoes_typed_or_readback_content() -> None:
    # Privacy: the classifier's reason text must never leak the actual
    # typed/read-back content, even truncated.
    secret = "super-secret-token-xyz123"
    for before_v, after_v in [("", secret), (None, None), ("", "")]:
        _, reason = _classify_type_result(secret, before_v, after_v)
        assert secret not in reason
    print("test_type_reason_never_echoes_typed_or_readback_content: PASS")


def test_inconclusive_tags_cover_ambiguous_and_no_change_and_type_failure() -> None:
    assert VERIFY_TAG_AMBIGUOUS in INCONCLUSIVE_TAGS
    assert VERIFY_TAG_NO_CHANGE in INCONCLUSIVE_TAGS
    assert TYPE_TAG_AMBIGUOUS in INCONCLUSIVE_TAGS
    assert TYPE_TAG_FAILURE in INCONCLUSIVE_TAGS
    assert VERIFY_TAG_SUCCESS not in INCONCLUSIVE_TAGS
    assert TYPE_TAG_SUCCESS not in INCONCLUSIVE_TAGS
    print("test_inconclusive_tags_cover_ambiguous_and_no_change_and_type_failure: PASS")


if __name__ == "__main__":
    test_startswith_match_wins_over_earlier_incidental_substring_match()
    test_exact_match_wins_immediately_regardless_of_order()
    test_case_insensitive_matching()
    test_shortest_startswith_match_preferred_over_a_longer_one()
    test_falls_back_to_substring_only_when_no_startswith_match_exists()
    test_no_match_returns_none()
    test_empty_description_returns_none()
    test_blank_and_stale_candidates_are_skipped_not_fatal()
    test_max_candidates_bound_is_respected()
    test_new_window_note_fires_when_a_new_window_appears()
    test_new_window_note_is_empty_when_nothing_changed()
    test_new_window_note_ignores_blank_titles()
    test_new_window_note_lists_multiple_new_windows()
    test_click_new_dialog_is_ambiguous_not_silently_success_or_failure()
    test_click_active_window_change_matching_target_is_success()
    test_click_active_window_change_not_matching_target_is_ambiguous()
    test_click_target_control_going_stale_is_success()
    test_click_toggle_state_change_is_success()
    test_click_selection_state_change_is_success()
    test_click_nothing_detectable_is_no_observable_change()
    test_click_screen_pixels_changed_alone_is_ambiguous()
    test_screen_changed_detects_real_difference()
    test_screen_changed_ignores_tiny_noise()
    test_screen_changed_returns_none_when_uncomparable()
    test_type_matching_text_is_success()
    test_type_unreadable_field_is_ambiguous_not_failure()
    test_type_unchanged_field_is_failure()
    test_type_changed_but_not_matching_is_ambiguous()
    test_type_reason_never_echoes_typed_or_readback_content()
    test_inconclusive_tags_cover_ambiguous_and_no_change_and_type_failure()
    print("\nAll computer_control tests passed.")
