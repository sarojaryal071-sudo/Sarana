"""
tests/test_office_control.py — Phase 3 of the "unused capabilities"
follow-up: Word/Excel DOCUMENT-CONTENT automation via win32com.client
(pywin32's COM Automation, Microsoft's own official Office object model)
rather than accomplish()'s generic UI-clicking, which is unreliable
against Office's own ribbon (inconsistent automation IDs across
versions).

Per this project's own established testing practice, NO test here ever
launches a real Word/Excel instance — win32com.client.GetActiveObject
and .Dispatch are mocked in every test. What's verified is that the
CORRECT COM calls happen (SetMasterVolume-style set-then-read-back
discipline, same shared Result Envelope) against fake app/document/
selection/range objects, exactly the "implementation -> mocked call ->
correct dispatch logic" standard this project's other suites already
use — including two REAL regression guards for the two concrete hang/
false-action risks found while designing this module:
  1. Save() must NEVER be called on a document/workbook that has never
     been saved before (no filename yet) — Office's Save() on such a
     document opens a BLOCKING native "Save As" dialog this process has
     no way to answer.
  2. save() must NEVER launch a fresh Word/Excel instance just to "save
     nothing" — if the app wasn't already running there is no document
     the user could have meant.

Run with:
    .venv/Scripts/python.exe -m tests.test_office_control
"""
from unittest.mock import patch, MagicMock

import actions.office_control as oc


# ── _get_app: attach-if-running, launch-visible-if-not ──────────────────

def test_get_app_attaches_to_an_already_running_instance() -> None:
    fake_app = MagicMock()
    with patch.object(oc.win32com.client, "GetActiveObject", return_value=fake_app) as m_get, \
         patch.object(oc.win32com.client, "Dispatch") as m_dispatch:
        app, was_running = oc._get_app("Word.Application")
    assert app is fake_app
    assert was_running is True
    m_get.assert_called_once_with("Word.Application")
    m_dispatch.assert_not_called()
    print("test_get_app_attaches_to_an_already_running_instance: PASS")

def test_get_app_launches_a_new_visible_instance_when_none_is_running() -> None:
    fake_app = MagicMock()
    with patch.object(oc.win32com.client, "GetActiveObject", side_effect=Exception("none running")), \
         patch.object(oc.win32com.client, "Dispatch", return_value=fake_app) as m_dispatch:
        app, was_running = oc._get_app("Excel.Application")
    assert app is fake_app
    assert was_running is False
    assert fake_app.Visible is True  # never launched hidden
    m_dispatch.assert_called_once_with("Excel.Application")
    print("test_get_app_launches_a_new_visible_instance_when_none_is_running: PASS")

def test_get_app_returns_none_when_both_attach_and_launch_fail() -> None:
    with patch.object(oc.win32com.client, "GetActiveObject", side_effect=Exception("none")), \
         patch.object(oc.win32com.client, "Dispatch", side_effect=Exception("not installed")):
        app, was_running = oc._get_app("Word.Application")
    assert app is None and was_running is False
    print("test_get_app_returns_none_when_both_attach_and_launch_fail: PASS")


# ── Word: insert_text ────────────────────────────────────────────────

def _fake_word_app(doc=None):
    app = MagicMock()
    app.ActiveDocument = doc
    return app

class _FakeRange:
    """A minimal stand-in for Word's Range COM object that actually
    mutates its own text on InsertAfter() — real enough to let
    word_insert_text()'s before/after length-diff verification exercise
    genuine behavior instead of two hand-wired mock return values."""
    def __init__(self, initial_text=""):
        self.text = initial_text
        self.collapse_calls = []
    @property
    def Text(self):
        return self.text
    def InsertAfter(self, s):
        self.text += s
    def Collapse(self, direction):
        self.collapse_calls.append(direction)

def test_word_insert_text_at_cursor_verifies_length_change() -> None:
    content = _FakeRange("hello")
    doc = MagicMock()
    doc.Content = content
    app = _fake_word_app(doc)
    app.Selection.Range = content  # the cursor path inserts via Selection.Range
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_insert_text(" world", where="cursor")
    assert content.text == "hello world"
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_word_insert_text_at_cursor_verifies_length_change: PASS")

def test_word_insert_text_with_no_text_is_inconclusive_calls_nothing() -> None:
    with patch.object(oc, "_get_app") as m_get:
        result = oc.word_insert_text("")
    m_get.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_word_insert_text_with_no_text_is_inconclusive_calls_nothing: PASS")

def test_word_insert_text_with_no_open_document_is_verified_failure() -> None:
    app = _fake_word_app(doc=None)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_insert_text("hello")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_word_insert_text_with_no_open_document_is_verified_failure: PASS")

def test_word_insert_text_at_end_collapses_and_inserts_after_content() -> None:
    doc = MagicMock()
    doc.Content.Text = "x" * 10
    app = _fake_word_app(doc)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        oc.word_insert_text("!!", where="end")
    doc.Content.Collapse.assert_called_once_with(0)
    doc.Content.InsertAfter.assert_called_once_with("!!")
    print("test_word_insert_text_at_end_collapses_and_inserts_after_content: PASS")


# ── Word: replace_text ───────────────────────────────────────────────

def test_word_replace_text_reports_verified_success_when_found_and_replaced() -> None:
    doc = MagicMock()
    doc.Content.Find.Execute.return_value = True
    doc.Content.Text = "no more xyz text"  # "old" no longer present after the replace
    app = _fake_word_app(doc)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_replace_text("old", "new")
    doc.Content.Find.Execute.assert_called_once_with(FindText="old", ReplaceWith="new", Replace=2)
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_word_replace_text_reports_verified_success_when_found_and_replaced: PASS")

def test_word_replace_text_reports_verified_failure_when_not_found() -> None:
    doc = MagicMock()
    doc.Content.Find.Execute.return_value = False
    app = _fake_word_app(doc)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_replace_text("nonexistent", "new")
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "was not found" in result
    print("test_word_replace_text_reports_verified_failure_when_not_found: PASS")

def test_word_replace_text_with_no_find_text_calls_nothing() -> None:
    with patch.object(oc, "_get_app") as m_get:
        result = oc.word_replace_text("", "new")
    m_get.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_word_replace_text_with_no_find_text_calls_nothing: PASS")


# ── Word: format_selection ───────────────────────────────────────────

def test_word_format_selection_with_nothing_requested_is_inconclusive() -> None:
    with patch.object(oc, "_get_app") as m_get:
        result = oc.word_format_selection()
    m_get.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_word_format_selection_with_nothing_requested_is_inconclusive: PASS")

def test_word_format_selection_with_no_selection_is_verified_failure() -> None:
    app = MagicMock()
    app.Selection.Range.Start = 5
    app.Selection.Range.End = 5  # collapsed = nothing selected
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_format_selection(bold=True)
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "nothing is currently selected" in result
    print("test_word_format_selection_with_no_selection_is_verified_failure: PASS")

def test_word_format_selection_sets_and_verifies_bold_and_italic() -> None:
    app = MagicMock()
    app.Selection.Range.Start = 0
    app.Selection.Range.End = 10
    app.Selection.Font.Bold = 1
    app.Selection.Font.Italic = 1
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_format_selection(bold=True, italic=True)
    assert app.Selection.Font.Bold == 1
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_word_format_selection_sets_and_verifies_bold_and_italic: PASS")

class _StubbornFont:
    """A Font stand-in that silently refuses a Bold assignment — models
    a real-world case (e.g. a protected/locked selection) where Word
    accepts the property set without erroring but the value doesn't
    actually change, which word_format_selection() must catch via its
    own readback, not assume from 'no exception was raised'."""
    Bold = 0
    def __setattr__(self, key, value):
        pass  # every assignment is silently ignored

def test_word_format_selection_reports_failure_when_readback_disagrees() -> None:
    app = MagicMock()
    app.Selection.Range.Start = 0
    app.Selection.Range.End = 10
    app.Selection.Font = _StubbornFont()
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_format_selection(bold=True)
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_word_format_selection_reports_failure_when_readback_disagrees: PASS")


# ── Word: save — the two hang/false-action regression guards ───────────

def test_word_save_never_launches_a_fresh_instance_just_to_save_nothing() -> None:
    with patch.object(oc, "_get_app", return_value=(MagicMock(), False)) as m_get:
        result = oc.word_save()
    m_get.assert_called_once()
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "not currently open" in result
    print("test_word_save_never_launches_a_fresh_instance_just_to_save_nothing: PASS")

def test_word_save_never_calls_save_on_a_document_with_no_filename_yet() -> None:
    doc = MagicMock()
    doc.Path = ""  # never saved before
    app = _fake_word_app(doc)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_save()
    doc.Save.assert_not_called()  # the actual hang-risk regression guard
    assert result.startswith("[INCONCLUSIVE]")
    assert "Save As" in result
    print("test_word_save_never_calls_save_on_a_document_with_no_filename_yet: PASS")

def test_word_save_succeeds_and_verifies_saved_state() -> None:
    doc = MagicMock()
    doc.Path = "C:\\Users\\me\\doc.docx"
    doc.Saved = True
    app = _fake_word_app(doc)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.word_save()
    doc.Save.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_word_save_succeeds_and_verifies_saved_state: PASS")


# ── Excel: set_cell / get_cell ───────────────────────────────────────

def _fake_excel_app(wb=None):
    app = MagicMock()
    app.ActiveWorkbook = wb
    return app

def test_excel_set_cell_with_no_cell_ref_calls_nothing() -> None:
    with patch.object(oc, "_get_app") as m_get:
        result = oc.excel_set_cell("", 42)
    m_get.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_excel_set_cell_with_no_cell_ref_calls_nothing: PASS")

def test_excel_set_cell_verifies_plain_value_readback() -> None:
    wb = MagicMock()
    range_mock = MagicMock()
    range_mock.Value = 42
    wb.ActiveSheet.Range.return_value = range_mock
    app = _fake_excel_app(wb)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.excel_set_cell("A1", 42)
    assert range_mock.Value == 42
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_excel_set_cell_verifies_plain_value_readback: PASS")

class _StubbornRange:
    """A Range stand-in whose Value assignment is silently ignored —
    models a real-world case (e.g. a protected sheet) where Excel
    accepts the write without erroring but the cell doesn't actually
    change, which excel_set_cell() must catch via its own readback, not
    assume from 'no exception was raised'."""
    Value = 999
    def __setattr__(self, key, value):
        pass

def test_excel_set_cell_reports_failure_on_a_disagreeing_readback() -> None:
    wb = MagicMock()
    wb.ActiveSheet.Range.return_value = _StubbornRange()
    app = _fake_excel_app(wb)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.excel_set_cell("A1", 42)
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_excel_set_cell_reports_failure_on_a_disagreeing_readback: PASS")

class _ComputedRange:
    """Value assignment is accepted (like a real formula write) but
    readback always returns the fixed COMPUTED result (15) regardless
    of what string was assigned — models Excel evaluating a formula
    rather than storing it verbatim."""
    def __setattr__(self, key, value):
        pass
    @property
    def Value(self):
        return 15

def test_excel_set_cell_with_a_formula_accepts_the_computed_readback() -> None:
    # Setting "=SUM(A1:A5)" and reading back e.g. 15 (the computed
    # result) is CORRECT, not a mismatch — this is the specific case
    # excel_set_cell must not misreport as VERIFIED_FAILURE.
    wb = MagicMock()
    wb.ActiveSheet.Range.return_value = _ComputedRange()
    app = _fake_excel_app(wb)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.excel_set_cell("B1", "=SUM(A1:A5)")
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "15" in result
    print("test_excel_set_cell_with_a_formula_accepts_the_computed_readback: PASS")

def test_excel_get_cell_reports_the_real_value() -> None:
    wb = MagicMock()
    wb.ActiveSheet.Range.return_value.Value = "hello"
    app = _fake_excel_app(wb)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.excel_get_cell("A1")
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "hello" in result
    print("test_excel_get_cell_reports_the_real_value: PASS")

def test_excel_get_cell_with_no_open_workbook_is_verified_failure() -> None:
    app = _fake_excel_app(wb=None)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.excel_get_cell("A1")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_excel_get_cell_with_no_open_workbook_is_verified_failure: PASS")


# ── Excel: save — same two hang/false-action regression guards ─────────

def test_excel_save_never_launches_a_fresh_instance_just_to_save_nothing() -> None:
    with patch.object(oc, "_get_app", return_value=(MagicMock(), False)):
        result = oc.excel_save()
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_excel_save_never_launches_a_fresh_instance_just_to_save_nothing: PASS")

def test_excel_save_never_calls_save_on_a_workbook_with_no_filename_yet() -> None:
    wb = MagicMock()
    wb.Path = ""
    app = _fake_excel_app(wb)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.excel_save()
    wb.Save.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    assert "Save As" in result
    print("test_excel_save_never_calls_save_on_a_workbook_with_no_filename_yet: PASS")

def test_excel_save_succeeds_and_verifies_saved_state() -> None:
    wb = MagicMock()
    wb.Path = "C:\\Users\\me\\book.xlsx"
    wb.Saved = True
    app = _fake_excel_app(wb)
    with patch.object(oc, "_get_app", return_value=(app, True)):
        result = oc.excel_save()
    wb.Save.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_excel_save_succeeds_and_verifies_saved_state: PASS")


# ── office_control(): the public dispatcher ─────────────────────────────

def test_office_control_routes_word_insert_text_with_correct_args() -> None:
    with patch.object(oc, "word_insert_text", return_value="[VERIFIED_SUCCESS] ok") as m:
        result = oc.office_control({"app": "word", "action": "insert_text", "text": "hi", "where": "end"})
    m.assert_called_once_with("hi", where="end")
    assert result == "[VERIFIED_SUCCESS] ok"
    print("test_office_control_routes_word_insert_text_with_correct_args: PASS")

def test_office_control_routes_excel_set_cell_with_correct_args() -> None:
    with patch.object(oc, "excel_set_cell", return_value="[VERIFIED_SUCCESS] ok") as m:
        result = oc.office_control({"app": "excel", "action": "set_cell", "cell": "A1", "value": "42"})
    m.assert_called_once_with("A1", "42")
    assert result == "[VERIFIED_SUCCESS] ok"
    print("test_office_control_routes_excel_set_cell_with_correct_args: PASS")

def test_office_control_routes_word_format_selection_bool_params() -> None:
    with patch.object(oc, "word_format_selection", return_value="[VERIFIED_SUCCESS] ok") as m:
        oc.office_control({"app": "word", "action": "format_selection", "bold": True, "italic": False})
    m.assert_called_once_with(bold=True, italic=False, underline=None)
    print("test_office_control_routes_word_format_selection_bool_params: PASS")

def test_office_control_with_unknown_app_says_so_honestly() -> None:
    result = oc.office_control({"app": "powerpoint", "action": "save"})
    assert "Unknown office app" in result
    print("test_office_control_with_unknown_app_says_so_honestly: PASS")

def test_office_control_with_unknown_action_says_so_honestly() -> None:
    result = oc.office_control({"app": "word", "action": "delete_everything"})
    assert "Unknown Word action" in result
    print("test_office_control_with_unknown_action_says_so_honestly: PASS")


if __name__ == "__main__":
    test_get_app_attaches_to_an_already_running_instance()
    test_get_app_launches_a_new_visible_instance_when_none_is_running()
    test_get_app_returns_none_when_both_attach_and_launch_fail()
    test_word_insert_text_at_cursor_verifies_length_change()
    test_word_insert_text_with_no_text_is_inconclusive_calls_nothing()
    test_word_insert_text_with_no_open_document_is_verified_failure()
    test_word_insert_text_at_end_collapses_and_inserts_after_content()
    test_word_replace_text_reports_verified_success_when_found_and_replaced()
    test_word_replace_text_reports_verified_failure_when_not_found()
    test_word_replace_text_with_no_find_text_calls_nothing()
    test_word_format_selection_with_nothing_requested_is_inconclusive()
    test_word_format_selection_with_no_selection_is_verified_failure()
    test_word_format_selection_sets_and_verifies_bold_and_italic()
    test_word_format_selection_reports_failure_when_readback_disagrees()
    test_word_save_never_launches_a_fresh_instance_just_to_save_nothing()
    test_word_save_never_calls_save_on_a_document_with_no_filename_yet()
    test_word_save_succeeds_and_verifies_saved_state()
    test_excel_set_cell_with_no_cell_ref_calls_nothing()
    test_excel_set_cell_verifies_plain_value_readback()
    test_excel_set_cell_reports_failure_on_a_disagreeing_readback()
    test_excel_set_cell_with_a_formula_accepts_the_computed_readback()
    test_excel_get_cell_reports_the_real_value()
    test_excel_get_cell_with_no_open_workbook_is_verified_failure()
    test_excel_save_never_launches_a_fresh_instance_just_to_save_nothing()
    test_excel_save_never_calls_save_on_a_workbook_with_no_filename_yet()
    test_excel_save_succeeds_and_verifies_saved_state()
    test_office_control_routes_word_insert_text_with_correct_args()
    test_office_control_routes_excel_set_cell_with_correct_args()
    test_office_control_routes_word_format_selection_bool_params()
    test_office_control_with_unknown_app_says_so_honestly()
    test_office_control_with_unknown_action_says_so_honestly()
    print("\nAll office_control tests passed.")
