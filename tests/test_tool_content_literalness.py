"""
tests/test_tool_content_literalness.py -- regression lock for a real,
reported bug: asked to "write a sick leave email to my boss" in Word,
JARVIS typed the literal string "a sick leave email to boss, with
salutation, with subject and body" into the document instead of an
actual email. Root cause: office_control's insert_text `text` param
(and file_controller's `content` param, the same class of tool) were
described only as "Text to insert"/"Content for create_file/write" --
nothing told Gemini this field IS the finished output, not a summary of
what the output should contain. Gemini is the one composing the actual
content; these tools only type whatever literal string they're given.

Pure tool-DESCRIPTION language, like test_tool_description_method_
selection.py's own J3 tests -- no dispatch code changed, nothing to
unit-test at the execution level. What's verified deterministically is
that the corrective language actually exists and hasn't silently
regressed. Whether Gemini's own generation reliably honors it is a live-
model question, not testable here (same disclosed limitation as J3).

Run with:
    .venv/Scripts/python.exe -m tests.test_tool_content_literalness
"""
from main import TOOL_DECLARATIONS


def _tool(name: str) -> dict:
    return next(t for t in TOOL_DECLARATIONS if t["name"] == name)


def _param_desc(tool_name: str, param: str) -> str:
    return _tool(tool_name)["parameters"]["properties"][param]["description"]


def test_office_control_insert_text_param_demands_complete_finished_content() -> None:
    d = _param_desc("office_control", "text").lower()
    assert "complete" in d and "finished" in d
    assert "not a description" in d or "never" in d
    print("test_office_control_insert_text_param_demands_complete_finished_content: PASS")


def test_office_control_top_level_description_spells_out_the_sick_leave_example() -> None:
    """The exact reported failure ('a sick leave email with salutation,
    subject, and body' typed literally) is named explicitly as what NOT
    to do, not just an abstract instruction Gemini might skim past."""
    d = _tool("office_control")["description"].lower()
    assert "sick leave email" in d
    assert "verbatim" in d
    print("test_office_control_top_level_description_spells_out_the_sick_leave_example: PASS")


def test_file_controller_content_param_demands_complete_finished_content() -> None:
    d = _param_desc("file_controller", "content").lower()
    assert "complete" in d and "finished" in d
    assert "never a description" in d or "not a description" in d
    print("test_file_controller_content_param_demands_complete_finished_content: PASS")


def test_neither_fix_weakened_any_existing_office_control_safety_language() -> None:
    """Real regression guard: the rewritten description still carries the
    pre-existing verified-write/save-dialog-hang language this tool
    already relied on (see test_office_control.py's own header)."""
    d = _tool("office_control")["description"]
    assert "VERIFIED_SUCCESS" in d and "VERIFIED_FAILURE" in d and "INCONCLUSIVE" in d
    assert "Save As" in d
    print("test_neither_fix_weakened_any_existing_office_control_safety_language: PASS")


if __name__ == "__main__":
    test_office_control_insert_text_param_demands_complete_finished_content()
    test_office_control_top_level_description_spells_out_the_sick_leave_example()
    test_file_controller_content_param_demands_complete_finished_content()
    test_neither_fix_weakened_any_existing_office_control_safety_language()
    print("\nAll tool-content-literalness tests passed.")
