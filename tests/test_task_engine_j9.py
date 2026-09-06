"""
tests/test_task_engine_j9.py — J9 (Git) Task Engine integration: routing
to the new `git` domain (a second FAMILY_DEVELOPMENT member, alongside
J8's `repo_agent`), the objective parser, and J4/J5/J6/J8 compatibility.
actions/git_control.py's own functions (status/diff/log/branch/stage/
commit) are covered directly and exhaustively in tests/test_git_control.py
— NOT duplicated here; this file proves only the Task-Engine-level
wiring around them (route()/build_plan()/_execute_step()/execute_task()),
using mocked git_control() calls, same convention as every other
domain's own task_engine test file (see test_task_engine_j8.py).

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_j9
"""
from unittest.mock import MagicMock, patch

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    return patch.dict(te._HANDLERS, overrides)


# ── Routing ───────────────────────────────────────────────────────────────

def test_route_resolves_git_objectives_to_the_git_domain() -> None:
    assert te.route("show the git status of this repository") == "git"
    assert te.route("show me the git diff") == "git"
    assert te.route("show the recent git commits") == "git"
    assert te.route("stage the changes") == "git"
    assert te.route("commit these changes with message 'Fix login bug'") == "git"
    print("test_route_resolves_git_objectives_to_the_git_domain: PASS")


def test_route_git_vs_repo_agent_collision_resolves_to_git() -> None:
    # "commit the repo changes" ties 1-1: "commit" for git vs. "repo" for
    # repo_agent -- git must win, since only git has a commit action.
    assert te.route("commit the repo changes") == "git"
    # Existing repo_agent routing must remain completely unaffected.
    assert te.route("search the repository for jarvis_task") == "repo_agent"
    assert te.route("run the tests") == "repo_agent"
    print("test_route_git_vs_repo_agent_collision_resolves_to_git: PASS")


def test_route_bare_status_without_git_still_goes_to_system_shortcut() -> None:
    # "status" alone is already system_shortcut's own keyword (e.g.
    # "check battery status") -- git deliberately does NOT claim the
    # bare word "status" (see task_engine.py's own git domain comment),
    # so this must be completely unaffected by J9.
    assert te.route("check battery status") == "system_shortcut"
    assert te.route("what is the status") == "system_shortcut"
    print("test_route_bare_status_without_git_still_goes_to_system_shortcut: PASS")


def test_route_existing_domains_are_unaffected_by_the_git_domain() -> None:
    assert te.route("play a song on youtube") == "youtube"
    assert te.route("open a webpage") == "browser"
    assert te.route("set the volume to 40") == "system_volume"
    assert te.route("list files on my desktop") == "file_system"
    assert te.route("insert this text into the document") == "office"
    print("test_route_existing_domains_are_unaffected_by_the_git_domain: PASS")


# ── Parser ────────────────────────────────────────────────────────────────

def test_parser_status_is_the_default_for_a_bare_git_mention() -> None:
    assert te._parse_git_action("check git") == {"action": "status"}
    assert te._parse_git_action("show the git status of this repository") == {"action": "status"}
    print("test_parser_status_is_the_default_for_a_bare_git_mention: PASS")


def test_parser_extracts_diff_log_branch_stage() -> None:
    assert te._parse_git_action("show me the git diff") == {"action": "diff"}
    assert te._parse_git_action("show the recent git commits") == {"action": "log"}
    assert te._parse_git_action("show the git log") == {"action": "log"}
    assert te._parse_git_action("list the branches") == {"action": "branch"}
    assert te._parse_git_action("stage the changes") == {"action": "stage"}
    print("test_parser_extracts_diff_log_branch_stage: PASS")


def test_parser_extracts_a_quoted_commit_message() -> None:
    assert te._parse_git_action("commit these changes with message 'Fix login bug'") == {
        "action": "commit", "message": "Fix login bug",
    }
    assert te._parse_git_action('commit with message "Add tests"') == {
        "action": "commit", "message": "Add tests",
    }
    print("test_parser_extracts_a_quoted_commit_message: PASS")


def test_parser_refuses_a_commit_with_no_extractable_message() -> None:
    assert te._parse_git_action("commit these changes") is None
    print("test_parser_refuses_a_commit_with_no_extractable_message: PASS")


def test_parser_checkout_maps_to_the_honestly_unsupported_action() -> None:
    assert te._parse_git_action("checkout the main branch") == {"action": "checkout"}
    assert te._parse_git_action("switch to the dev branch") == {"action": "checkout"}
    print("test_parser_checkout_maps_to_the_honestly_unsupported_action: PASS")


def test_parser_never_silently_defaults_dangerous_git_requests_to_status() -> None:
    # Real gap found and fixed during J9's own live E2E: these all
    # contain "git" (so they DO reach this parser) but no other
    # sub-action keyword matched -- before the fix, every one of these
    # silently fell through to {"action": "status"}, meaning a request
    # to push/reset/clean was answered with an unrelated status report
    # instead of an honest refusal.
    assert te._parse_git_action("git push these changes") == {"action": "push"}
    assert te._parse_git_action("force push to git") in ({"action": "push"}, {"action": "force_push"})
    assert te._parse_git_action("reset the git repository hard") == {"action": "reset_hard"}
    assert te._parse_git_action("clean the git repository") == {"action": "clean"}
    assert te._parse_git_action("rebase this git branch") == {"action": "rebase"}
    assert te._parse_git_action("merge this git branch") == {"action": "merge"}
    print("test_parser_never_silently_defaults_dangerous_git_requests_to_status: PASS")


def test_execute_task_git_push_request_is_blocked_end_to_end_never_status() -> None:
    result = te.execute_task(parameters={"objective": "git push these changes"})
    assert result.startswith("[BLOCKED]")
    print("test_execute_task_git_push_request_is_blocked_end_to_end_never_status: PASS")


def test_execute_task_git_reset_hard_request_is_blocked_end_to_end() -> None:
    result = te.execute_task(parameters={"objective": "reset the git repository hard"})
    assert result.startswith("[BLOCKED]")
    print("test_execute_task_git_reset_hard_request_is_blocked_end_to_end: PASS")


# ── Execution / verification through the Task Engine ────────────────────

def test_execute_task_status_reaches_git_and_reports_real_success() -> None:
    m_git = MagicMock(return_value="[VERIFIED_SUCCESS] On branch main, clean.")
    with _handlers(git=m_git):
        result = _task(objective="show the git status of this repository")
    m_git.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_execute_task_status_reaches_git_and_reports_real_success: PASS")


def test_execute_task_commit_reports_real_failure_honestly() -> None:
    m_git = MagicMock(return_value="[VERIFIED_FAILURE] git commit failed: nothing to commit.")
    with _handlers(git=m_git):
        result = _task(objective="commit these changes with message 'oops'")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_execute_task_commit_reports_real_failure_honestly: PASS")


def test_execute_task_ambiguous_git_objective_is_inconclusive() -> None:
    result = _task(objective="commit these changes")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_execute_task_ambiguous_git_objective_is_inconclusive: PASS")


# ── Confirmation / safety ─────────────────────────────────────────────────

def test_git_commit_confirmation_is_threaded_through_end_to_end() -> None:
    import subprocess
    import tempfile
    from pathlib import Path
    import actions.git_control as gc

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init"], cwd=tmp, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "jarvis-test@example.com"], cwd=tmp, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "JARVIS Test"], cwd=tmp, capture_output=True, text=True)
        (Path(tmp) / "f.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True, text=True)

        def _real_git_control_pinned_to_tmp(parameters):
            return gc.git_control(parameters={**parameters, "repo_root": tmp})

        with patch.object(te, "git_control", side_effect=_real_git_control_pinned_to_tmp):
            result = _task(objective="commit these changes with message 'Should need confirmation'")
        assert result.startswith("[CONFIRMATION_REQUIRED]")

        log_result = gc.log(tmp)
        assert "no commits yet" in log_result.lower()
    print("test_git_commit_confirmation_is_threaded_through_end_to_end: PASS")


def test_confirmation_required_git_result_is_never_bypassed_by_recovery() -> None:
    m_git = MagicMock(return_value='[CONFIRMATION_REQUIRED] this will create a real commit.')
    m_other = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"git": "repo_agent"}), \
         _handlers(git=m_git, repo_agent=m_other):
        result = _task(objective="commit these changes with message 'x'")
    m_other.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_confirmation_required_git_result_is_never_bypassed_by_recovery: PASS")


def test_blocked_git_result_is_never_bypassed_by_recovery() -> None:
    m_git = MagicMock(return_value="[BLOCKED] 'push' is permanently disallowed.")
    m_other = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"git": "repo_agent"}), \
         _handlers(git=m_git, repo_agent=m_other):
        result = _task(objective="commit these changes with message 'x'")
    m_other.assert_not_called()
    assert result.startswith("[BLOCKED]")
    print("test_blocked_git_result_is_never_bypassed_by_recovery: PASS")


# ── J5: no recovery chain, family scoping ────────────────────────────────

def test_git_has_no_recovery_chain_entry() -> None:
    assert "git" not in te._RECOVERY_CHAIN
    print("test_git_has_no_recovery_chain_entry: PASS")


def test_git_belongs_to_the_development_family_alongside_repo_agent() -> None:
    assert te.family_of("git") == te.FAMILY_DEVELOPMENT
    assert te.family_of("repo_agent") == te.FAMILY_DEVELOPMENT
    print("test_git_belongs_to_the_development_family_alongside_repo_agent: PASS")


# ── J6: no unnecessary inspection ────────────────────────────────────────

def test_git_has_no_inspect_config_entry() -> None:
    assert "git" not in te._INSPECT_CONFIG
    print("test_git_has_no_inspect_config_entry: PASS")


# ── J4: single-objective passthrough ─────────────────────────────────────

def test_single_objective_git_result_is_still_byte_for_byte_the_raw_result() -> None:
    m_git = MagicMock(return_value="[VERIFIED_SUCCESS] commit abc1234 created.")
    with _handlers(git=m_git):
        result = _task(objective="commit these changes with message 'x'")
    assert result == "[VERIFIED_SUCCESS] commit abc1234 created."
    print("test_single_objective_git_result_is_still_byte_for_byte_the_raw_result: PASS")


# ── J8 compatibility: repo_agent and git coexist cleanly ─────────────────

def test_repo_agent_and_git_route_independently_in_a_multi_objective_task() -> None:
    m_ra = MagicMock(return_value="[VERIFIED_SUCCESS] All tests passed.")
    m_git = MagicMock(return_value="[VERIFIED_SUCCESS] commit abc1234 created.")
    with _handlers(repo_agent=m_ra, git=m_git):
        result = _task(objectives=["run the tests", "commit these changes with message 'passing tests'"])
    m_ra.assert_called_once()
    m_git.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_repo_agent_and_git_route_independently_in_a_multi_objective_task: PASS")


def _run() -> None:
    test_route_resolves_git_objectives_to_the_git_domain()
    test_route_git_vs_repo_agent_collision_resolves_to_git()
    test_route_bare_status_without_git_still_goes_to_system_shortcut()
    test_route_existing_domains_are_unaffected_by_the_git_domain()
    test_parser_status_is_the_default_for_a_bare_git_mention()
    test_parser_extracts_diff_log_branch_stage()
    test_parser_extracts_a_quoted_commit_message()
    test_parser_refuses_a_commit_with_no_extractable_message()
    test_parser_checkout_maps_to_the_honestly_unsupported_action()
    test_parser_never_silently_defaults_dangerous_git_requests_to_status()
    test_execute_task_git_push_request_is_blocked_end_to_end_never_status()
    test_execute_task_git_reset_hard_request_is_blocked_end_to_end()
    test_execute_task_status_reaches_git_and_reports_real_success()
    test_execute_task_commit_reports_real_failure_honestly()
    test_execute_task_ambiguous_git_objective_is_inconclusive()
    test_git_commit_confirmation_is_threaded_through_end_to_end()
    test_confirmation_required_git_result_is_never_bypassed_by_recovery()
    test_blocked_git_result_is_never_bypassed_by_recovery()
    test_git_has_no_recovery_chain_entry()
    test_git_belongs_to_the_development_family_alongside_repo_agent()
    test_git_has_no_inspect_config_entry()
    test_single_objective_git_result_is_still_byte_for_byte_the_raw_result()
    test_repo_agent_and_git_route_independently_in_a_multi_objective_task()
    print("\nAll task_engine_j9 tests passed.")


if __name__ == "__main__":
    _run()
