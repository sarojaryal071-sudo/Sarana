"""
tests/test_task_engine_phase5a.py — Phase 5A (multi-objective task
sequencing, structural foundation) of the JARVIS execution-architecture
mission.

Covers: backward compatibility (a legacy single `objective` call behaves
byte-for-byte as before), JARVIS-owned plan construction (build_plan()
routes every incoming objective via the EXISTING route() before anything
executes — Gemini can never supply a domain directly), pre-validation
(bounded objective count, empty objectives, an unroutable objective
rejecting the WHOLE plan before any execution), sequencing vs. recovery
(PlanSteps execute in order and may cross families; a same-family
recovery hop inside one PlanStep never advances to the next one), honest
failure propagation (a permanently-failed/blocked/confirmation-required
PlanStep stops the task, later PlanSteps never run, already-completed
ones are never rerun), the new TaskContext (a verified result can
populate a small structured value a later objective explicitly opts
into consuming — Office is the one Phase 5A consumer — and this is
strictly runtime/per-task, never shared across separate tasks), and
task-level verification (VERIFIED_SUCCESS only when EVERY PlanStep
verified success).

Per this project's own established convention: every underlying
capability (computer_settings/office_control/system_shortcuts/
youtube_video/browser_control) is ALWAYS mocked here.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_phase5a
"""
import inspect
import time
from unittest.mock import patch, MagicMock

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    """Same dict-capture pitfall as _HANDLERS elsewhere in this project —
    patch.dict on the dict ENTRIES, not patch.object on the bare _run_*
    names."""
    return patch.dict(te._HANDLERS, overrides)


# ── Backward compatibility ──────────────────────────────────────────────

def test_single_objective_call_behaves_identically_to_pre_phase5a() -> None:
    m_yt = MagicMock(return_value="[VERIFIED_SUCCESS] Playing: X.")
    with _handlers(youtube=m_yt):
        result = _task(objective="play X on YouTube")
    assert result == "[VERIFIED_SUCCESS] Playing: X."
    m_yt.assert_called_once()
    print("test_single_objective_call_behaves_identically_to_pre_phase5a: PASS")


def test_objectives_key_takes_precedence_when_both_are_given() -> None:
    m_yt = MagicMock(return_value="[VERIFIED_SUCCESS] a")
    m_ss = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 10.")
    with _handlers(youtube=m_yt, system_shortcut=m_ss):
        _task(objective="play X on YouTube", objectives=["check my battery percentage"])
    m_ss.assert_called_once()
    m_yt.assert_not_called()
    print("test_objectives_key_takes_precedence_when_both_are_given: PASS")


# ── Task/PlanStep/TaskContext structure ─────────────────────────────────

def test_task_objectives_defaults_from_legacy_single_objective_constructor() -> None:
    task = te.Task("play X on YouTube")
    assert task.objectives == ["play X on YouTube"]
    assert task.objective == "play X on YouTube"
    assert task.plan == []
    assert isinstance(task.task_context, te.TaskContext)
    print("test_task_objectives_defaults_from_legacy_single_objective_constructor: PASS")


def test_task_record_defaults_plan_index_to_zero_for_legacy_callers() -> None:
    task = te.Task("play X on YouTube")
    step = task.record("youtube", "[VERIFIED_SUCCESS] Playing: X.", time.monotonic())
    assert step.plan_index == 0
    print("test_task_record_defaults_plan_index_to_zero_for_legacy_callers: PASS")


# ── Plan construction: JARVIS routes, Gemini only supplies text ────────

def test_build_plan_creates_one_planstep_per_objective_with_jarvis_routed_domains() -> None:
    plan, error = te.build_plan(["check my battery status", "set cell A1 to 5"])
    assert error is None
    assert len(plan) == 2
    assert all(isinstance(p, te.PlanStep) for p in plan)
    assert plan[0].domain == "system_shortcut"
    assert plan[1].domain == "office"
    print("test_build_plan_creates_one_planstep_per_objective_with_jarvis_routed_domains: PASS")


def test_build_plan_signature_accepts_only_objective_strings_never_a_domain() -> None:
    # Structural proof, not just behavioral: build_plan()'s own signature
    # has no field Gemini could use to supply a domain/tool name even if
    # it tried — the ONLY input is plain objective text.
    sig = inspect.signature(te.build_plan)
    assert list(sig.parameters) == ["objectives"]
    print("test_build_plan_signature_accepts_only_objective_strings_never_a_domain: PASS")


def test_build_plan_domain_always_matches_what_route_would_independently_return() -> None:
    objective = "check my battery status"
    plan, _ = te.build_plan([objective])
    assert plan[0].domain == te.route(objective)
    print("test_build_plan_domain_always_matches_what_route_would_independently_return: PASS")


# ── Pre-validation ───────────────────────────────────────────────────────

def test_build_plan_rejects_an_empty_objectives_list() -> None:
    plan, error = te.build_plan([])
    assert plan is None
    assert "no objective" in error
    print("test_build_plan_rejects_an_empty_objectives_list: PASS")


def test_build_plan_rejects_a_blank_objective_string() -> None:
    plan, error = te.build_plan(["check my battery status", "   "])
    assert plan is None
    assert "empty" in error
    print("test_build_plan_rejects_a_blank_objective_string: PASS")


def test_build_plan_rejects_more_objectives_than_the_bounded_maximum() -> None:
    too_many = ["check my battery status"] * (te._MAX_OBJECTIVES_PER_TASK + 1)
    plan, error = te.build_plan(too_many)
    assert plan is None
    assert str(te._MAX_OBJECTIVES_PER_TASK) in error
    print("test_build_plan_rejects_more_objectives_than_the_bounded_maximum: PASS")


def test_build_plan_rejects_the_whole_plan_when_any_objective_is_unroutable() -> None:
    plan, error = te.build_plan(["check my battery status", "what is the capital of France"])
    assert plan is None
    assert "no known JARVIS capability" in error
    print("test_build_plan_rejects_the_whole_plan_when_any_objective_is_unroutable: PASS")


def test_unroutable_objective_in_a_multi_objective_task_executes_nothing() -> None:
    m_ss = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52.")
    with _handlers(system_shortcut=m_ss):
        result = _task(objectives=["check my battery status", "what is the capital of France"])
    m_ss.assert_not_called()  # rejected BEFORE anything executes — no partial execution
    assert result.startswith("[INCONCLUSIVE]")
    print("test_unroutable_objective_in_a_multi_objective_task_executes_nothing: PASS")


# ── Sequencing: forward through the plan, may cross families ───────────

def test_plan_step_0_executes_before_plan_step_1() -> None:
    order = []
    def h1(objective, confirmed=False, context=None):
        order.append("battery")
        return "[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True."
    def h2(objective, confirmed=False, context=None):
        order.append("cell")
        return "[VERIFIED_SUCCESS] A1 is now 52."
    with _handlers(system_shortcut=h1, office=h2):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    assert order == ["battery", "cell"]
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_plan_step_0_executes_before_plan_step_1: PASS")


def test_sequencing_may_cross_capability_families() -> None:
    assert te.family_of("system_shortcut") == te.FAMILY_SYSTEM
    assert te.family_of("office") == te.FAMILY_APPLICATION
    m_sys = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.")
    m_office = MagicMock(return_value="[VERIFIED_SUCCESS] A1 is now 52.")
    with _handlers(system_shortcut=m_sys, office=m_office):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    m_sys.assert_called_once()
    m_office.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_sequencing_may_cross_capability_families: PASS")


# ── Recovery: unchanged, family-scoped, never becomes sequencing ───────

def test_recovery_still_works_within_one_plan_step_in_a_multi_objective_task() -> None:
    m_yt = MagicMock(return_value="[INCONCLUSIVE] unsure on youtube.")
    m_br = MagicMock(return_value="[VERIFIED_SUCCESS] Opened: https://youtube.com/results")
    m_office = MagicMock(return_value="[VERIFIED_SUCCESS] A1 is now 1.")
    with _handlers(youtube=m_yt, browser=m_br, office=m_office):
        result = _task(objectives=["play some obscure Kafle remix on YouTube", "set cell A1 to 1"])
    m_br.assert_called_once()   # recovered WITHIN PlanStep 0
    m_office.assert_called_once()  # PlanStep 1 still ran afterward
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_recovery_still_works_within_one_plan_step_in_a_multi_objective_task: PASS")


def test_recovery_hop_never_advances_to_the_next_plan_step() -> None:
    call_order = []
    def yt(objective, confirmed=False, context=None):
        call_order.append("youtube")
        return "[INCONCLUSIVE] unsure."
    def br(objective, confirmed=False, context=None):
        call_order.append("browser")
        return "[VERIFIED_SUCCESS] Opened."
    def office(objective, confirmed=False, context=None):
        call_order.append("office")
        return "[VERIFIED_SUCCESS] A1 is now 1."
    with _handlers(youtube=yt, browser=br, office=office):
        _task(objectives=["play some obscure Kafle remix on YouTube", "set cell A1 to 1"])
    # office must run strictly AFTER browser's recovery resolved PlanStep
    # 0 — never interleaved with or mistaken for it.
    assert call_order == ["youtube", "browser", "office"]
    print("test_recovery_hop_never_advances_to_the_next_plan_step: PASS")


# ── Failure: stops the whole task, never skips ahead, never reruns ─────

def test_failed_plan_step_stops_subsequent_objectives() -> None:
    m1 = MagicMock(return_value="[VERIFIED_FAILURE] could not read battery.")
    m2 = MagicMock()
    with _handlers(system_shortcut=m1, office=m2):
        result = _task(objectives=["check my battery percentage", "set cell A1 to 1"])
    m2.assert_not_called()
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_failed_plan_step_stops_subsequent_objectives: PASS")


def test_blocked_plan_step_stops_the_whole_task() -> None:
    m1 = MagicMock(return_value="[BLOCKED] not allowed.")
    m2 = MagicMock()
    with _handlers(system_power=m1, office=m2):
        result = _task(objectives=["shut down my computer", "set cell A1 to 1"])
    m2.assert_not_called()
    assert result.startswith("[BLOCKED]")
    print("test_blocked_plan_step_stops_the_whole_task: PASS")


def test_confirmation_required_plan_step_stops_the_task_without_rerunning_completed_steps() -> None:
    m1 = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52.")
    m2 = MagicMock(return_value="[CONFIRMATION_REQUIRED] this will shutdown the computer.")
    m3 = MagicMock()
    with _handlers(system_shortcut=m1, system_power=m2, office=m3):
        result = _task(objectives=[
            "check my battery percentage", "shut down my computer", "set cell A1 to 1",
        ])
    m1.assert_called_once()   # completed step is NOT rerun
    m2.assert_called_once()
    m3.assert_not_called()    # never reached
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_confirmation_required_plan_step_stops_the_task_without_rerunning_completed_steps: PASS")


# ── TaskContext: structured, opt-in, runtime-only ───────────────────────

def test_verified_success_populates_task_context_values() -> None:
    context = te.TaskContext()
    te._extract_context_values("system_shortcut", "[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.", context)
    assert context.values["percent"] == "52"
    print("test_verified_success_populates_task_context_values: PASS")


def test_subsequent_objective_can_consume_an_explicitly_supported_context_value() -> None:
    context = te.TaskContext()
    context.values["percent"] = "52"
    params = te._parse_office_action("put that percentage into cell A1", context)
    assert params == {"app": "excel", "action": "set_cell", "cell": "A1", "value": 52}
    print("test_subsequent_objective_can_consume_an_explicitly_supported_context_value: PASS")


def test_context_value_is_never_used_without_a_referential_word_in_the_objective() -> None:
    context = te.TaskContext()
    context.values["percent"] = "52"
    # No "that"/"it" in the text — office_control's honest INCONCLUSIVE
    # path fires instead of silently guessing a number never asked for.
    params = te._parse_office_action("open cell A1", context)
    assert params is None
    print("test_context_value_is_never_used_without_a_referential_word_in_the_objective: PASS")


def test_unrelated_handlers_are_unaffected_by_context_support() -> None:
    context = te.TaskContext()
    context.values["percent"] = "99"
    with patch.object(te, "computer_settings", return_value="[VERIFIED_SUCCESS] volume is now 40%.") as m_cs:
        te._run_system_volume("set my volume to 40 percent", context=context)
    m_cs.assert_called_once_with(parameters={"action": "volume_set", "value": "40"})
    print("test_unrelated_handlers_are_unaffected_by_context_support: PASS")


def test_end_to_end_battery_percentage_flows_into_the_office_cell() -> None:
    m_battery = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.")
    with patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] A1 is now 52.") as m_oc, \
         _handlers(system_shortcut=m_battery):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    m_oc.assert_called_once_with(parameters={"app": "excel", "action": "set_cell", "cell": "A1", "value": 52})
    assert result == "[VERIFIED_SUCCESS] A1 is now 52."
    print("test_end_to_end_battery_percentage_flows_into_the_office_cell: PASS")


def test_task_context_is_runtime_only_and_never_shared_across_separate_tasks() -> None:
    with _handlers(system_shortcut=MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.")):
        _task(objective="check my battery percentage")
    # A brand-new, unrelated task must never see the PREVIOUS task's
    # context — TaskContext lives on one Task instance only, never
    # module-level/global/persisted state.
    with patch.object(te, "office_control") as m_oc:
        _task(objective="put that percentage into cell A1")
    m_oc.assert_not_called()  # no context available -> honestly INCONCLUSIVE, never guesses
    print("test_task_context_is_runtime_only_and_never_shared_across_separate_tasks: PASS")


# ── Verification: task-level success requires EVERY PlanStep to verify ─

def test_task_verified_success_requires_every_planstep_to_succeed() -> None:
    m1 = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52.")
    m2 = MagicMock(return_value="[VERIFIED_SUCCESS] A1 is now 52.")
    with _handlers(system_shortcut=m1, office=m2):
        result = _task(objectives=["check my battery percentage", "set cell A1 to 52"])
    assert result.startswith("[VERIFIED_SUCCESS]")
    m1.assert_called_once()
    m2.assert_called_once()
    print("test_task_verified_success_requires_every_planstep_to_succeed: PASS")


def test_inconclusive_final_step_is_never_reported_as_task_success() -> None:
    m1 = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52.")
    m2 = MagicMock(return_value="[INCONCLUSIVE] not sure this worked.")
    with _handlers(system_shortcut=m1, office=m2):
        result = _task(objectives=["check my battery percentage", "set cell A1 to 52"])
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_inconclusive_final_step_is_never_reported_as_task_success: PASS")


def _run() -> None:
    test_single_objective_call_behaves_identically_to_pre_phase5a()
    test_objectives_key_takes_precedence_when_both_are_given()
    test_task_objectives_defaults_from_legacy_single_objective_constructor()
    test_task_record_defaults_plan_index_to_zero_for_legacy_callers()
    test_build_plan_creates_one_planstep_per_objective_with_jarvis_routed_domains()
    test_build_plan_signature_accepts_only_objective_strings_never_a_domain()
    test_build_plan_domain_always_matches_what_route_would_independently_return()
    test_build_plan_rejects_an_empty_objectives_list()
    test_build_plan_rejects_a_blank_objective_string()
    test_build_plan_rejects_more_objectives_than_the_bounded_maximum()
    test_build_plan_rejects_the_whole_plan_when_any_objective_is_unroutable()
    test_unroutable_objective_in_a_multi_objective_task_executes_nothing()
    test_plan_step_0_executes_before_plan_step_1()
    test_sequencing_may_cross_capability_families()
    test_recovery_still_works_within_one_plan_step_in_a_multi_objective_task()
    test_recovery_hop_never_advances_to_the_next_plan_step()
    test_failed_plan_step_stops_subsequent_objectives()
    test_blocked_plan_step_stops_the_whole_task()
    test_confirmation_required_plan_step_stops_the_task_without_rerunning_completed_steps()
    test_verified_success_populates_task_context_values()
    test_subsequent_objective_can_consume_an_explicitly_supported_context_value()
    test_context_value_is_never_used_without_a_referential_word_in_the_objective()
    test_unrelated_handlers_are_unaffected_by_context_support()
    test_end_to_end_battery_percentage_flows_into_the_office_cell()
    test_task_context_is_runtime_only_and_never_shared_across_separate_tasks()
    test_task_verified_success_requires_every_planstep_to_succeed()
    test_inconclusive_final_step_is_never_reported_as_task_success()
    print("\nAll task_engine_phase5a tests passed.")


if __name__ == "__main__":
    _run()
