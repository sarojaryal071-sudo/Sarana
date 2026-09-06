"""
tests/test_file_controller.py — targeted corrections to two confirmed,
pre-existing bugs found during real-world JARVIS testing (see the
diagnostic report): _resolve_path() couldn't handle a shortcut-prefixed
subpath ("Desktop/Consumer behaviour"), and find_files() never matched
directories at all, only files.

Per this project's own established convention: real, isolated temp
directories are used for find_files()'s directory-matching tests (never
the user's real home tree) — a standard tempfile.TemporaryDirectory()
lives under the user's own home directory on Windows, so it passes
_is_safe_path() without needing to patch anything.

Run with:
    .venv/Scripts/python.exe -m tests.test_file_controller
"""
import tempfile
from pathlib import Path

import actions.file_controller as fc


# ── _resolve_path: shortcut-prefixed subpaths ───────────────────────────

def test_resolve_path_bare_shortcuts_are_unaffected() -> None:
    assert fc._resolve_path("desktop") == fc._get_desktop()
    assert fc._resolve_path("Desktop") == fc._get_desktop()
    assert fc._resolve_path("downloads") == fc._get_downloads()
    assert fc._resolve_path("home") == Path.home()
    print("test_resolve_path_bare_shortcuts_are_unaffected: PASS")


def test_resolve_path_shortcut_with_forward_slash_subpath() -> None:
    assert fc._resolve_path("Desktop/Consumer behaviour") == fc._get_desktop() / "Consumer behaviour"
    assert fc._resolve_path("Desktop/Images") == fc._get_desktop() / "Images"
    print("test_resolve_path_shortcut_with_forward_slash_subpath: PASS")


def test_resolve_path_shortcut_with_backslash_subpath() -> None:
    assert fc._resolve_path("Desktop\\Images") == fc._get_desktop() / "Images"
    print("test_resolve_path_shortcut_with_backslash_subpath: PASS")


def test_resolve_path_shortcut_with_multi_segment_subpath() -> None:
    assert fc._resolve_path("Downloads/some-folder/nested") == fc._get_downloads() / "some-folder" / "nested"
    print("test_resolve_path_shortcut_with_multi_segment_subpath: PASS")


def test_resolve_path_shortcut_matching_is_case_insensitive() -> None:
    assert fc._resolve_path("DESKTOP/Images") == fc._get_desktop() / "Images"
    print("test_resolve_path_shortcut_matching_is_case_insensitive: PASS")


def test_resolve_path_non_shortcut_absolute_path_is_unaffected() -> None:
    # A real absolute path that doesn't start with a shortcut token must
    # keep behaving exactly as before (existing fallback, unchanged).
    home_str = str(Path.home())
    assert fc._resolve_path(home_str) == Path(home_str).expanduser()
    print("test_resolve_path_non_shortcut_absolute_path_is_unaffected: PASS")


# ── find_files: must locate directories too, without losing file search ─

def test_find_files_locates_a_directory_by_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Consumer behaviour").mkdir()
        result = fc.find_files(name="Consumer behaviour", path=str(root))
        assert "Consumer behaviour" in result
        assert "No Consumer behaviour found" not in result
    print("test_find_files_locates_a_directory_by_name: PASS")


def test_find_files_still_locates_files_by_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "notes.txt").write_text("hi")
        result = fc.find_files(name="notes", path=str(root))
        assert "notes.txt" in result
    print("test_find_files_still_locates_files_by_name: PASS")


def test_find_files_extension_search_still_matches_only_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "report.pdf").mkdir()          # a FOLDER that happens to look like a .pdf name
        (root / "real_report.pdf").write_text("x")
        result = fc.find_files(extension=".pdf", path=str(root))
        assert "real_report.pdf" in result
        assert "📁" not in result              # the folder must never be matched via extension
    print("test_find_files_extension_search_still_matches_only_files: PASS")


def test_find_files_with_no_match_reports_honestly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = fc.find_files(name="DoesNotExist12345", path=tmp)
        assert "No DoesNotExist12345 found" in result
    print("test_find_files_with_no_match_reports_honestly: PASS")


def _run() -> None:
    test_resolve_path_bare_shortcuts_are_unaffected()
    test_resolve_path_shortcut_with_forward_slash_subpath()
    test_resolve_path_shortcut_with_backslash_subpath()
    test_resolve_path_shortcut_with_multi_segment_subpath()
    test_resolve_path_shortcut_matching_is_case_insensitive()
    test_resolve_path_non_shortcut_absolute_path_is_unaffected()
    test_find_files_locates_a_directory_by_name()
    test_find_files_still_locates_files_by_name()
    test_find_files_extension_search_still_matches_only_files()
    test_find_files_with_no_match_reports_honestly()
    print("\nAll file_controller tests passed.")


if __name__ == "__main__":
    _run()
