"""
actions/office_control.py — Word/Excel DOCUMENT-CONTENT automation via
Microsoft's own official COM Automation object model (win32com.client,
part of pywin32 — already a project dependency, confirmed importing
cleanly), NOT generic UI-coordinate/UIA clicking.

Why this exists as its own path rather than going through
computer_control.py's accomplish(): Office's ribbon is a known weak spot
for UI Automation — automation IDs on ribbon controls are inconsistent
across Office versions and collapse/rearrange based on window width, so
"click Bold" via accomplish() can legitimately come back UI_AMBIGUOUS or
fail. That's a UI-clicking problem; DOCUMENT CONTENT (a cell's value, a
paragraph's formatting) was never a UI-clicking problem to begin with —
Word and Excel expose their real object model (Document/Range/Selection,
Workbook/Worksheet/Range) directly over COM, the same "skip UI guessing
when a real API exists" principle already used by browser_control.py's
DOM-based smart_click/smart_type and system_shortcuts.py's cmdlet
library.

Every write is verified by reading the SAME thing back afterward and
wrapped in the shared Result Envelope (result_envelope.py) — never a
bare "Done."

Every function ATTACHES to an already-running Word/Excel instance if one
exists (GetActiveObject — COM's Running Object Table) and only launches
a new, VISIBLE instance if none is running (never hidden) — the same
"reuse the existing session, don't spawn an untracked new one" principle
as browser_control.py's _SessionRegistry, adapted to COM's own
equivalent of that check.

Deliberately narrow for this first pass: acts on whichever
document/workbook is currently ACTIVE in the app — same as a human
clicking into it first — not a multi-document addressing scheme. Also
deliberately does NOT ever call Save() on a document/workbook that has
never been saved before (no filename yet): Word/Excel's Save() on such a
document opens a BLOCKING native "Save As" dialog that this process has
no way to answer, which would hang the call indefinitely rather than
fail cleanly — so that case is caught up front and reported honestly
instead of risked.
"""
from actions import result_envelope as _envelope

try:
    import win32com.client
    _COM_OK = True
except ImportError:  # pragma: no cover — Windows-only dependency
    win32com = None
    _COM_OK = False


def _get_app(prog_id: str):
    """Attaches to an already-running instance via GetActiveObject; if
    none is running, launches a new VISIBLE one via Dispatch. Returns
    (app_or_None, was_already_running: bool)."""
    try:
        return win32com.client.GetActiveObject(prog_id), True
    except Exception:
        pass
    try:
        app = win32com.client.Dispatch(prog_id)
        app.Visible = True
        return app, False
    except Exception as e:
        print(f"[Office] Could not start {prog_id}: {e}")
        return None, False


def _bool_param(params: dict, key: str):
    val = (params or {}).get(key)
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


# ── Word ─────────────────────────────────────────────────────────────

def word_insert_text(text: str, where: str = "cursor") -> str:
    if not _COM_OK:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "pywin32/win32com is not available")
    text = text or ""
    if not text:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no text given to insert")
    app, _ = _get_app("Word.Application")
    if app is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "could not start or attach to Word")
    try:
        doc = app.ActiveDocument
    except Exception:
        doc = None
    if doc is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "Word has no open document to insert into")
    try:
        before_len = len(doc.Content.Text or "")
        if where == "end":
            rng = doc.Content
            rng.Collapse(0)  # wdCollapseEnd
            rng.InsertAfter(text)
        else:
            app.Selection.Range.InsertAfter(text)
        after_len = len(doc.Content.Text or "")
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"could not insert text: {e}")
    if after_len - before_len == len(text):
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"inserted {len(text)} character(s) into the document")
    return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "text was inserted but the document's length change didn't match exactly")


def word_replace_text(find: str, replace: str) -> str:
    if not _COM_OK:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "pywin32/win32com is not available")
    find = find or ""
    if not find:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no text given to find")
    app, _ = _get_app("Word.Application")
    if app is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "could not start or attach to Word")
    try:
        doc = app.ActiveDocument
    except Exception:
        doc = None
    if doc is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "Word has no open document to search")
    try:
        find_obj = doc.Content.Find
        find_obj.ClearFormatting()
        replaced = find_obj.Execute(FindText=find, ReplaceWith=replace or "", Replace=2)  # wdReplaceAll
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"could not run find/replace: {e}")
    if not replaced:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"'{find}' was not found in the document")
    try:
        remaining = doc.Content.Text.count(find) if find != replace else None
    except Exception:
        remaining = None
    if remaining in (0, None):
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"replaced '{find}' with '{replace}'")
    return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"replace ran but '{find}' still appears {remaining} more time(s)")


def word_format_selection(bold=None, italic=None, underline=None) -> str:
    if not _COM_OK:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "pywin32/win32com is not available")
    requested = {"bold": bold, "italic": italic, "underline": underline}
    requested = {k: v for k, v in requested.items() if v is not None}
    if not requested:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no formatting (bold/italic/underline) was requested")
    app, _ = _get_app("Word.Application")
    if app is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "could not start or attach to Word")
    try:
        sel = app.Selection
        if sel is None or sel.Range.Start == sel.Range.End:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "nothing is currently selected in Word to format")
        if "bold" in requested:
            sel.Font.Bold = int(requested["bold"])
        if "italic" in requested:
            sel.Font.Italic = int(requested["italic"])
        if "underline" in requested:
            sel.Font.Underline = 1 if requested["underline"] else 0
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"could not format the selection: {e}")
    try:
        actual = {}
        if "bold" in requested:      actual["bold"] = bool(sel.Font.Bold)
        if "italic" in requested:    actual["italic"] = bool(sel.Font.Italic)
        if "underline" in requested: actual["underline"] = bool(sel.Font.Underline)
    except Exception:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "formatting was applied but could not be read back")
    mismatches = [k for k, v in actual.items() if v != requested[k]]
    if not mismatches:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"selection formatting is now {actual}")
    return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"formatting did not fully apply (mismatch on {mismatches}); now {actual}")


def word_save() -> str:
    if not _COM_OK:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "pywin32/win32com is not available")
    app, was_running = _get_app("Word.Application")
    if app is None or not was_running:
        # If Word wasn't already running there's no document the user
        # could have meant — launching Word fresh just to "save nothing"
        # would be a fabricated action, not a real save.
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "Word is not currently open with a document")
    try:
        doc = app.ActiveDocument
    except Exception:
        doc = None
    if doc is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "Word has no open document to save")
    try:
        has_path = bool(doc.Path)
    except Exception:
        has_path = None
    if has_path is False:
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            "this document has never been saved and has no filename yet — Save() would open a blocking "
            "native 'Save As' dialog this process cannot answer; ask the user for a filename/location first",
        )
    try:
        doc.Save()
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"could not save: {e}")
    try:
        saved = bool(doc.Saved)
    except Exception:
        saved = None
    if saved:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "document saved")
    if saved is False:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "Save() was called but the document still reports unsaved changes")
    return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "the document was saved but its saved state could not be read back to confirm")


# ── Excel ────────────────────────────────────────────────────────────

def excel_set_cell(cell: str, value) -> str:
    if not _COM_OK:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "pywin32/win32com is not available")
    cell = (cell or "").strip()
    if not cell:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no cell reference given (e.g. 'A1')")
    app, _ = _get_app("Excel.Application")
    if app is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "could not start or attach to Excel")
    try:
        wb = app.ActiveWorkbook
    except Exception:
        wb = None
    if wb is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "Excel has no open workbook")
    try:
        sheet = wb.ActiveSheet
        sheet.Range(cell).Value = value
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"could not set {cell}: {e}")
    try:
        actual = sheet.Range(cell).Value
    except Exception:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"{cell} was set but could not be read back")
    # A formula ("=SUM(A1:A5)") is what you SET; Excel then returns its
    # COMPUTED result on readback — that mismatch is expected/correct,
    # not a failure, so only value-compare non-formula writes.
    is_formula = isinstance(value, str) and value.strip().startswith("=")
    if is_formula:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"{cell} now evaluates to {actual!r}")
    numeric_match = (
        isinstance(value, (int, float)) and isinstance(actual, (int, float)) and abs(actual - value) < 1e-9
    )
    if actual == value or numeric_match:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"{cell} is now {actual!r}")
    return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"requested {value!r} but {cell} now reads {actual!r}")


def excel_get_cell(cell: str) -> str:
    if not _COM_OK:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "pywin32/win32com is not available")
    cell = (cell or "").strip()
    if not cell:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no cell reference given (e.g. 'A1')")
    app, _ = _get_app("Excel.Application")
    if app is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "could not start or attach to Excel")
    try:
        wb = app.ActiveWorkbook
    except Exception:
        wb = None
    if wb is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "Excel has no open workbook")
    try:
        value = wb.ActiveSheet.Range(cell).Value
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"could not read {cell}: {e}")
    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"{cell} = {value!r}")


def excel_save() -> str:
    if not _COM_OK:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "pywin32/win32com is not available")
    app, was_running = _get_app("Excel.Application")
    if app is None or not was_running:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "Excel is not currently open with a workbook")
    try:
        wb = app.ActiveWorkbook
    except Exception:
        wb = None
    if wb is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "Excel has no open workbook to save")
    try:
        has_path = bool(wb.Path)
    except Exception:
        has_path = None
    if has_path is False:
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            "this workbook has never been saved and has no filename yet — Save() would open a blocking "
            "native 'Save As' dialog this process cannot answer; ask the user for a filename/location first",
        )
    try:
        wb.Save()
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"could not save: {e}")
    try:
        saved = bool(wb.Saved)
    except Exception:
        saved = None
    if saved:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "workbook saved")
    if saved is False:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "Save() was called but the workbook still reports unsaved changes")
    return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "the workbook was saved but its saved state could not be read back to confirm")


# ── public dispatcher (wired into main.py as the office_control tool) ──

def office_control(parameters: dict = None) -> str:
    params = parameters or {}
    app_name = str(params.get("app", "")).strip().lower()
    action = str(params.get("action", "")).strip().lower()

    if app_name == "word":
        if action in ("insert_text", "type"):
            return word_insert_text(str(params.get("text", "")), where=str(params.get("where", "cursor")).lower())
        if action in ("replace_text", "find_replace"):
            return word_replace_text(str(params.get("find", "")), str(params.get("replace", "")))
        if action in ("format_selection", "format"):
            return word_format_selection(
                bold=_bool_param(params, "bold"),
                italic=_bool_param(params, "italic"),
                underline=_bool_param(params, "underline"),
            )
        if action == "save":
            return word_save()
        return f"Unknown Word action: '{action}'. Supported: insert_text, replace_text, format_selection, save."

    if app_name == "excel":
        if action == "set_cell":
            return excel_set_cell(str(params.get("cell", "")), params.get("value"))
        if action == "get_cell":
            return excel_get_cell(str(params.get("cell", "")))
        if action == "save":
            return excel_save()
        return f"Unknown Excel action: '{action}'. Supported: set_cell, get_cell, save."

    return f"Unknown office app: '{app_name}'. Supported: word, excel."
