"""
tests/test_task_engine_j8.py — J8 (Software Development Agent) Task
Engine integration: routing to the new `repo_agent` domain (the first
real FAMILY_DEVELOPMENT member), the objective parser, and J4/J5/J6
compatibility. actions/repo_agent.py's own functions (search/run_tests/
edit) are covered directly and exhaustively in tests/test_repo_agent.py
— NOT duplicated here; this file proves only the Task-Engine-level
wiring around them (route()/build_plan()/_execute_step()/execute_task()),
using mocked repo_agent() calls, same convention as every other domain's
own task_engine test file.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_j8
"""
from unittest.mock import MagicMock, patch

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    return patch.dict(te._HANDLERS, overrides)


# ── Routing ───────────────────────────────────────────────────────────────

def test_route_resolves_development_objectives_to_repo_agent() -> None:
    assert te.route("search the repository for jarvis_task") == "repo_agent"
    assert te.route("run the tests") == "repo_agent"
    assert te.route("find every reference to _RECOVERY_CHAIN in the codebase") == "repo_agent"
    print("test_route_resolves_development_objectives_to_repo_agent: PASS")


def test_route_search_vs_browser_collision_resolves_to_repo_agent() -> None:
    # The real collision discovered live: "search" is a genuine browser
    # keyword too -- "search the repository for X" used to tie 1-1 and
    # lose to browser on declaration order before repo_agent was moved
    # ahead of it in _DOMAINS.
    assert te.route("search the repository for jarvis_task") == "repo_agent"
    # Existing browser routing must remain completely unaffected.
    assert te.route("search for restaurants nearby on google") == "browser"
    assert te.route("open a webpage") == "browser"
    print("test_route_search_vs_browser_collision_resolves_to_repo_agent: PASS")


def test_route_code_extension_hint_reaches_repo_agent_without_a_bare_noun() -> None:
    # "fix the bug in helper.py" contains no bare "repository"/"repo"/
    # "codebase" word at all -- the fix this proves.
    assert te.route("fix the bug in helper.py") == "repo_agent"
    assert te.route("edit main.py to add logging") == "repo_agent"
    print("test_route_code_extension_hint_reaches_repo_agent_without_a_bare_noun: PASS")


def test_route_code_extension_hint_does_not_regress_file_system_routing() -> None:
    # file_system's own document/media extensions (txt/pdf/etc, from J7)
    # are unaffected by splitting code extensions into their own hint.
    assert te.route("delete notes.txt from my desktop") == "file_system"
    assert te.route("rename report.pdf to summary.pdf") == "file_system"
    print("test_route_code_extension_hint_does_not_regress_file_system_routing: PASS")


def test_route_disclosed_trade_off_code_extension_file_delete_is_ambiguous() -> None:
    # Documented, accepted limitation (see task_engine.py's own
    # _CODE_EXTENSION_HINT_RE comment): a plain "delete script.py"-style
    # request now routes to repo_agent, not file_system -- safe (its own
    # parser honestly returns INCONCLUSIVE, never a wrong deletion),
    # just a known, disclosed trade-off rather than a silent regression.
    assert te.route("delete script.py from my desktop") == "repo_agent"
    assert te._parse_repo_action("delete script.py from my desktop") is None
    print("test_route_disclosed_trade_off_code_extension_file_delete_is_ambiguous: PASS")


# ── Parser ────────────────────────────────────────────────────────────────

def test_parser_extracts_search_query_from_several_phrasings() -> None:
    assert te._parse_repo_action("search the repository for jarvis_task") == {
        "action": "search", "query": "jarvis_task",
    }
    assert te._parse_repo_action("find every reference to _RECOVERY_CHAIN in the codebase") == {
        "action": "search", "query": "_RECOVERY_CHAIN",
    }
    print("test_parser_extracts_search_query_from_several_phrasings: PASS")


def test_parser_run_tests_needs_no_further_target() -> None:
    assert te._parse_repo_action("run the tests") == {"action": "run_tests"}
    assert te._parse_repo_action("run the test suite") == {"action": "run_tests"}
    print("test_parser_run_tests_needs_no_further_target: PASS")


def test_parser_edit_requires_an_extractable_file() -> None:
    assert te._parse_repo_action("fix the bug in helper.py") == {
        "action": "edit", "file_path": "helper.py", "instruction": "fix the bug in helper.py",
    }
    assert te._parse_repo_action("please fix the project") is None
    print("test_parser_edit_requires_an_extractable_file: PASS")


def test_parser_refuses_a_vague_objective() -> None:
    assert te._parse_repo_action("do something with the repository") is None
    print("test_parser_refuses_a_vague_objective: PASS")


# ── Execution / verification through the Task Engine ────────────────────

def test_execute_task_search_reaches_repo_agent_and_reports_real_success() -> None:
    m_ra = MagicMock(return_value="[VERIFIED_SUCCESS] Found 1 match(es).")
    with _handlers(repo_agent=m_ra):
        result = _task(objective="search the repository for jarvis_task")
    m_ra.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_execute_task_search_reaches_repo_agent_and_reports_real_success: PASS")


def test_execute_task_run_tests_reports_real_failure_honestly() -> None:
    m_ra = MagicMock(return_value="[VERIFIED_FAILURE] Tests failed (exit 1).")
    with _handlers(repo_agent=m_ra):
        result = _task(objective="run the tests")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_execute_task_run_tests_reports_real_failure_honestly: PASS")


def test_execute_task_ambiguous_development_objective_is_inconclusive() -> None:
    result = _task(objective="please just handle the repository somehow")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_execute_task_ambiguous_development_objective_is_inconclusive: PASS")


# ── Development task success is objective-level, not step-level (§10) ──

def test_fix_the_failing_test_workflow_requires_every_step_to_verify() -> None:
    # Section 10's own worked example: a successful EDIT is not enough --
    # the meaningful lifecycle is search -> edit -> run tests -> verify.
    # Multi-objective sequencing (already proven in Phase 5A/J4) carries
    # this for free once each step is independently parseable.
    m_ra_search = MagicMock(return_value="[VERIFIED_SUCCESS] Found 1 match(es): tests/test_battery.py:5.")
    m_ra_edit = MagicMock(return_value="[VERIFIED_SUCCESS] File edited.")
    m_ra_tests = MagicMock(return_value="[VERIFIED_SUCCESS] All 1 test module(s) passed.")

    def fake_repo_agent(parameters):
        action = parameters.get("action")
        return {"search": m_ra_search, "edit": m_ra_edit, "run_tests": m_ra_tests}[action](parameters)

    with patch.object(te, "repo_agent", side_effect=fake_repo_agent):
        result = _task(objectives=[
            "search the repository for battery_percent",
            "fix helper.py to correct the battery calculation",
            "run the tests",
        ])
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "battery_percent" in result or "battery" in result.lower()
    print("test_fix_the_failing_test_workflow_requires_every_step_to_verify: PASS")


def test_fix_the_failing_test_workflow_stops_honestly_if_tests_still_fail() -> None:
    # An edit "succeeding" is not the objective succeeding -- if the
    # tests still fail afterward, the WHOLE task must report failure,
    # never just the edit step's own local success.
    m_ra_edit = MagicMock(return_value="[VERIFIED_SUCCESS] File edited.")
    m_ra_tests = MagicMock(return_value="[VERIFIED_FAILURE] Tests still failing.")

    def fake_repo_agent(parameters):
        action = parameters.get("action")
        return {"edit": m_ra_edit, "run_tests": m_ra_tests}[action](parameters)

    with patch.object(te, "repo_agent", side_effect=fake_repo_agent):
        result = _task(objectives=["fix helper.py to correct the bug", "run the tests"])
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_fix_the_failing_test_workflow_stops_honestly_if_tests_still_fail: PASS")


# ── Confirmation / safety ─────────────────────────────────────────────────

def test_repo_edit_confirmation_is_threaded_through_end_to_end() -> None:
    import tempfile
    from pathlib import Path
    import actions.repo_agent as ra

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "helper.py"
        target.write_text("x = 1\n")

        def _real_repo_agent_pinned_to_tmp(parameters):
            return ra.repo_agent(parameters={**parameters, "repo_root": tmp})

        with patch.object(te, "repo_agent", side_effect=_real_repo_agent_pinned_to_tmp):
            result = _task(objective="fix helper.py to change x")
        assert result.startswith("[CONFIRMATION_REQUIRED]")
        assert target.read_text() == "x = 1\n"
    print("test_repo_edit_confirmation_is_threaded_through_end_to_end: PASS")


def test_confirmation_required_repo_result_is_never_bypassed_by_recovery() -> None:
    m_ra = MagicMock(return_value="[CONFIRMATION_REQUIRED] this will modify helper.py.")
    m_other = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"repo_agent": "file_system"}), \
         _handlers(repo_agent=m_ra, file_system=m_other):
        result = _task(objective="fix helper.py to change x")
    m_other.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_confirmation_required_repo_result_is_never_bypassed_by_recovery: PASS")


def test_blocked_repo_result_is_never_bypassed_by_recovery() -> None:
    m_ra = MagicMock(return_value="[BLOCKED] Access denied: outside repo.")
    m_other = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"repo_agent": "file_system"}), \
         _handlers(repo_agent=m_ra, file_system=m_other):
        result = _task(objective="fix helper.py to change x")
    m_other.assert_not_called()
    assert result.startswith("[BLOCKED]")
    print("test_blocked_repo_result_is_never_bypassed_by_recovery: PASS")


# ── J5: no recovery chain, family scoping ────────────────────────────────

def test_repo_agent_has_no_recovery_chain_entry() -> None:
    assert "repo_agent" not in te._RECOVERY_CHAIN
    print("test_repo_agent_has_no_recovery_chain_entry: PASS")


def test_repo_agent_belongs_to_the_development_family() -> None:
    assert te.family_of("repo_agent") == te.FAMILY_DEVELOPMENT
    assert te.FAMILY_DEVELOPMENT not in (te.FAMILY_SYSTEM, te.FAMILY_APPLICATION, te.FAMILY_RESOURCE)
    print("test_repo_agent_belongs_to_the_development_family: PASS")


# ── J6: no unnecessary inspection ────────────────────────────────────────

def test_repo_agent_has_no_inspect_config_entry() -> None:
    assert "repo_agent" not in te._INSPECT_CONFIG
    print("test_repo_agent_has_no_inspect_config_entry: PASS")


# ── J4: single-objective passthrough, multi-objective reporting ────────

def test_single_objective_repo_result_is_still_byte_for_byte_the_raw_result() -> None:
    m_ra = MagicMock(return_value="[VERIFIED_SUCCESS] Found 3 match(es).")
    with _handlers(repo_agent=m_ra):
        result = _task(objective="search the repository for jarvis_task")
    assert result == "[VERIFIED_SUCCESS] Found 3 match(es)."
    print("test_single_objective_repo_result_is_still_byte_for_byte_the_raw_result: PASS")


def _run() -> None:
    test_route_resolves_development_objectives_to_repo_agent()
    test_route_search_vs_browser_collision_resolves_to_repo_agent()
    test_route_code_extension_hint_reaches_repo_agent_without_a_bare_noun()
    test_route_code_extension_hint_does_not_regress_file_system_routing()
    test_route_disclosed_trade_off_code_extension_file_delete_is_ambiguous()
    test_parser_extracts_search_query_from_several_phrasings()
    test_parser_run_tests_needs_no_further_target()
    test_parser_edit_requires_an_extractable_file()
    test_parser_refuses_a_vague_objective()
    test_execute_task_search_reaches_repo_agent_and_reports_real_success()
    test_execute_task_run_tests_reports_real_failure_honestly()
    test_execute_task_ambiguous_development_objective_is_inconclusive()
    test_fix_the_failing_test_workflow_requires_every_step_to_verify()
    test_fix_the_failing_test_workflow_stops_honestly_if_tests_still_fail()
    test_repo_edit_confirmation_is_threaded_through_end_to_end()
    test_confirmation_required_repo_result_is_never_bypassed_by_recovery()
    test_blocked_repo_result_is_never_bypassed_by_recovery()
    test_repo_agent_has_no_recovery_chain_entry()
    test_repo_agent_belongs_to_the_development_family()
    test_repo_agent_has_no_inspect_config_entry()
    test_single_objective_repo_result_is_still_byte_for_byte_the_raw_result()
    print("\nAll task_engine_j8 tests passed.")


if __name__ == "__main__":
    _run()
