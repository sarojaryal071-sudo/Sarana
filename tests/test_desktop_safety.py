"""
tests/test_desktop_safety.py — Phase 0 of the JARVIS execution-architecture
mission: closing the actual, previously-flagged security gap in
actions/desktop.py's generate-then-exec() path (action='task').

Two independent layers are tested: (1) the sandbox itself no longer
injects the specific objects/builtins (getattr/hasattr, raw ctypes) that
enable the classic "no __import__ needed, just walk the object graph"
Python sandbox-escape technique; (2) a static AST check
(_validate_generated_code) rejects dangerous code BEFORE exec() ever
runs, independent of layer 1 — so removing an item from the injected
builtins isn't the only thing standing between generated code and a
real escape.

Per this project's own established testing practice: no test here
actually runs unsafe generated code against the real sandbox and hopes
it's contained — the validator is tested by asserting it REJECTS known
escape-shaped code (never executing it at all), and the "escape closed"
claim for the removed sandbox objects is verified by confirming
exec()-time NameError on code that WOULD have used them, not by trying
the escape and hoping it fails safely.

Run with:
    .venv/Scripts/python.exe -m tests.test_desktop_safety
"""
import os
from unittest.mock import patch, MagicMock

import actions.desktop as desktop


# ── _validate_generated_code: the static AST layer ──────────────────────

def test_validator_rejects_import_statements() -> None:
    reason = desktop._validate_generated_code("import os\nos.system('echo hi')")
    assert reason is not None and "import" in reason
    print("test_validator_rejects_import_statements: PASS")

def test_validator_rejects_import_from() -> None:
    reason = desktop._validate_generated_code("from os import system")
    assert reason is not None and "import" in reason
    print("test_validator_rejects_import_from: PASS")

def test_validator_rejects_the_classic_no_import_needed_escape_gadget() -> None:
    # The textbook sandbox-escape one-liner — reaches __import__ via
    # object-graph traversal (__class__/__bases__/__subclasses__),
    # WITHOUT ever using the word "import". This is exactly what removing
    # getattr/hasattr from safe_builtins alone would NOT have caught if
    # written differently — the AST layer catches it independently, by
    # its dunder attribute access, not by name-matching "import".
    code = "().__class__.__bases__[0].__subclasses__()"
    reason = desktop._validate_generated_code(code)
    assert reason is not None and "dunder" in reason
    print("test_validator_rejects_the_classic_no_import_needed_escape_gadget: PASS")

def test_validator_rejects_globals_dunder_access() -> None:
    reason = desktop._validate_generated_code("print(shutil.copy2.__globals__)")
    assert reason is not None and "dunder" in reason
    print("test_validator_rejects_globals_dunder_access: PASS")

def test_validator_rejects_blocked_call_names() -> None:
    for snippet, name in [
        ("exec('print(1)')", "exec"),
        ("eval('1+1')", "eval"),
        ("compile('1', '<s>', 'eval')", "compile"),
        ("open('C:/secrets.txt')", "open"),
        ("__import__('os')", "__import__"),
    ]:
        reason = desktop._validate_generated_code(snippet)
        assert reason is not None and name in reason, f"expected '{name}' to be rejected, got: {reason}"
    print("test_validator_rejects_blocked_call_names: PASS")

def test_validator_rejects_syntax_errors_without_crashing() -> None:
    reason = desktop._validate_generated_code("def broken(:\n  pass")
    assert reason is not None and "syntax error" in reason
    print("test_validator_rejects_syntax_errors_without_crashing: PASS")

def test_validator_accepts_legitimate_safe_code() -> None:
    code = (
        "p = Path.home() / 'Desktop' / 'note.txt'\n"
        "print('checking', p)\n"
        "if p.exists():\n"
        "    print('found it')\n"
    )
    reason = desktop._validate_generated_code(code)
    assert reason is None, f"legitimate code was wrongly rejected: {reason}"
    print("test_validator_accepts_legitimate_safe_code: PASS")


# ── _build_sandbox(): the removed-objects layer ─────────────────────────

def test_sandbox_no_longer_exposes_getattr_or_hasattr() -> None:
    sandbox = desktop._build_sandbox()
    builtins_dict = sandbox["__builtins__"]
    assert "getattr" not in builtins_dict
    assert "hasattr" not in builtins_dict
    print("test_sandbox_no_longer_exposes_getattr_or_hasattr: PASS")

def test_sandbox_no_longer_exposes_raw_ctypes() -> None:
    sandbox = desktop._build_sandbox()
    assert "ctypes" not in sandbox
    print("test_sandbox_no_longer_exposes_raw_ctypes: PASS")

def test_code_using_removed_ctypes_fails_at_exec_time_never_runs() -> None:
    # Passes the AST validator (no import, no dunder, no blocked call
    # name) — proves the REMOVAL, not the validator, is what stops this;
    # confirms it fails honestly (NameError) rather than silently
    # succeeding or crashing the whole tool.
    code = "ctypes.CDLL('kernel32').Beep(750, 300)"
    assert desktop._validate_generated_code(code) is None  # passes AST layer
    result = desktop._execute_generated_code(code)
    assert "Execution error" in result and "ctypes" in result
    print("test_code_using_removed_ctypes_fails_at_exec_time_never_runs: PASS")


# ── _execute_generated_code: end-to-end, both layers together ──────────

def test_execute_generated_code_blocks_the_unsafe_sentinel() -> None:
    result = desktop._execute_generated_code("UNSAFE")
    assert result.startswith("[BLOCKED]")
    print("test_execute_generated_code_blocks_the_unsafe_sentinel: PASS")

def test_execute_generated_code_blocks_rejected_code_before_exec() -> None:
    # A real behavioral proof exec() never ran: this code would set a
    # real environment variable if it executed — it must NOT be set
    # afterward.
    marker = "JARVIS_SANDBOX_ESCAPE_TEST_MARKER"
    os.environ.pop(marker, None)
    code = f"import os\nos.environ['{marker}'] = '1'"
    result = desktop._execute_generated_code(code)
    assert result.startswith("[BLOCKED]")
    assert marker not in os.environ
    print("test_execute_generated_code_blocks_rejected_code_before_exec: PASS")

def test_execute_generated_code_runs_safe_code_normally() -> None:
    result = desktop._execute_generated_code("print('hello from sandbox')")
    assert result == "hello from sandbox"
    print("test_execute_generated_code_runs_safe_code_normally: PASS")


# ── desktop_control(): the new confirmation gate on action='task' ──────

def test_desktop_control_task_without_confirmation_never_calls_gemini() -> None:
    with patch.object(desktop, "_ask_gemini_for_desktop_action") as m_ask:
        result = desktop.desktop_control(parameters={"action": "task", "task": "delete everything"})
    m_ask.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_desktop_control_task_without_confirmation_never_calls_gemini: PASS")

def test_desktop_control_task_with_confirmed_true_proceeds() -> None:
    with patch.object(desktop, "_ask_gemini_for_desktop_action", return_value="print('ok')") as m_ask, \
         patch.object(desktop, "_execute_generated_code", return_value="ok") as m_exec:
        result = desktop.desktop_control(parameters={"action": "task", "task": "say ok", "confirmed": True})
    m_ask.assert_called_once()
    m_exec.assert_called_once()
    assert result == "ok"
    print("test_desktop_control_task_with_confirmed_true_proceeds: PASS")

def test_desktop_control_free_text_task_param_also_requires_confirmation() -> None:
    # The `elif action == "task" or task:` branch covers BOTH action='task'
    # AND a bare `task=` param with no action at all — both paths must be
    # gated identically.
    with patch.object(desktop, "_ask_gemini_for_desktop_action") as m_ask:
        result = desktop.desktop_control(parameters={"task": "reorganize my whole drive"})
    m_ask.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_desktop_control_free_text_task_param_also_requires_confirmation: PASS")

def test_desktop_control_unknown_action_fallback_also_requires_confirmation() -> None:
    with patch.object(desktop, "_ask_gemini_for_desktop_action") as m_ask:
        result = desktop.desktop_control(parameters={"action": "do something weird"})
    m_ask.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_desktop_control_unknown_action_fallback_also_requires_confirmation: PASS")

def test_desktop_control_named_safe_actions_unaffected_by_the_new_gate() -> None:
    # Regression guard: list/stats/organize/clean/wallpaper must NOT
    # suddenly require confirmation — only the code-generation path does.
    with patch.object(desktop, "list_desktop", return_value="Desktop (0 items)"):
        result = desktop.desktop_control(parameters={"action": "list"})
    assert result == "Desktop (0 items)"
    assert not result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_desktop_control_named_safe_actions_unaffected_by_the_new_gate: PASS")


if __name__ == "__main__":
    test_validator_rejects_import_statements()
    test_validator_rejects_import_from()
    test_validator_rejects_the_classic_no_import_needed_escape_gadget()
    test_validator_rejects_globals_dunder_access()
    test_validator_rejects_blocked_call_names()
    test_validator_rejects_syntax_errors_without_crashing()
    test_validator_accepts_legitimate_safe_code()
    test_sandbox_no_longer_exposes_getattr_or_hasattr()
    test_sandbox_no_longer_exposes_raw_ctypes()
    test_code_using_removed_ctypes_fails_at_exec_time_never_runs()
    test_execute_generated_code_blocks_the_unsafe_sentinel()
    test_execute_generated_code_blocks_rejected_code_before_exec()
    test_execute_generated_code_runs_safe_code_normally()
    test_desktop_control_task_without_confirmation_never_calls_gemini()
    test_desktop_control_task_with_confirmed_true_proceeds()
    test_desktop_control_free_text_task_param_also_requires_confirmation()
    test_desktop_control_unknown_action_fallback_also_requires_confirmation()
    test_desktop_control_named_safe_actions_unaffected_by_the_new_gate()
    print("\nAll desktop_safety tests passed.")
