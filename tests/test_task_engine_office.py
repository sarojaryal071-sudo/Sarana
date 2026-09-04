"""
tests/test_task_engine_office.py — Phase 4 (Office Capability) of the
JARVIS execution-architecture mission.

Covers the new "office" APPLICATION-family domain added to
actions/task_engine.py — routing (including the specific collisions
against browser/youtube the mission called out by name), family
classification, the deterministic objective->office_control() parameter
parser, real execution against the EXISTING office_control.py (never
duplicated), honest result classification, and that no artificial
office recovery entry was invented.

Per this project's own established convention: office_control() is
ALWAYS mocked here — no test actually touches a real running Word/Excel
instance. Office has no separate real-machine verification file (unlike
Phase 3's system_shortcut queries) because every real office_control()
path requires a live, already-open Word/Excel document/workbook to be
meaningful — there is nothing safe to assert about a fresh CI/dev
machine's state the way "read the real disk space" was for Phase 3.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_office
"""
from unittest.mock import patch, MagicMock

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    """Same dict-capture pitfall as _HANDLERS elsewhere in this project —
    patch.dict on the dict ENTRIES, not patch.object on the bare _run_*
    names."""
    return patch.dict(te._HANDLERS, overrides)


# ── Routing ──────────────────────────────────────────────────────────

def test_route_matches_office_for_word_and_excel_content_objectives() -> None:
    assert te.route("write hello world in the document") == "office"
    assert te.route("replace foo with bar in the document") == "office"
    assert te.route("make this bold") == "office"
    assert te.route("set cell A1 to 5") == "office"
    assert te.route("what is in cell B2") == "office"
    assert te.route("save this document") == "office"
    print("test_route_matches_office_for_word_and_excel_content_objectives: PASS")


def test_route_office_family_is_application() -> None:
    assert te.family_of("office") == te.FAMILY_APPLICATION
    print("test_route_office_family_is_application: PASS")


# ── Routing collisions — the specific cases the Phase 4 mission named ──

def test_route_open_word_and_open_excel_resolve_to_office_not_browser() -> None:
    # "open Word"/"open Excel" tie 1-1 against browser's own "open"
    # keyword; office is declared BEFORE browser in _DOMAINS specifically
    # so this tie resolves to office, not a generic browser search.
    assert te.route("open Word") == "office"
    assert te.route("open Excel") == "office"
    assert te.route("open an Excel spreadsheet") == "office"
    assert te.route("create a Word document") == "office"
    print("test_route_open_word_and_open_excel_resolve_to_office_not_browser: PASS")


def test_route_open_powerpoint_is_honestly_not_office() -> None:
    # office_control.py has NO PowerPoint support (Word/Excel only) — the
    # "office" domain deliberately does not claim "powerpoint" as a
    # keyword, so this objective is NOT misrouted into a capability that
    # doesn't exist. It falls to browser (via the generic "open"
    # keyword) instead — a real, disclosed limitation, not a bug this
    # phase fixes (see docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md).
    assert te.route("open PowerPoint") != "office"
    print("test_route_open_powerpoint_is_honestly_not_office: PASS")


def test_route_browser_and_youtube_objectives_unaffected_by_office_addition() -> None:
    assert te.route("search for something in the browser") == "browser"
    assert te.route("open a webpage") == "browser"
    assert te.route("open YouTube") == "youtube"
    assert te.route("play a YouTube video") == "youtube"
    print("test_route_browser_and_youtube_objectives_unaffected_by_office_addition: PASS")


def test_route_system_domains_unaffected_by_office_addition() -> None:
    assert te.route("set my volume to 40 percent") == "system_volume"
    assert te.route("check my battery status") == "system_shortcut"
    print("test_route_system_domains_unaffected_by_office_addition: PASS")


# ── Objective parsing — the deterministic (objective -> office_control()
#    params) extraction, never a second LLM call ─────────────────────────

def test_parse_office_action_replace_text() -> None:
    p = te._parse_office_action("replace foo with bar")
    assert p == {"app": "word", "action": "replace_text", "find": "foo", "replace": "bar"}
    print("test_parse_office_action_replace_text: PASS")


def test_parse_office_action_format_selection() -> None:
    p = te._parse_office_action("make this bold and italic")
    assert p["app"] == "word" and p["action"] == "format_selection"
    assert p["bold"] is True and p["italic"] is True
    assert "underline" not in p
    print("test_parse_office_action_format_selection: PASS")


def test_parse_office_action_insert_text() -> None:
    p = te._parse_office_action("write hello there")
    assert p["app"] == "word" and p["action"] == "insert_text"
    assert "hello there" in p["text"]
    print("test_parse_office_action_insert_text: PASS")


def test_parse_office_action_set_cell_with_numeric_value() -> None:
    p = te._parse_office_action("set cell A1 to 5")
    assert p == {"app": "excel", "action": "set_cell", "cell": "A1", "value": 5}
    print("test_parse_office_action_set_cell_with_numeric_value: PASS")


def test_parse_office_action_get_cell() -> None:
    p = te._parse_office_action("what is in cell B2")
    assert p == {"app": "excel", "action": "get_cell", "cell": "B2"}
    print("test_parse_office_action_get_cell: PASS")


def test_parse_office_action_save_disambiguates_app() -> None:
    assert te._parse_office_action("save this document") == {"app": "word", "action": "save"}
    assert te._parse_office_action("save my spreadsheet") == {"app": "excel", "action": "save"}
    print("test_parse_office_action_save_disambiguates_app: PASS")


def test_parse_office_action_returns_none_for_a_bare_open_with_no_content() -> None:
    # office_control.py has no generic "just open the app" action —
    # _run_office() must not fabricate one.
    assert te._parse_office_action("open Word") is None
    assert te._parse_office_action("open Excel") is None
    assert te._parse_office_action("edit this document in Word") is None
    print("test_parse_office_action_returns_none_for_a_bare_open_with_no_content: PASS")


# ── Execution: calls the EXISTING office_control.py, never duplicates it ─

def test_run_office_calls_office_control_with_parsed_params() -> None:
    with patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] cell A1 is now 5.") as m_oc:
        result = te._run_office("set cell A1 to 5")
    m_oc.assert_called_once_with(parameters={"app": "excel", "action": "set_cell", "cell": "A1", "value": 5})
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_run_office_calls_office_control_with_parsed_params: PASS")


def test_run_office_bare_open_never_calls_office_control_and_is_honestly_inconclusive() -> None:
    with patch.object(te, "office_control") as m_oc:
        result = te._run_office("open Word")
    m_oc.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_run_office_bare_open_never_calls_office_control_and_is_honestly_inconclusive: PASS")


# ── Result handling: honest classification, real result not fabricated/discarded ─

def test_office_verified_success_reaches_execute_task_unchanged() -> None:
    with _handlers(office=MagicMock(return_value="[VERIFIED_SUCCESS] A1 is now 5.")):
        result = _task(objective="set cell A1 to 5")
    assert result == "[VERIFIED_SUCCESS] A1 is now 5."
    print("test_office_verified_success_reaches_execute_task_unchanged: PASS")


def test_office_verified_failure_is_not_upgraded_to_success() -> None:
    with _handlers(office=MagicMock(return_value="[VERIFIED_FAILURE] could not save: no such document.")):
        result = _task(objective="save this document")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_office_verified_failure_is_not_upgraded_to_success: PASS")


def test_classify_office_result_unknown_dispatcher_fallback_is_verified_failure() -> None:
    # office_control()'s own top-level dispatcher fallback strings
    # ("Unknown Word action...", "Unknown office app...") are bare, by
    # that module's design — _run_office() should never actually reach
    # this path (see its own docstring), but the classifier still
    # handles it honestly rather than assuming success.
    assert te._classify_office_result("Unknown Word action: 'frobnicate'. Supported: ...") == te._envelope.STATUS_VERIFIED_FAILURE
    print("test_classify_office_result_unknown_dispatcher_fallback_is_verified_failure: PASS")


def test_classify_office_result_passes_through_existing_tags() -> None:
    assert te._classify_office_result("[VERIFIED_SUCCESS] ok.") == te._envelope.STATUS_VERIFIED_SUCCESS
    assert te._classify_office_result("[INCONCLUSIVE] unsure.") == te._envelope.STATUS_INCONCLUSIVE
    print("test_classify_office_result_passes_through_existing_tags: PASS")


# ── Recovery: no artificial office_control chain was invented ──────────

def test_no_real_recovery_chain_entry_exists_for_office() -> None:
    assert "office" not in te._RECOVERY_CHAIN
    print("test_no_real_recovery_chain_entry_exists_for_office: PASS")


def test_office_inconclusive_does_not_recover_into_browser() -> None:
    # Proves the family-scoped mechanism doesn't accidentally let office
    # "recover" into another APPLICATION domain just because no real
    # chain entry exists to stop it — there IS no entry, so nothing
    # should be attempted at all; only ONE step is ever recorded.
    m_office = MagicMock(return_value="[INCONCLUSIVE] not sure this worked.")
    m_browser = MagicMock(return_value="[VERIFIED_SUCCESS] opened.")
    with _handlers(office=m_office, browser=m_browser):
        _task(objective="write hello world in the document")
    m_office.assert_called_once()
    m_browser.assert_not_called()
    print("test_office_inconclusive_does_not_recover_into_browser: PASS")


def _run() -> None:
    test_route_matches_office_for_word_and_excel_content_objectives()
    test_route_office_family_is_application()
    test_route_open_word_and_open_excel_resolve_to_office_not_browser()
    test_route_open_powerpoint_is_honestly_not_office()
    test_route_browser_and_youtube_objectives_unaffected_by_office_addition()
    test_route_system_domains_unaffected_by_office_addition()
    test_parse_office_action_replace_text()
    test_parse_office_action_format_selection()
    test_parse_office_action_insert_text()
    test_parse_office_action_set_cell_with_numeric_value()
    test_parse_office_action_get_cell()
    test_parse_office_action_save_disambiguates_app()
    test_parse_office_action_returns_none_for_a_bare_open_with_no_content()
    test_run_office_calls_office_control_with_parsed_params()
    test_run_office_bare_open_never_calls_office_control_and_is_honestly_inconclusive()
    test_office_verified_success_reaches_execute_task_unchanged()
    test_office_verified_failure_is_not_upgraded_to_success()
    test_classify_office_result_unknown_dispatcher_fallback_is_verified_failure()
    test_classify_office_result_passes_through_existing_tags()
    test_no_real_recovery_chain_entry_exists_for_office()
    test_office_inconclusive_does_not_recover_into_browser()
    print("\nAll task_engine_office tests passed.")


if __name__ == "__main__":
    _run()
