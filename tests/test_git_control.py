"""
tests/test_git_control.py — actions/git_control.py (J9) in isolation:
repository-root reuse from repo_agent.py, read-only Git state, staging,
confirmation-gated commit with real post-condition verification, and the
explicit action allowlist (permanently-blocked / known-unsupported /
unknown actions).

Per this project's own established convention: real, isolated temp
directories are used for anything that touches the actual filesystem (a
disposable temp dir lives under the user's own home directory on
Windows, so it passes file_controller.py's own _is_safe_path() without
needing to patch anything — see test_repo_agent.py's own convention
note). Every test repository here is a REAL `git init` repository with
REAL commits — this module's own subprocess calls are never mocked,
since git_control.py's entire value proposition is running the real
`git` binary safely; mocking it out would test nothing.

Run with:
    .venv/Scripts/python.exe -m tests.test_git_control
"""
import subprocess
import tempfile
from pathlib import Path

import actions.git_control as gc


def _init_repo(tmp: str) -> None:
    subprocess.run(["git", "init"], cwd=tmp, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "jarvis-test@example.com"], cwd=tmp, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "JARVIS Test"], cwd=tmp, capture_output=True, text=True)


def _commit_one(tmp: str, filename: str = "seed.txt", message: str = "seed commit") -> None:
    (Path(tmp) / filename).write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", message], cwd=tmp, capture_output=True, text=True)


# ── Repository-root reuse / boundary ─────────────────────────────────────

def test_resolve_and_check_rejects_a_path_outside_the_home_boundary() -> None:
    outside = "C:/Windows/System32" if Path.home().drive else "/etc"
    result = gc.status(outside)
    assert result.startswith("[BLOCKED]")
    assert "access denied" in result.lower()
    print("test_resolve_and_check_rejects_a_path_outside_the_home_boundary: PASS")


def test_resolve_and_check_rejects_a_nonexistent_repository() -> None:
    result = gc.status(r"C:\this\path\genuinely\does\not\exist\anywhere_git")
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "not found" in result.lower()
    print("test_resolve_and_check_rejects_a_nonexistent_repository: PASS")


def test_resolve_and_check_rejects_a_directory_that_is_not_a_git_repository() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = gc.status(tmp)
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "not a git repository" in result.lower()
    print("test_resolve_and_check_rejects_a_directory_that_is_not_a_git_repository: PASS")


# ── Read-only actions ─────────────────────────────────────────────────────

def test_status_on_a_real_empty_repository_is_verified_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        result = gc.status(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "branch" in result.lower()
    print("test_status_on_a_real_empty_repository_is_verified_success: PASS")


def test_diff_returns_real_repository_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        _commit_one(tmp)
        (Path(tmp) / "seed.txt").write_text("seed\nsecond line\n", encoding="utf-8")
        result = gc.diff(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "second line" in result
    print("test_diff_returns_real_repository_state: PASS")


def test_diff_with_no_changes_is_still_verified_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        _commit_one(tmp)
        result = gc.diff(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "no uncommitted changes" in result.lower()
    print("test_diff_with_no_changes_is_still_verified_success: PASS")


def test_log_returns_real_commit_history() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        _commit_one(tmp, "a.txt", "First real commit XYZ123")
        result = gc.log(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "First real commit XYZ123" in result
    print("test_log_returns_real_commit_history: PASS")


def test_log_on_a_brand_new_repository_honestly_reports_no_commits() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        result = gc.log(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "no commits yet" in result.lower()
    print("test_log_on_a_brand_new_repository_honestly_reports_no_commits: PASS")


def test_branch_lists_real_local_branches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        _commit_one(tmp)
        result = gc.branch(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_branch_lists_real_local_branches: PASS")


# ── Staging ────────────────────────────────────────────────────────────────

def test_stage_all_verifies_the_real_index_afterward() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        (Path(tmp) / "new_file.txt").write_text("content\n", encoding="utf-8")
        result = gc.stage(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "new_file.txt" in result
    print("test_stage_all_verifies_the_real_index_afterward: PASS")


def test_stage_nothing_to_stage_is_still_honest_verified_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        _commit_one(tmp)
        result = gc.stage(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "nothing to stage" in result.lower()
    print("test_stage_nothing_to_stage_is_still_honest_verified_success: PASS")


def test_stage_one_explicit_path_stages_only_that_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        (Path(tmp) / "one.txt").write_text("1\n", encoding="utf-8")
        (Path(tmp) / "two.txt").write_text("2\n", encoding="utf-8")
        result = gc.stage(tmp, "one.txt")
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "one.txt" in result and "two.txt" not in result
    print("test_stage_one_explicit_path_stages_only_that_file: PASS")


def test_stage_path_traversal_outside_the_repository_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        result = gc.stage(tmp, "../../../../Windows/System32/notepad.exe")
    assert result.startswith("[BLOCKED]")
    print("test_stage_path_traversal_outside_the_repository_is_blocked: PASS")


# ── Commit ─────────────────────────────────────────────────────────────────

def test_commit_without_a_message_is_inconclusive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        (Path(tmp) / "f.txt").write_text("x\n", encoding="utf-8")
        gc.stage(tmp)
        result = gc.commit(tmp, "", confirmed=True)
    assert result.startswith("[INCONCLUSIVE]")
    print("test_commit_without_a_message_is_inconclusive: PASS")


def test_commit_with_nothing_staged_is_inconclusive_and_creates_no_commit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        result = gc.commit(tmp, "should not happen", confirmed=True)
        log_after = gc.log(tmp)
    assert result.startswith("[INCONCLUSIVE]")
    assert "no commits yet" in log_after.lower()
    print("test_commit_with_nothing_staged_is_inconclusive_and_creates_no_commit: PASS")


def test_commit_without_confirmation_returns_confirmation_required_and_creates_no_commit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        (Path(tmp) / "f.txt").write_text("x\n", encoding="utf-8")
        gc.stage(tmp)
        result = gc.commit(tmp, "Should not be created", confirmed=False)
        log_after = gc.log(tmp)
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    assert "no commits yet" in log_after.lower()
    print("test_commit_without_confirmation_returns_confirmation_required_and_creates_no_commit: PASS")


def test_confirmed_commit_actually_creates_a_real_commit_verified_independently() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        (Path(tmp) / "f.txt").write_text("x\n", encoding="utf-8")
        gc.stage(tmp)
        result = gc.commit(tmp, "JARVIS GIT TEST 4821", confirmed=True)
        # Independent verification — a direct, non-mocked git subprocess
        # call outside git_control.py's own code path.
        subj = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=tmp, capture_output=True, text=True).stdout.strip()
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, capture_output=True, text=True).stdout.strip()
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert subj == "JARVIS GIT TEST 4821"
    assert head[:8] in result
    print("test_confirmed_commit_actually_creates_a_real_commit_verified_independently: PASS")


def test_commit_verification_detects_the_new_commit_head_actually_changed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        _commit_one(tmp, "a.txt", "first")
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, capture_output=True, text=True).stdout.strip()
        (Path(tmp) / "b.txt").write_text("b\n", encoding="utf-8")
        gc.stage(tmp)
        gc.commit(tmp, "second", confirmed=True)
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, capture_output=True, text=True).stdout.strip()
    assert head_before != head_after
    print("test_commit_verification_detects_the_new_commit_head_actually_changed: PASS")


def test_commit_never_creates_a_second_commit_when_repeated_with_nothing_newly_staged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        (Path(tmp) / "f.txt").write_text("x\n", encoding="utf-8")
        gc.stage(tmp)
        gc.commit(tmp, "only commit", confirmed=True)
        # Nothing new staged — a second commit call must not fabricate a commit.
        second = gc.commit(tmp, "should not happen again", confirmed=True)
        count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=tmp, capture_output=True, text=True).stdout.strip()
    assert second.startswith("[INCONCLUSIVE]")
    assert count == "1"
    print("test_commit_never_creates_a_second_commit_when_repeated_with_nothing_newly_staged: PASS")


# ── Safety allowlist (red-team) ──────────────────────────────────────────

def test_force_push_is_blocked_without_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        result = gc.git_control(parameters={"action": "force_push", "repo_root": tmp})
    assert result.startswith("[BLOCKED]")
    print("test_force_push_is_blocked_without_execution: PASS")


def test_push_pull_fetch_are_all_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        for action in ("push", "pull", "fetch"):
            result = gc.git_control(parameters={"action": action, "repo_root": tmp})
            assert result.startswith("[BLOCKED]"), f"{action} was not blocked: {result}"
    print("test_push_pull_fetch_are_all_blocked: PASS")


def test_reset_hard_is_blocked_without_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        _commit_one(tmp)
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, capture_output=True, text=True).stdout.strip()
        result = gc.git_control(parameters={"action": "reset_hard", "repo_root": tmp})
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, capture_output=True, text=True).stdout.strip()
    assert result.startswith("[BLOCKED]")
    assert head_before == head_after
    print("test_reset_hard_is_blocked_without_execution: PASS")


def test_destructive_clean_is_blocked_without_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        (Path(tmp) / "untracked.txt").write_text("keep me\n", encoding="utf-8")
        result = gc.git_control(parameters={"action": "clean", "repo_root": tmp})
        assert result.startswith("[BLOCKED]")
        assert (Path(tmp) / "untracked.txt").exists()
    print("test_destructive_clean_is_blocked_without_execution: PASS")


def test_rebase_and_merge_and_remote_admin_are_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        for action in ("rebase", "merge", "remote", "filter_branch", "gc", "prune"):
            result = gc.git_control(parameters={"action": action, "repo_root": tmp})
            assert result.startswith("[BLOCKED]"), f"{action} was not blocked: {result}"
    print("test_rebase_and_merge_and_remote_admin_are_blocked: PASS")


def test_no_arbitrary_command_parameter_is_ever_recognized() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        # There is no 'command' action shape — an attempt to smuggle a
        # raw git argv string through the 'action' field itself is just
        # an unrecognized action name, refused the same as any other.
        result = gc.git_control(parameters={
            "action": "status; rm -rf /",
            "repo_root": tmp,
        })
    assert result.startswith("[BLOCKED]")
    print("test_no_arbitrary_command_parameter_is_ever_recognized: PASS")


def test_unknown_action_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        result = gc.git_control(parameters={"action": "totally_made_up_action", "repo_root": tmp})
    assert result.startswith("[BLOCKED]")
    print("test_unknown_action_is_blocked: PASS")


def test_checkout_switch_are_recognized_but_honestly_unsupported_not_executed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_repo(tmp)
        _commit_one(tmp)
        for action in ("checkout", "switch"):
            result = gc.git_control(parameters={"action": action, "repo_root": tmp})
            assert result.startswith("[INCONCLUSIVE]"), f"{action}: {result}"
    print("test_checkout_switch_are_recognized_but_honestly_unsupported_not_executed: PASS")


def test_repository_outside_the_allowed_path_is_blocked_not_executed() -> None:
    outside = "C:/Windows/System32" if Path.home().drive else "/etc"
    for action in ("status", "stage", "commit"):
        result = gc.git_control(parameters={"action": action, "repo_root": outside})
        assert result.startswith("[BLOCKED]"), f"{action}: {result}"
    print("test_repository_outside_the_allowed_path_is_blocked_not_executed: PASS")


def _run() -> None:
    test_resolve_and_check_rejects_a_path_outside_the_home_boundary()
    test_resolve_and_check_rejects_a_nonexistent_repository()
    test_resolve_and_check_rejects_a_directory_that_is_not_a_git_repository()
    test_status_on_a_real_empty_repository_is_verified_success()
    test_diff_returns_real_repository_state()
    test_diff_with_no_changes_is_still_verified_success()
    test_log_returns_real_commit_history()
    test_log_on_a_brand_new_repository_honestly_reports_no_commits()
    test_branch_lists_real_local_branches()
    test_stage_all_verifies_the_real_index_afterward()
    test_stage_nothing_to_stage_is_still_honest_verified_success()
    test_stage_one_explicit_path_stages_only_that_file()
    test_stage_path_traversal_outside_the_repository_is_blocked()
    test_commit_without_a_message_is_inconclusive()
    test_commit_with_nothing_staged_is_inconclusive_and_creates_no_commit()
    test_commit_without_confirmation_returns_confirmation_required_and_creates_no_commit()
    test_confirmed_commit_actually_creates_a_real_commit_verified_independently()
    test_commit_verification_detects_the_new_commit_head_actually_changed()
    test_commit_never_creates_a_second_commit_when_repeated_with_nothing_newly_staged()
    test_force_push_is_blocked_without_execution()
    test_push_pull_fetch_are_all_blocked()
    test_reset_hard_is_blocked_without_execution()
    test_destructive_clean_is_blocked_without_execution()
    test_rebase_and_merge_and_remote_admin_are_blocked()
    test_no_arbitrary_command_parameter_is_ever_recognized()
    test_unknown_action_is_blocked()
    test_checkout_switch_are_recognized_but_honestly_unsupported_not_executed()
    test_repository_outside_the_allowed_path_is_blocked_not_executed()
    print("\nAll git_control tests passed.")


if __name__ == "__main__":
    _run()
