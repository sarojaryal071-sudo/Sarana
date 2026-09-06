"""
tests/test_task_engine_j11.py — J11 (Deployment & Production Operations)
Task Engine integration: routing to the new `deployment` domain (the
first real FAMILY_DEPLOYMENT member), the objective parser, J10's own
test-before-commit invariant reused for `deploy`, the new git-commit ->
deploy context-passing rule, and J4/J5/J6 compatibility.
actions/deployment_control.py's own functions are covered directly and
exhaustively in tests/test_deployment_control.py — NOT duplicated here;
this file proves only the Task-Engine-level wiring around them, using
mocked deployment_control() calls, same convention as every other
domain's own task_engine test file (see test_task_engine_j9.py/_j10.py).

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_j11
"""
from unittest.mock import MagicMock, patch

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    return patch.dict(te._HANDLERS, overrides)


# ── Routing ───────────────────────────────────────────────────────────────

def test_route_resolves_production_objectives_to_the_deployment_domain() -> None:
    assert te.route("check whether the backend is running") == "deployment"
    assert te.route("what is the deployment status") == "deployment"
    assert te.route("show the deployment history") == "deployment"
    assert te.route("deploy the latest verified version of the backend") == "deployment"
    assert te.route("roll back to deploy dep-abc123") == "deployment"
    print("test_route_resolves_production_objectives_to_the_deployment_domain: PASS")


def test_route_deploy_vs_git_commit_collision_resolves_to_deployment() -> None:
    # Real collision found live: "deploy the latest commit" ties 1-1
    # ("deploy" for deployment vs. "commit" for git) -- deployment must
    # win, since git has no deploy action at all.
    assert te.route("deploy the latest commit") == "deployment"
    # Existing git routing must remain completely unaffected.
    assert te.route("commit the repo changes") == "git"
    assert te.route("git status") == "git"
    print("test_route_deploy_vs_git_commit_collision_resolves_to_deployment: PASS")


def test_route_restart_production_vs_restart_local_machine() -> None:
    assert te.route("restart the production service") == "deployment"
    assert te.route("restart my computer") == "system_power"
    print("test_route_restart_production_vs_restart_local_machine: PASS")


def test_route_existing_domains_are_unaffected_by_the_deployment_domain() -> None:
    assert te.route("check battery status") == "system_shortcut"
    assert te.route("play a song on youtube") == "youtube"
    assert te.route("open a webpage") == "browser"
    assert te.route("search the repository for jarvis_task") == "repo_agent"
    print("test_route_existing_domains_are_unaffected_by_the_deployment_domain: PASS")


# ── Parser ────────────────────────────────────────────────────────────────

def test_parser_health_check_is_the_default() -> None:
    assert te._parse_deployment_action("check whether the backend is running") == {"action": "health_check"}
    assert te._parse_deployment_action("is production up") == {"action": "health_check"}
    print("test_parser_health_check_is_the_default: PASS")


def test_parser_extracts_status_history_rollback() -> None:
    assert te._parse_deployment_action("what is the deployment status") == {"action": "status"}
    assert te._parse_deployment_action("show the deployment history") == {"action": "history"}
    assert te._parse_deployment_action("roll back to deploy dep-abc123") == {"action": "rollback", "deploy_id": "dep-abc123"}
    print("test_parser_extracts_status_history_rollback: PASS")


def test_parser_extracts_an_explicit_commit_hash_for_deploy() -> None:
    assert te._parse_deployment_action("deploy commit abc1234") == {"action": "deploy", "commit_id": "abc1234"}
    print("test_parser_extracts_an_explicit_commit_hash_for_deploy: PASS")


def test_parser_deploy_with_no_reference_leaves_commit_id_empty() -> None:
    assert te._parse_deployment_action("deploy the backend") == {"action": "deploy", "commit_id": ""}
    print("test_parser_deploy_with_no_reference_leaves_commit_id_empty: PASS")


def test_parser_rollback_without_an_id_is_still_a_dict_deployment_control_will_refuse() -> None:
    result = te._parse_deployment_action("roll back production")
    assert result == {"action": "rollback", "deploy_id": ""}
    print("test_parser_rollback_without_an_id_is_still_a_dict_deployment_control_will_refuse: PASS")


# ── Execution / verification through the Task Engine ────────────────────

def test_execute_task_health_check_reaches_deployment_and_reports_real_success() -> None:
    m_dep = MagicMock(return_value="[VERIFIED_SUCCESS] backend/frontend up.")
    with _handlers(deployment=m_dep):
        result = _task(objective="check whether the backend is running")
    m_dep.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_execute_task_health_check_reaches_deployment_and_reports_real_success: PASS")


def test_execute_task_deploy_reports_real_failure_honestly() -> None:
    m_dep = MagicMock(return_value="[VERIFIED_FAILURE] deploy failed.")
    with _handlers(deployment=m_dep):
        result = _task(objective="deploy the backend", confirmed=True)
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_execute_task_deploy_reports_real_failure_honestly: PASS")


# ── J10 composition: test-before-commit invariant reused for deploy ─────

def test_deploy_is_refused_when_an_edit_in_this_task_was_never_tested() -> None:
    # Mocks deployment_control() itself (not _HANDLERS["deployment"]) so
    # the REAL _run_deployment() executes, including the invariant check
    # under test -- replacing the whole handler would bypass it entirely.
    m_ra = MagicMock(return_value="[VERIFIED_SUCCESS] File edited.")
    with patch.object(te, "repo_agent", m_ra), patch.object(te, "deployment_control") as m_dc:
        result = _task(objectives=["fix calc.py to correct the bug", "deploy the backend"], confirmed=True)
    m_dc.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_deploy_is_refused_when_an_edit_in_this_task_was_never_tested: PASS")


def test_deploy_proceeds_once_tests_verify_the_edit() -> None:
    m_ra_edit = MagicMock(return_value="[VERIFIED_SUCCESS] File edited.")
    m_ra_tests = MagicMock(return_value="[VERIFIED_SUCCESS] All tests passed.")

    def fake_repo_agent(parameters):
        return m_ra_tests(parameters) if parameters.get("action") == "run_tests" else m_ra_edit(parameters)

    with patch.object(te, "repo_agent", side_effect=fake_repo_agent), \
         patch.object(te, "deployment_control", return_value="[VERIFIED_SUCCESS] deployed.") as m_dc:
        result = _task(objectives=["fix calc.py to correct the bug", "run the tests", "deploy the backend"], confirmed=True)
    m_dc.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_deploy_proceeds_once_tests_verify_the_edit: PASS")


def test_standalone_deploy_task_with_no_prior_edit_is_unaffected() -> None:
    with patch.object(te, "deployment_control", return_value="[VERIFIED_SUCCESS] deployed.") as m_dc:
        result = _task(objective="deploy the backend", confirmed=True)
    m_dc.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_standalone_deploy_task_with_no_prior_edit_is_unaffected: PASS")


# ── Git commit -> deploy context passing ─────────────────────────────────

def test_a_verified_commit_hash_flows_from_git_into_a_later_deploy_step() -> None:
    m_git = MagicMock(return_value='[VERIFIED_SUCCESS] commit abc1234 created in repo: "fix".')
    m_dep = MagicMock(return_value="[VERIFIED_SUCCESS] deployed.")
    with _handlers(git=m_git, deployment=m_dep):
        result = _task(objectives=["commit these changes with message 'fix'", "deploy the latest commit"], confirmed=True)
    assert result.startswith("[VERIFIED_SUCCESS]")
    m_dep.assert_called_once()
    called_objective = m_dep.call_args[0][0]
    assert "deploy" in called_objective.lower()
    print("test_a_verified_commit_hash_flows_from_git_into_a_later_deploy_step: PASS")


def test_context_commit_extraction_actually_reaches_deployment_control_parameters() -> None:
    m_git = MagicMock(return_value='[VERIFIED_SUCCESS] commit abc1234 created in repo: "fix".')
    with _handlers(git=m_git), patch.object(te, "deployment_control") as m_dc:
        m_dc.return_value = "[VERIFIED_SUCCESS] ok."
        _task(objectives=["commit these changes with message 'fix'", "deploy the latest commit"], confirmed=True)
    assert m_dc.call_args.kwargs["parameters"]["commit_id"] == "abc1234"
    print("test_context_commit_extraction_actually_reaches_deployment_control_parameters: PASS")


# ── Confirmation / safety ─────────────────────────────────────────────────

def test_confirmation_required_deployment_result_is_never_bypassed_by_recovery() -> None:
    m_dep = MagicMock(return_value="[CONFIRMATION_REQUIRED] this will deploy to production.")
    m_other = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"deployment": "git"}), \
         _handlers(deployment=m_dep, git=m_other):
        result = _task(objective="deploy the backend", confirmed=False)
    m_other.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_confirmation_required_deployment_result_is_never_bypassed_by_recovery: PASS")


def test_blocked_deployment_result_is_never_bypassed_by_recovery() -> None:
    m_dep = MagicMock(return_value="[BLOCKED] Render is not configured.")
    m_other = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"deployment": "git"}), \
         _handlers(deployment=m_dep, git=m_other):
        result = _task(objective="deploy the backend", confirmed=True)
    m_other.assert_not_called()
    assert result.startswith("[BLOCKED]")
    print("test_blocked_deployment_result_is_never_bypassed_by_recovery: PASS")


def test_gemini_cannot_smuggle_a_direct_action_parameter_past_the_objective_text() -> None:
    m_dep = MagicMock(return_value="[VERIFIED_SUCCESS] health ok.")
    with _handlers(deployment=m_dep):
        _task(objective="check whether the backend is running", action="deploy", confirmed=True)
    called_objective = m_dep.call_args[0][0]
    assert "running" in called_objective.lower()
    print("test_gemini_cannot_smuggle_a_direct_action_parameter_past_the_objective_text: PASS")


# ── J5: no recovery chain, family scoping ────────────────────────────────

def test_deployment_has_no_recovery_chain_entry() -> None:
    assert "deployment" not in te._RECOVERY_CHAIN
    print("test_deployment_has_no_recovery_chain_entry: PASS")


def test_deployment_belongs_to_its_own_family() -> None:
    assert te.family_of("deployment") == te.FAMILY_DEPLOYMENT
    assert te.FAMILY_DEPLOYMENT not in (te.FAMILY_SYSTEM, te.FAMILY_APPLICATION, te.FAMILY_RESOURCE, te.FAMILY_DEVELOPMENT)
    print("test_deployment_belongs_to_its_own_family: PASS")


# ── J6: no unnecessary inspection ────────────────────────────────────────

def test_deployment_has_no_inspect_config_entry() -> None:
    assert "deployment" not in te._INSPECT_CONFIG
    print("test_deployment_has_no_inspect_config_entry: PASS")


# ── J4: single-objective passthrough, whole-objective report ────────────

def test_single_objective_deployment_result_is_still_byte_for_byte_the_raw_result() -> None:
    m_dep = MagicMock(return_value="[VERIFIED_SUCCESS] backend/frontend up.")
    with _handlers(deployment=m_dep):
        result = _task(objective="check whether the backend is running")
    assert result == "[VERIFIED_SUCCESS] backend/frontend up."
    print("test_single_objective_deployment_result_is_still_byte_for_byte_the_raw_result: PASS")


def test_final_report_synthesizes_a_composed_technical_and_deployment_objective() -> None:
    m_ra_tests = MagicMock(return_value="[VERIFIED_SUCCESS] All tests passed.")
    m_git = MagicMock(return_value='[VERIFIED_SUCCESS] commit abc1234 created in repo: "fix".')
    m_dep = MagicMock(return_value="[VERIFIED_SUCCESS] deploy dep-1 status live; production health: VERIFIED_SUCCESS.")
    with _handlers(repo_agent=m_ra_tests, git=m_git, deployment=m_dep):
        result = _task(objectives=[
            "run the tests",
            "commit these changes with message 'x'",
            "deploy the latest commit",
        ], confirmed=True)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "All 3 objectives verified" in result
    for expected in ("run the tests", "commit these changes", "deploy the latest commit"):
        assert expected in result, f"missing step in final report: {expected}"
    print("test_final_report_synthesizes_a_composed_technical_and_deployment_objective: PASS")


def _run() -> None:
    test_route_resolves_production_objectives_to_the_deployment_domain()
    test_route_deploy_vs_git_commit_collision_resolves_to_deployment()
    test_route_restart_production_vs_restart_local_machine()
    test_route_existing_domains_are_unaffected_by_the_deployment_domain()
    test_parser_health_check_is_the_default()
    test_parser_extracts_status_history_rollback()
    test_parser_extracts_an_explicit_commit_hash_for_deploy()
    test_parser_deploy_with_no_reference_leaves_commit_id_empty()
    test_parser_rollback_without_an_id_is_still_a_dict_deployment_control_will_refuse()
    test_execute_task_health_check_reaches_deployment_and_reports_real_success()
    test_execute_task_deploy_reports_real_failure_honestly()
    test_deploy_is_refused_when_an_edit_in_this_task_was_never_tested()
    test_deploy_proceeds_once_tests_verify_the_edit()
    test_standalone_deploy_task_with_no_prior_edit_is_unaffected()
    test_a_verified_commit_hash_flows_from_git_into_a_later_deploy_step()
    test_context_commit_extraction_actually_reaches_deployment_control_parameters()
    test_confirmation_required_deployment_result_is_never_bypassed_by_recovery()
    test_blocked_deployment_result_is_never_bypassed_by_recovery()
    test_gemini_cannot_smuggle_a_direct_action_parameter_past_the_objective_text()
    test_deployment_has_no_recovery_chain_entry()
    test_deployment_belongs_to_its_own_family()
    test_deployment_has_no_inspect_config_entry()
    test_single_objective_deployment_result_is_still_byte_for_byte_the_raw_result()
    test_final_report_synthesizes_a_composed_technical_and_deployment_objective()
    print("\nAll task_engine_j11 tests passed.")


if __name__ == "__main__":
    _run()
