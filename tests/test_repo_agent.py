"""
tests/test_repo_agent.py — actions/repo_agent.py (J8) in isolation:
repository-root resolution, repo-wide content search, test-command
detection/execution, and the confirmation-gated edit action.

Per this project's own established convention: real, isolated temp
directories are used for anything that touches the actual filesystem
(a disposable temp dir lives under the user's own home directory on
Windows, so it passes file_controller.py's _is_safe_path() without
needing to patch anything — see test_file_controller.py's own
convention note). code_helper.py's actual Gemini-driven content
generation is mocked in the edit tests (no real API calls here); the
real, non-mocked repo-agent -> code_helper -> real file write path is
exercised in the J8 completion report's own real E2E, not repeated here
on every test pass.

Run with:
    .venv/Scripts/python.exe -m tests.test_repo_agent
"""
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import actions.repo_agent as ra


# ── Repository discovery ─────────────────────────────────────────────────

def test_resolve_repo_root_defaults_to_the_jarvis_repo_itself() -> None:
    root, err = ra.resolve_repo_root("")
    assert err is None
    assert root == ra._JARVIS_REPO_ROOT
    assert (root / "actions" / "repo_agent.py").exists()
    print("test_resolve_repo_root_defaults_to_the_jarvis_repo_itself: PASS")


def test_resolve_repo_root_accepts_a_real_explicit_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root, err = ra.resolve_repo_root(tmp)
    assert err is None
    assert root == Path(tmp).resolve()
    print("test_resolve_repo_root_accepts_a_real_explicit_directory: PASS")


def test_resolve_repo_root_rejects_a_nonexistent_path() -> None:
    root, err = ra.resolve_repo_root(r"C:\this\path\genuinely\does\not\exist\anywhere")
    assert root is None
    assert "not found" in err.lower()
    print("test_resolve_repo_root_rejects_a_nonexistent_path: PASS")


def test_resolve_repo_root_rejects_a_file_not_a_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "notadir.txt"
        f.write_text("x")
        root, err = ra.resolve_repo_root(str(f))
    assert root is None
    assert "not a directory" in err.lower()
    print("test_resolve_repo_root_rejects_a_file_not_a_directory: PASS")


def test_resolve_repo_root_rejects_a_path_outside_the_home_boundary() -> None:
    outside = "C:/Windows/System32" if Path.home().drive else "/etc"
    root, err = ra.resolve_repo_root(outside)
    assert root is None
    assert "access denied" in err.lower()
    print("test_resolve_repo_root_rejects_a_path_outside_the_home_boundary: PASS")


# ── Search ────────────────────────────────────────────────────────────────

def test_search_finds_a_real_match_with_line_context() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "sample.py").write_text("def jarvis_task():\n    return 42\n")
        result = ra.search_repository("jarvis_task", tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "sample.py:1" in result
    print("test_search_finds_a_real_match_with_line_context: PASS")


def test_search_reports_multiple_matches_across_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.py").write_text("TARGET_SYMBOL = 1\n")
        (Path(tmp) / "b.py").write_text("print(TARGET_SYMBOL)\n")
        result = ra.search_repository("TARGET_SYMBOL", tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "a.py" in result and "b.py" in result
    assert "2 file(s)" in result
    print("test_search_reports_multiple_matches_across_files: PASS")


def test_search_with_no_matches_is_still_verified_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.py").write_text("nothing interesting here\n")
        result = ra.search_repository("totally_absent_symbol_xyz", tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "No matches" in result
    print("test_search_with_no_matches_is_still_verified_success: PASS")


def test_search_skips_unreadable_binary_files_without_crashing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "data.bin").write_bytes(bytes(range(256)))
        (Path(tmp) / "readme.txt").write_text("findme here")
        result = ra.search_repository("findme", tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "readme.txt" in result
    print("test_search_skips_unreadable_binary_files_without_crashing: PASS")


def test_search_never_descends_into_excluded_directories() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        git_dir = Path(tmp) / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("findme_in_git")
        (Path(tmp) / "real.py").write_text("nothing relevant")
        result = ra.search_repository("findme_in_git", tmp)
    assert "No matches" in result
    print("test_search_never_descends_into_excluded_directories: PASS")


def test_search_with_empty_query_is_inconclusive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = ra.search_repository("", tmp)
    assert result.startswith("[INCONCLUSIVE]")
    print("test_search_with_empty_query_is_inconclusive: PASS")


def test_search_real_jarvis_repo_for_a_known_symbol() -> None:
    # Real, non-mocked, read-only search against the actual JARVIS repo.
    result = ra.search_repository("_RECOVERY_CHAIN")
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "task_engine.py" in result
    print("test_search_real_jarvis_repo_for_a_known_symbol: PASS")


# ── Test-run integration ──────────────────────────────────────────────────

def test_detect_test_command_finds_per_file_python_convention() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tests_dir = Path(tmp) / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_one.py").write_text("print('ok')")
        detected = ra._detect_test_command(Path(tmp))
    assert detected["kind"] == "per_file_python"
    assert "test_one" in detected["modules"]
    print("test_detect_test_command_finds_per_file_python_convention: PASS")


def test_detect_test_command_prefers_npm_when_package_json_has_a_test_script() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
        detected = ra._detect_test_command(Path(tmp))
    assert detected["kind"] == "npm"
    print("test_detect_test_command_prefers_npm_when_package_json_has_a_test_script: PASS")


def test_detect_test_command_returns_none_when_nothing_matches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        detected = ra._detect_test_command(Path(tmp))
    assert detected is None
    print("test_detect_test_command_returns_none_when_nothing_matches: PASS")


def test_run_tests_reports_inconclusive_when_no_command_detected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = ra.run_tests(tmp)
    assert result.startswith("[INCONCLUSIVE]")
    print("test_run_tests_reports_inconclusive_when_no_command_detected: PASS")


def test_run_tests_real_passing_per_file_python_suite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tests_dir = Path(tmp) / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_good.py").write_text("print('all good')\n")
        result = ra.run_tests(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_run_tests_real_passing_per_file_python_suite: PASS")


def test_run_tests_real_failing_per_file_python_suite_is_verified_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tests_dir = Path(tmp) / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_bad.py").write_text("raise ValueError('deliberately broken')\n")
        result = ra.run_tests(tmp)
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "test_bad" in result
    print("test_run_tests_real_failing_per_file_python_suite_is_verified_failure: PASS")


def test_run_tests_never_reports_success_merely_because_a_process_launched() -> None:
    # Result-spoofing guard: a test module that exits non-zero must never
    # be upgraded to VERIFIED_SUCCESS just because SOMETHING ran.
    with tempfile.TemporaryDirectory() as tmp:
        tests_dir = Path(tmp) / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_a.py").write_text("print('fine')\n")
        (tests_dir / "test_b.py").write_text("import sys; sys.exit(1)\n")
        result = ra.run_tests(tmp)
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_run_tests_never_reports_success_merely_because_a_process_launched: PASS")


# ── Modification (edit) ──────────────────────────────────────────────────

def test_edit_requires_both_a_file_path_and_an_instruction() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert ra.edit_file("", "do something", tmp).startswith("[INCONCLUSIVE]")
        assert ra.edit_file("a.py", "", tmp).startswith("[INCONCLUSIVE]")
    print("test_edit_requires_both_a_file_path_and_an_instruction: PASS")


def test_edit_of_a_nonexistent_file_is_verified_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = ra.edit_file("does_not_exist.py", "add a comment", tmp, confirmed=True)
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_edit_of_a_nonexistent_file_is_verified_failure: PASS")


def test_edit_outside_the_repository_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
        outside_file = Path(other) / "outside.py"
        outside_file.write_text("x = 1")
        result = ra.edit_file(str(outside_file), "change x", tmp, confirmed=True)
    assert result.startswith("[BLOCKED]")
    print("test_edit_outside_the_repository_is_blocked: PASS")


def test_edit_without_confirmation_requires_it_and_never_calls_code_helper() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "helper.py"
        target.write_text("x = 1\n")
        with patch("actions.repo_agent._code_helper") as m_ch:
            result = ra.edit_file("helper.py", "change x to 2", tmp, confirmed=False)
        m_ch.assert_not_called()
        assert result.startswith("[CONFIRMATION_REQUIRED]")
        assert target.read_text() == "x = 1\n"
    print("test_edit_without_confirmation_requires_it_and_never_calls_code_helper: PASS")


def test_edit_confirmed_true_calls_code_helper_and_verifies_the_change() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "helper.py"
        target.write_text("x = 1\n")

        def fake_edit(parameters):
            Path(parameters["file_path"]).write_text("x = 2\n")
            return "File edited. Saved to: helper.py"

        with patch("actions.repo_agent._code_helper", side_effect=fake_edit) as m_ch:
            result = ra.edit_file("helper.py", "change x to 2", tmp, confirmed=True)
        m_ch.assert_called_once()
        assert result.startswith("[VERIFIED_SUCCESS]")
        assert target.read_text() == "x = 2\n"
    print("test_edit_confirmed_true_calls_code_helper_and_verifies_the_change: PASS")


def test_edit_that_does_not_actually_change_the_file_is_inconclusive() -> None:
    # Result-spoofing guard: code_helper.py reporting success is not
    # enough -- the file content must have actually changed.
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "helper.py"
        target.write_text("x = 1\n")
        with patch("actions.repo_agent._code_helper", return_value="File edited (allegedly)."):
            result = ra.edit_file("helper.py", "change x to 2", tmp, confirmed=True)
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_edit_that_does_not_actually_change_the_file_is_inconclusive: PASS")


# ── Safety / red-team ─────────────────────────────────────────────────────

def test_no_action_exists_that_can_delete_or_wipe_the_repository() -> None:
    # repo_agent's own action surface is exactly {search, run_tests, edit}
    # -- there is structurally no "delete"/"wipe"/"remove" action to
    # reach, regardless of phrasing.
    with tempfile.TemporaryDirectory() as tmp:
        result = ra.repo_agent(parameters={"action": "delete", "repo_root": tmp})
        assert result.startswith("[INCONCLUSIVE]")
        result2 = ra.repo_agent(parameters={"action": "wipe", "repo_root": tmp})
        assert result2.startswith("[INCONCLUSIVE]")
    print("test_no_action_exists_that_can_delete_or_wipe_the_repository: PASS")


def test_no_arbitrary_shell_command_action_exists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = ra.repo_agent(parameters={
            "action": "shell", "command": "rm -rf /", "repo_root": tmp,
        })
    assert result.startswith("[INCONCLUSIVE]")
    print("test_no_arbitrary_shell_command_action_exists: PASS")


def test_path_traversal_edit_target_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = ra.edit_file("../../../../../../../../Windows/win.ini", "change it", tmp, confirmed=True)
    assert result.startswith("[BLOCKED]")
    print("test_path_traversal_edit_target_is_blocked: PASS")


def test_search_root_outside_home_is_blocked_not_executed() -> None:
    outside = "C:/Windows/System32" if Path.home().drive else "/etc"
    result = ra.search_repository("anything", outside)
    assert result.startswith("[BLOCKED]")
    print("test_search_root_outside_home_is_blocked_not_executed: PASS")


def _run() -> None:
    test_resolve_repo_root_defaults_to_the_jarvis_repo_itself()
    test_resolve_repo_root_accepts_a_real_explicit_directory()
    test_resolve_repo_root_rejects_a_nonexistent_path()
    test_resolve_repo_root_rejects_a_file_not_a_directory()
    test_resolve_repo_root_rejects_a_path_outside_the_home_boundary()
    test_search_finds_a_real_match_with_line_context()
    test_search_reports_multiple_matches_across_files()
    test_search_with_no_matches_is_still_verified_success()
    test_search_skips_unreadable_binary_files_without_crashing()
    test_search_never_descends_into_excluded_directories()
    test_search_with_empty_query_is_inconclusive()
    test_search_real_jarvis_repo_for_a_known_symbol()
    test_detect_test_command_finds_per_file_python_convention()
    test_detect_test_command_prefers_npm_when_package_json_has_a_test_script()
    test_detect_test_command_returns_none_when_nothing_matches()
    test_run_tests_reports_inconclusive_when_no_command_detected()
    test_run_tests_real_passing_per_file_python_suite()
    test_run_tests_real_failing_per_file_python_suite_is_verified_failure()
    test_run_tests_never_reports_success_merely_because_a_process_launched()
    test_edit_requires_both_a_file_path_and_an_instruction()
    test_edit_of_a_nonexistent_file_is_verified_failure()
    test_edit_outside_the_repository_is_blocked()
    test_edit_without_confirmation_requires_it_and_never_calls_code_helper()
    test_edit_confirmed_true_calls_code_helper_and_verifies_the_change()
    test_edit_that_does_not_actually_change_the_file_is_inconclusive()
    test_no_action_exists_that_can_delete_or_wipe_the_repository()
    test_no_arbitrary_shell_command_action_exists()
    test_path_traversal_edit_target_is_blocked()
    test_search_root_outside_home_is_blocked_not_executed()
    print("\nAll repo_agent tests passed.")


if __name__ == "__main__":
    _run()
