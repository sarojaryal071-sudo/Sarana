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
from actions.computer_control import _pick_best_match, _new_window_note


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
    print("\nAll computer_control tests passed.")
