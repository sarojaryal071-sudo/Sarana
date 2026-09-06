"""
tests/test_task_engine_j10.py — J10 (Autonomous Multi-Step Technical
Objectives): proves the EXISTING J4 multi-objective sequencing already
composes J6 (inspect)/J8 (repo_agent)/J9 (git) into one coherent
technical-objective lifecycle, and exercises the ONE genuine invariant
J10 actually adds — a repo_agent EDIT that succeeds must be followed by
a repo_agent RUN_TESTS that also succeeds before a git COMMIT in the
SAME task is allowed to proceed (section 8's "test-before-commit"
requirement, explicitly deferred by J9's own report: "that composition
is J10's job").

Per this project's own established convention: real, isolated temp
directories/repositories are used for anything that touches the actual
filesystem/git (see test_repo_agent.py's/test_git_control.py's own
convention notes). Only `actions.repo_agent._code_helper` (Gemini's own
content-generation call) is mocked here, exactly as test_repo_agent.py's
own edit tests already do — repo_agent.py's and git_control.py's own
real logic (search/run_tests/stage/commit, real git subprocess calls)
run for real and unmocked in every composed-workflow test below. The
ONE real, non-mocked Gemini-driven edit (repo_agent -> code_helper ->
real Gemini call) is exercised separately in the J10 completion report's
own real E2E script, not repeated here on every test pass.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_j10
"""
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import actions.task_engine as te
import actions.git_control as gc


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    return patch.dict(te._HANDLERS, overrides)


def _init_git_repo(tmp: str) -> None:
    subprocess.run(["git", "init"], cwd=tmp, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "jarvis-test@example.com"], cwd=tmp, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "JARVIS Test"], cwd=tmp, capture_output=True, text=True)


def _seed_buggy_repo(tmp: str) -> None:
    """A tiny, real, disposable project with one deliberate bug (`add`
    subtracts instead of adding) and the project's OWN actual test
    convention (a tests/ directory of standalone test_*.py modules) —
    same pattern J8's own real E2E used, per this file's own docstring."""
    _init_git_repo(tmp)
    (Path(tmp) / "calc.py").write_text("def add(a, b):\n    return a - b  # BUG: should add\n", encoding="utf-8")
    tests_dir = Path(tmp) / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_calc.py").write_text(
        "from calc import add\n"
        "assert add(2, 3) == 5, f'add(2,3) should be 5, got {add(2, 3)}'\n"
        "print('calc test passed')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial buggy seed"], cwd=tmp, capture_output=True, text=True)


def _fix_calc_py(parameters: dict) -> str:
    """Stands in for the real Gemini call inside code_helper.py's own
    edit action — writes the actual fix. Same technique test_repo_agent.py's
    own edit tests already use (patch.object(..., '_code_helper', side_effect=...))."""
    Path(parameters["file_path"]).write_text("def add(a, b):\n    return a + b  # fixed\n", encoding="utf-8")
    return "File edited. Saved to: calc.py"


def _pinned(tmp: str):
    """Patches BOTH repo_agent() and git_control() (as task_engine.py
    imports and calls them) to the SAME real, unmocked module functions,
    just pinned to the disposable repo — objective text never needs to
    carry a repo_root, exactly matching how a live objective ("fix the
    bug in calc.py") would arrive with no repo_root of its own."""
    import actions.repo_agent as ra

    def pinned_repo_agent(parameters):
        return ra.repo_agent(parameters={**parameters, "repo_root": tmp})

    def pinned_git_control(parameters):
        return gc.git_control(parameters={**parameters, "repo_root": tmp})

    return patch.object(te, "repo_agent", side_effect=pinned_repo_agent), \
        patch.object(te, "git_control", side_effect=pinned_git_control)


def _real_head_subject(tmp: str) -> str:
    return subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=tmp, capture_output=True, text=True).stdout.strip()


def _real_commit_count(tmp: str) -> str:
    return subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=tmp, capture_output=True, text=True).stdout.strip()


# ── Group A: the test-before-commit invariant, exercised directly ───────

def test_edit_success_marks_pending_test_verification() -> None:
    context = te.TaskContext()
    m_ra = MagicMock(return_value="[VERIFIED_SUCCESS] File edited.")
    with patch.object(te, "repo_agent", m_ra):
        te._run_repo_agent("fix calc.py to correct the bug", confirmed=True, context=context)
    assert context.values.get(te._PENDING_TEST_VERIFICATION_KEY) == "true"
    print("test_edit_success_marks_pending_test_verification: PASS")


def test_run_tests_success_clears_pending_flag() -> None:
    context = te.TaskContext()
    context.values[te._PENDING_TEST_VERIFICATION_KEY] = "true"
    m_ra = MagicMock(return_value="[VERIFIED_SUCCESS] All 1 test module(s) passed.")
    with patch.object(te, "repo_agent", m_ra):
        te._run_repo_agent("run the tests", confirmed=False, context=context)
    assert context.values.get(te._PENDING_TEST_VERIFICATION_KEY) == "false"
    print("test_run_tests_success_clears_pending_flag: PASS")


def test_run_tests_failure_leaves_pending_flag_set() -> None:
    context = te.TaskContext()
    context.values[te._PENDING_TEST_VERIFICATION_KEY] = "true"
    m_ra = MagicMock(return_value="[VERIFIED_FAILURE] 1 of 1 test module(s) failed.")
    with patch.object(te, "repo_agent", m_ra):
        te._run_repo_agent("run the tests", confirmed=False, context=context)
    assert context.values.get(te._PENDING_TEST_VERIFICATION_KEY) == "true"
    print("test_run_tests_failure_leaves_pending_flag_set: PASS")


def test_commit_is_refused_when_pending_flag_is_set_git_control_never_called() -> None:
    context = te.TaskContext()
    context.values[te._PENDING_TEST_VERIFICATION_KEY] = "true"
    m_git = MagicMock()
    with patch.object(te, "git_control", m_git):
        result = te._run_git("commit these changes with message 'x'", confirmed=True, context=context)
    m_git.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_commit_is_refused_when_pending_flag_is_set_git_control_never_called: PASS")


def test_commit_proceeds_normally_when_no_context_or_flag_clear() -> None:
    m_git = MagicMock(return_value="[VERIFIED_SUCCESS] commit abc1234 created.")
    with patch.object(te, "git_control", m_git):
        result_no_context = te._run_git("commit these changes with message 'x'", confirmed=True, context=None)
        context = te.TaskContext()
        context.values[te._PENDING_TEST_VERIFICATION_KEY] = "false"
        result_clear_flag = te._run_git("commit these changes with message 'x'", confirmed=True, context=context)
    assert result_no_context.startswith("[VERIFIED_SUCCESS]")
    assert result_clear_flag.startswith("[VERIFIED_SUCCESS]")
    assert m_git.call_count == 2
    print("test_commit_proceeds_normally_when_no_context_or_flag_clear: PASS")


# ── Group B: real, composed, disposable-repository workflows ────────────

def test_composed_technical_objective_full_workflow_succeeds_and_commit_is_verified() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_buggy_repo(tmp)
        p_ra, p_git = _pinned(tmp)
        with p_ra, p_git, patch("actions.repo_agent._code_helper", side_effect=_fix_calc_py):
            result = _task(objectives=[
                "search the repository for def add",
                "fix calc.py to correct the addition bug",
                "run the tests",
                "stage the changes",
                "commit these changes with message 'JARVIS J10 fix: correct add()'",
            ], confirmed=True)
        subject = _real_head_subject(tmp)
        count = _real_commit_count(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    assert subject == "JARVIS J10 fix: correct add()"
    assert count == "2"
    print("test_composed_technical_objective_full_workflow_succeeds_and_commit_is_verified: PASS")


def test_commit_refused_end_to_end_when_test_step_is_omitted_no_commit_created() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_buggy_repo(tmp)
        p_ra, p_git = _pinned(tmp)
        with p_ra, p_git, patch("actions.repo_agent._code_helper", side_effect=_fix_calc_py):
            result = _task(objectives=[
                "fix calc.py to correct the addition bug",
                "stage the changes",
                "commit these changes with message 'skip the tests'",
            ], confirmed=True)
        count = _real_commit_count(tmp)
    assert result.startswith("[INCONCLUSIVE]"), result
    assert count == "1"  # still just the seed commit -- nothing was committed
    print("test_commit_refused_end_to_end_when_test_step_is_omitted_no_commit_created: PASS")


def test_confirmation_required_still_gates_the_final_commit_even_after_tests_pass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_buggy_repo(tmp)
        p_ra, p_git = _pinned(tmp)
        with p_ra, p_git, patch("actions.repo_agent._code_helper", side_effect=_fix_calc_py):
            result = _task(objectives=[
                "fix calc.py to correct the addition bug",
                "run the tests",
                "stage the changes",
                "commit these changes with message 'no confirmation given'",
            ], confirmed=False)  # edit's own confirmation gate stops this before commit is ever reached
        count = _real_commit_count(tmp)
    assert result.startswith("[CONFIRMATION_REQUIRED]"), result
    assert count == "1"
    print("test_confirmation_required_still_gates_the_final_commit_even_after_tests_pass: PASS")


def test_blocked_step_stops_the_whole_task_before_reaching_commit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_buggy_repo(tmp)
        p_ra, p_git = _pinned(tmp)
        with p_ra, p_git, patch("actions.repo_agent._code_helper", side_effect=_fix_calc_py):
            result = _task(objectives=[
                "fix calc.py to correct the addition bug",
                "run the tests",
                "stage the changes",
                "git force push these changes",
                "commit these changes with message 'should never be reached'",
            ], confirmed=True)
        count = _real_commit_count(tmp)
        subject = _real_head_subject(tmp)
    assert result.startswith("[BLOCKED]"), result
    assert count == "1"
    assert subject != "should never be reached"
    print("test_blocked_step_stops_the_whole_task_before_reaching_commit: PASS")


def test_standalone_commit_task_with_no_prior_edit_is_unaffected_by_the_invariant() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _init_git_repo(tmp)
        (Path(tmp) / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=tmp, capture_output=True, text=True)
        (Path(tmp) / "notes.txt").write_text("unrelated docs change\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, text=True)

        p_ra, p_git = _pinned(tmp)
        with p_ra, p_git:
            result = _task(objective="commit these changes with message 'docs: add notes'", confirmed=True)
        count = _real_commit_count(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    assert count == "2"
    print("test_standalone_commit_task_with_no_prior_edit_is_unaffected_by_the_invariant: PASS")


def test_context_does_not_leak_the_pending_flag_across_separate_tasks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_buggy_repo(tmp)
        p_ra, p_git = _pinned(tmp)
        with p_ra, p_git, patch("actions.repo_agent._code_helper", side_effect=_fix_calc_py):
            first = _task(objective="fix calc.py to correct the addition bug", confirmed=True)
        assert first.startswith("[VERIFIED_SUCCESS]")

        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, text=True)
        with p_ra, p_git:
            # A completely separate execute_task() call/Task/TaskContext --
            # the first task's own "edit happened, unverified" flag must
            # not survive into this one.
            second = _task(objective="commit these changes with message 'separate task, separate context'", confirmed=True)
        count = _real_commit_count(tmp)
    assert second.startswith("[VERIFIED_SUCCESS]"), second
    assert count == "2"
    print("test_context_does_not_leak_the_pending_flag_across_separate_tasks: PASS")


# ── Group C: Task Engine wiring / boundary (mocked handlers) ────────────

def test_composed_recovery_cannot_cross_families_within_a_technical_objective() -> None:
    m_ra = MagicMock(return_value="[VERIFIED_FAILURE] search found nothing useful.")
    m_office = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"repo_agent": "office"}), \
         _handlers(repo_agent=m_ra, office=m_office):
        result = _task(objectives=["search the repository for a nonexistent symbol", "run the tests"])
    m_office.assert_not_called()
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_composed_recovery_cannot_cross_families_within_a_technical_objective: PASS")


def test_final_report_synthesizes_the_whole_composed_objective() -> None:
    m_ra_search = MagicMock(return_value="[VERIFIED_SUCCESS] Found 1 match(es): calc.py:2.")
    m_ra_edit = MagicMock(return_value="[VERIFIED_SUCCESS] File edited.")
    m_ra_tests = MagicMock(return_value="[VERIFIED_SUCCESS] All 1 test module(s) passed.")
    m_git_stage = MagicMock(return_value="[VERIFIED_SUCCESS] staged: calc.py.")
    m_git_commit = MagicMock(return_value="[VERIFIED_SUCCESS] commit abc1234 created.")

    def fake_repo_agent(objective, confirmed=False, context=None):
        low = objective.lower()
        if "search" in low:
            return m_ra_search()
        if "run" in low:
            return m_ra_tests()
        return m_ra_edit()

    def fake_git(objective, confirmed=False, context=None):
        return m_git_commit() if "commit" in objective.lower() else m_git_stage()

    with _handlers(repo_agent=fake_repo_agent, git=fake_git):
        result = _task(objectives=[
            "search the repository for add",
            "fix calc.py to correct the bug",
            "run the tests",
            "stage the changes",
            "commit these changes with message 'x'",
        ], confirmed=True)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "All 5 objectives verified" in result
    for expected in ("search the repository", "fix calc.py", "run the tests", "stage the changes", "commit these changes"):
        assert expected in result, f"missing step in final report: {expected}"
    print("test_final_report_synthesizes_the_whole_composed_objective: PASS")


def test_gemini_cannot_smuggle_a_direct_action_parameter_past_the_objective_text() -> None:
    m_git = MagicMock(return_value="[VERIFIED_SUCCESS] On branch main, clean.")
    with _handlers(git=m_git):
        _task(objective="show the git status of this repository", action="commit", confirmed=True)
    # execute_task() reads only objective/objectives/context/confirmed --
    # an extra 'action' parameter (Gemini attempting to name an internal
    # capability/action directly) has no effect at all; the objective
    # TEXT alone still determined 'status', not 'commit'.
    called_objective = m_git.call_args[0][0]
    assert "status" in called_objective.lower()
    print("test_gemini_cannot_smuggle_a_direct_action_parameter_past_the_objective_text: PASS")


def _run() -> None:
    test_edit_success_marks_pending_test_verification()
    test_run_tests_success_clears_pending_flag()
    test_run_tests_failure_leaves_pending_flag_set()
    test_commit_is_refused_when_pending_flag_is_set_git_control_never_called()
    test_commit_proceeds_normally_when_no_context_or_flag_clear()
    test_composed_technical_objective_full_workflow_succeeds_and_commit_is_verified()
    test_commit_refused_end_to_end_when_test_step_is_omitted_no_commit_created()
    test_confirmation_required_still_gates_the_final_commit_even_after_tests_pass()
    test_blocked_step_stops_the_whole_task_before_reaching_commit()
    test_standalone_commit_task_with_no_prior_edit_is_unaffected_by_the_invariant()
    test_context_does_not_leak_the_pending_flag_across_separate_tasks()
    test_composed_recovery_cannot_cross_families_within_a_technical_objective()
    test_final_report_synthesizes_the_whole_composed_objective()
    test_gemini_cannot_smuggle_a_direct_action_parameter_past_the_objective_text()
    print("\nAll task_engine_j10 tests passed.")


if __name__ == "__main__":
    _run()
