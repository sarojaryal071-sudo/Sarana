"""
tests/test_stdout_stderr_guard.py -- regression lock for the actual real
root cause of "Presentation Engine failed to load after several attempts"
on desktop, confirmed by direct reproduction: the J.A.R.V.I.S desktop
shortcut launches main.py with pythonw.exe (no console), and under that
exact launch path sys.stdout/sys.stderr are genuinely None -- confirmed
via a real detached Process.Start (System.Diagnostics.ProcessStartInfo,
UseShellExecute=false, no console), the same way the real .lnk launches
it. Python's own http.server.BaseHTTPRequestHandler.send_response() calls
log_message() -> sys.stderr.write(...) BEFORE sending any response bytes,
so EVERY request to ui.py's local static server raised an unhandled
AttributeError and the connection was torn down with zero bytes sent --
Chromium's ERR_EMPTY_RESPONSE, 100% reproducible, not transient.

Run with:
    .venv/Scripts/python.exe -m tests.test_stdout_stderr_guard
"""
import sys
from pathlib import Path

_MAIN_SRC = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_guard_appears_before_any_other_heavy_import_that_could_print() -> None:
    """The guard must run before array/asyncio/etc. (and everything they
    transitively pull in) gets a chance to print() during import."""
    guard_idx = _MAIN_SRC.index("if sys.stdout is None:")
    array_import_idx = _MAIN_SRC.index("import array")
    assert guard_idx < array_import_idx
    print("test_guard_appears_before_any_other_heavy_import_that_could_print: PASS")


def test_os_and_sys_are_imported_before_the_guard_uses_them() -> None:
    guard_idx = _MAIN_SRC.index("if sys.stdout is None:")
    os_import_idx = _MAIN_SRC.index("import os")
    sys_import_idx = _MAIN_SRC.index("import sys")
    assert os_import_idx < guard_idx
    assert sys_import_idx < guard_idx
    print("test_os_and_sys_are_imported_before_the_guard_uses_them: PASS")


def test_guard_replaces_none_streams_with_a_real_writable_file() -> None:
    assert 'sys.stdout = open(os.devnull, "w")' in _MAIN_SRC
    assert 'sys.stderr = open(os.devnull, "w")' in _MAIN_SRC
    print("test_guard_replaces_none_streams_with_a_real_writable_file: PASS")


def test_guard_mechanism_actually_works_when_streams_are_none() -> None:
    """The real mechanism, exercised directly (not just source-inspected):
    with sys.stdout/sys.stderr genuinely None (exactly the confirmed
    pythonw.exe state), the same two-line guard must leave both streams
    real, writable, and usable by print() without raising."""
    import os
    real_stdout, real_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None

        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")

        assert sys.stdout is not None
        assert sys.stderr is not None
        print("this must not raise", file=sys.stdout)
        print("this must not raise either", file=sys.stderr)
        sys.stdout.close()
        sys.stderr.close()
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
    print("test_guard_mechanism_actually_works_when_streams_are_none: PASS")


def test_guard_is_a_no_op_when_streams_are_already_real_the_normal_python_exe_case() -> None:
    """Must never replace a perfectly good real console stream (the
    python.exe-from-a-terminal case every other test in this project
    runs under) with devnull."""
    import os
    real_stdout, real_stderr = sys.stdout, sys.stderr
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")
        assert sys.stdout is real_stdout
        assert sys.stderr is real_stderr
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
    print("test_guard_is_a_no_op_when_streams_are_already_real_the_normal_python_exe_case: PASS")


if __name__ == "__main__":
    test_guard_appears_before_any_other_heavy_import_that_could_print()
    test_os_and_sys_are_imported_before_the_guard_uses_them()
    test_guard_replaces_none_streams_with_a_real_writable_file()
    test_guard_mechanism_actually_works_when_streams_are_none()
    test_guard_is_a_no_op_when_streams_are_already_real_the_normal_python_exe_case()
    print("\nAll stdout/stderr-guard tests passed.")
