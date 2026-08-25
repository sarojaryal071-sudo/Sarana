"""
tests/test_console_encoding.py — regression test for the ACTUAL root cause
of "SARANA does not greet after login" (Priority 1): print()ing a live
transcript containing Devanagari/Nepali script (routine now that Nepali is
the default response language) raised UnicodeEncodeError on a legacy
Windows console codepage (cp1252). That crash happened inside
_receive_audio() (core/headless_surface.py's write_log(), called from
main.py's transcript-logging code), which cancelled the ENTIRE run()
TaskGroup -- every sibling task, including whatever greeting/response was
mid-stream -- forcing a reconnect and cutting the greeting off.
Live-reproduced against a real Gemini connection before this fix; fixed by
reconfiguring stdout/stderr to UTF-8 with errors="replace" once, at
main.py's own import time (see the top of that file), so no individual
print() call site needs to be hand-verified ASCII-only.

Run with:
    .venv/Scripts/python.exe -m tests.test_console_encoding
"""
import sys

import main   # importing this is what performs the stdout/stderr reconfigure
from core.headless_surface import HeadlessSurface


def test_stdout_reconfigured_to_utf8() -> None:
    assert sys.stdout.encoding is not None
    assert sys.stdout.encoding.lower().replace("-", "") in ("utf8",), (
        f"expected stdout to be reconfigured to utf-8, got {sys.stdout.encoding!r}"
    )
    print("test_stdout_reconfigured_to_utf8: PASS")


def test_stderr_reconfigured_to_utf8() -> None:
    assert sys.stderr.encoding is not None
    assert sys.stderr.encoding.lower().replace("-", "") in ("utf8",), (
        f"expected stderr to be reconfigured to utf-8, got {sys.stderr.encoding!r}"
    )
    print("test_stderr_reconfigured_to_utf8: PASS")


def test_write_log_survives_devanagari_transcript() -> None:
    """The exact live-reproduced crash: a user-speech transcript containing
    Nepali script, logged via write_log() -- must not raise."""
    h = HeadlessSurface()
    h.write_log("You: आज के गरौँ?")   # must not raise UnicodeEncodeError
    print("test_write_log_survives_devanagari_transcript: PASS")


def test_write_log_survives_arbitrary_unicode_including_emoji() -> None:
    """Broader than the one reproduced bug — any print() in the process
    (not just write_log) must now survive arbitrary Unicode content,
    covering the same class of bug the earlier hardcoded-emoji fixes
    patched one call site at a time."""
    h = HeadlessSurface()
    h.write_log("SARANA: नमस्ते! 🎉 Öäü — 你好 — עברית")
    print("test_write_log_survives_arbitrary_unicode_including_emoji: PASS")


def test_print_directly_survives_devanagari() -> None:
    """Not every crash site was write_log() specifically — some are plain
    print() calls in main.py itself. Confirm the fix is at the stream
    level (sys.stdout), not just inside one wrapper function."""
    print("[JARVIS] राम्रो — direct print, not via write_log")
    print("test_print_directly_survives_devanagari: PASS")


if __name__ == "__main__":
    test_stdout_reconfigured_to_utf8()
    test_stderr_reconfigured_to_utf8()
    test_write_log_survives_devanagari_transcript()
    test_write_log_survives_arbitrary_unicode_including_emoji()
    test_print_directly_survives_devanagari()
    print("\nAll console-encoding tests passed.")
