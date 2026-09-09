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

Follow-up (same bug, different tool): the SAME request recurred even
after the office_control/file_controller fix above -- "write a sick
leave email... in ms word" typed the near-verbatim user utterance into
the document. Real root cause this time: computer_settings also offers
generic "typing text on screen" (action='type_text'), and its `value`/
`description` params had the identical unguarded-content gap. Worse,
`description` (used when `action` is omitted) used to feed a SEPARATE
live Gemini sub-call (actions/computer_settings.py's own
_detect_action(), a smaller/faster model whose only job was guessing an
action from free text) -- not a content composer -- so a composition
request routed through `description` would get typed back out nearly
verbatim, no matter how well office_control's own text param was fixed.
_detect_action() has since been replaced entirely with a local difflib
match (no LLM call at all) as part of a separate cleanup -- see the test
below confirming that sub-prompt is genuinely gone, and
tests/test_computer_settings.py for the real behavioral coverage.

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


def test_computer_settings_value_param_demands_complete_finished_content_for_type_text() -> None:
    d = _param_desc("computer_settings", "value").lower()
    assert "complete" in d
    assert "never a description" in d or "not a description" in d or "never a summary" in d
    print("test_computer_settings_value_param_demands_complete_finished_content_for_type_text: PASS")


def test_computer_settings_description_param_is_explicitly_ruled_out_for_content_composition() -> None:
    """The other half of the real recurrence: `description` feeds a
    separate lightweight intent-detector, not a content composer — must
    be explicitly steered away from for anything needing real composed
    text, not just have `value` fixed in isolation."""
    d = _param_desc("computer_settings", "description").lower()
    assert "not for content composition" in d or "composition" in d
    top = _tool("computer_settings")["description"].lower()
    assert "description" in top and "intent-detector" in top
    print("test_computer_settings_description_param_is_explicitly_ruled_out_for_content_composition: PASS")


def test_computer_settings_points_to_office_control_for_word_excel_content() -> None:
    """Tool-CHOICE guidance, not just content-completeness — the reported
    case used Word specifically, where office_control is the actually-
    correct, more reliable tool."""
    d = _tool("computer_settings")["description"].lower()
    assert "office_control" in d
    d2 = _tool("office_control")["description"].lower()
    assert "computer_settings" in d2
    print("test_computer_settings_points_to_office_control_for_word_excel_content: PASS")


def test_detect_action_no_longer_has_a_sub_prompt_to_worry_about_at_all() -> None:
    """Superseded, not just fixed: _detect_action() used to be a second
    live Gemini call with its own composed-content instruction (defense
    in depth for THAT path). It's now a local difflib match with no LLM
    call at all — see tests/test_computer_settings.py's own
    test_detect_action_matches_locally_without_a_second_gemini_call for
    the real behavioral coverage. Nothing here can compose or mangle
    content anymore because nothing here generates text; it only matches
    an action NAME. This test just locks in that the old sub-prompt
    genuinely stayed gone."""
    import inspect
    import actions.computer_settings as cs
    src = inspect.getsource(cs._detect_action)
    assert "genai" not in src and "generate_content" not in src
    print("test_detect_action_no_longer_has_a_sub_prompt_to_worry_about_at_all: PASS")


if __name__ == "__main__":
    test_office_control_insert_text_param_demands_complete_finished_content()
    test_office_control_top_level_description_spells_out_the_sick_leave_example()
    test_file_controller_content_param_demands_complete_finished_content()
    test_neither_fix_weakened_any_existing_office_control_safety_language()
    test_computer_settings_value_param_demands_complete_finished_content_for_type_text()
    test_computer_settings_description_param_is_explicitly_ruled_out_for_content_composition()
    test_computer_settings_points_to_office_control_for_word_excel_content()
    test_detect_action_no_longer_has_a_sub_prompt_to_worry_about_at_all()
    print("\nAll tool-content-literalness tests passed.")
