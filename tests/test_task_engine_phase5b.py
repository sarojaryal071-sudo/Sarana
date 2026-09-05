"""
tests/test_task_engine_phase5b.py — Phase 5B (first real compound
workflow) of the JARVIS execution-architecture mission.

Proves the Phase 5A mechanism (build_plan/PlanStep/_execute_step/
TaskContext) with the actual APPROVED cross-family workflow — "check the
battery percentage, then put that percentage into a specific cell" —
using Gemini-shaped `objectives[]` exactly as jarvis_task's tool schema
documents it, through the SAME code path a real jarvis_task call uses.

A note on phrasing: the illustrative "put it into Excel" (no cell named)
is genuinely NOT executable — office_control.py has no way to know
WHERE to write, and _run_office() honestly returns INCONCLUSIVE rather
than guessing a cell (see tests/test_task_engine_office.py's own
"bare open" tests for the same principle). This file uses the corrected,
concrete phrasing jarvis_task's own tool description now asks Gemini
for: "...put that percentage into cell A1" — see main.py's jarvis_task
description update alongside this file.

Per this project's own established convention: system_shortcuts/
office_control are ALWAYS mocked in THIS file — the real, non-mocked
end-to-end run (through the actual main.py dispatch, a real battery
read, and a real Excel COM write/read-back) was performed separately and
is reported in the Phase 5B report, not re-run here on every test pass.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_phase5b
"""
from unittest.mock import patch, MagicMock

import actions.task_engine as te

BATTERY_OBJECTIVE = "Check the battery percentage."
CELL_OBJECTIVE = "Put that percentage into cell A1."


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    return patch.dict(te._HANDLERS, overrides)


# ── 1/2. Gemini supplies plain-language objectives; JARVIS derives the plan ─

def test_gemini_shaped_objectives_produce_the_correct_jarvis_routed_plan() -> None:
    plan, error = te.build_plan([BATTERY_OBJECTIVE, CELL_OBJECTIVE])
    assert error is None
    assert [p.domain for p in plan] == ["system_shortcut", "office"]
    assert [p.objective for p in plan] == [BATTERY_OBJECTIVE, CELL_OBJECTIVE]
    print("test_gemini_shaped_objectives_produce_the_correct_jarvis_routed_plan: PASS")


# ── 3. SYSTEM -> APPLICATION sequencing ─────────────────────────────────

def test_system_to_application_sequencing_executes_both_plansteps_in_order() -> None:
    call_order = []
    def battery(objective, confirmed=False, context=None):
        call_order.append("system_shortcut")
        return "[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True."
    def cell(objective, confirmed=False, context=None):
        call_order.append("office")
        return "[VERIFIED_SUCCESS] A1 is now 52."
    with _handlers(system_shortcut=battery, office=cell):
        result = _task(objectives=[BATTERY_OBJECTIVE, CELL_OBJECTIVE])
    assert call_order == ["system_shortcut", "office"]
    assert result == "[VERIFIED_SUCCESS] A1 is now 52."
    print("test_system_to_application_sequencing_executes_both_plansteps_in_order: PASS")


# ── 4/5. Battery result enters TaskContext; Excel consumes it ──────────

def test_battery_percent_enters_context_and_office_consumes_it() -> None:
    with patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] A1 is now 52.") as m_oc, \
         _handlers(system_shortcut=MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.")):
        result = _task(objectives=[BATTERY_OBJECTIVE, CELL_OBJECTIVE])
    m_oc.assert_called_once_with(parameters={"app": "excel", "action": "set_cell", "cell": "A1", "value": 52})
    assert result == "[VERIFIED_SUCCESS] A1 is now 52."
    print("test_battery_percent_enters_context_and_office_consumes_it: PASS")


def test_vague_phrasing_with_no_cell_reference_is_honestly_inconclusive_not_guessed() -> None:
    # The illustrative "put it into Excel" (no cell named) — office_control.py
    # genuinely has no target to write to; JARVIS must not invent one.
    with patch.object(te, "office_control") as m_oc, \
         _handlers(system_shortcut=MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.")):
        result = _task(objectives=[BATTERY_OBJECTIVE, "Put that percentage into Excel."])
    m_oc.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_vague_phrasing_with_no_cell_reference_is_honestly_inconclusive_not_guessed: PASS")


# ── 6. Actual final verification is required, not merely "dispatched" ──

def test_excel_cell_readback_failure_is_not_reported_as_task_success() -> None:
    # office_control.py's own excel_set_cell() readback mismatch -> a
    # real VERIFIED_FAILURE it already returns; the task must propagate
    # that honestly, never upgrade "the call was made" into success.
    with patch.object(te, "office_control", return_value="[VERIFIED_FAILURE] requested 52 but A1 now reads 0.") as m_oc, \
         _handlers(system_shortcut=MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.")):
        result = _task(objectives=[BATTERY_OBJECTIVE, CELL_OBJECTIVE])
    m_oc.assert_called_once()
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_excel_cell_readback_failure_is_not_reported_as_task_success: PASS")


def test_task_reports_verified_success_only_when_both_plansteps_verify() -> None:
    with patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] A1 is now 52.") as m_oc, \
         _handlers(system_shortcut=MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.")):
        result = _task(objectives=[BATTERY_OBJECTIVE, CELL_OBJECTIVE])
    assert result.startswith("[VERIFIED_SUCCESS]")
    m_oc.assert_called_once()
    print("test_task_reports_verified_success_only_when_both_plansteps_verify: PASS")


# ── 7. Failed first step prevents the second ────────────────────────────

def test_failed_battery_query_prevents_the_excel_step_from_ever_running() -> None:
    m_oc = MagicMock()
    with patch.object(te, "office_control", m_oc), \
         _handlers(system_shortcut=MagicMock(return_value="[VERIFIED_FAILURE] could not read battery: no sensor.")):
        result = _task(objectives=[BATTERY_OBJECTIVE, CELL_OBJECTIVE])
    m_oc.assert_not_called()
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_failed_battery_query_prevents_the_excel_step_from_ever_running: PASS")


# ── 8. Existing recovery rules are unchanged by cross-family sequencing ─

def test_inconclusive_battery_query_does_not_recover_into_office() -> None:
    # Sequencing now legitimately crosses SYSTEM -> APPLICATION, but
    # RECOVERY must not: an escalatable system_shortcut result has no
    # _RECOVERY_CHAIN entry at all (Phase 3's own finding, unchanged) and
    # must never "recover" by jumping to the next PlanStep's domain.
    assert "system_shortcut" not in te._RECOVERY_CHAIN
    m_office = MagicMock()
    with patch.object(te, "office_control", m_office), \
         _handlers(system_shortcut=MagicMock(return_value="[INCONCLUSIVE] sensor busy, try again.")):
        result = _task(objectives=[BATTERY_OBJECTIVE, CELL_OBJECTIVE])
    m_office.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_inconclusive_battery_query_does_not_recover_into_office: PASS")


# ── 9. Existing single-objective behavior is unaffected ────────────────

def test_single_objective_battery_query_is_unaffected_by_phase5b() -> None:
    with _handlers(system_shortcut=MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True.")):
        result = _task(objective=BATTERY_OBJECTIVE)
    assert result == "[VERIFIED_SUCCESS] Percent: 52, PluggedIn: True."
    print("test_single_objective_battery_query_is_unaffected_by_phase5b: PASS")


# ── 10. Gemini cannot inject a domain/tool/execution method ─────────────

def test_gemini_cannot_supply_a_domain_only_plain_objective_text() -> None:
    # build_plan()'s only input is objective STRINGS; there is no field
    # anywhere in the objectives[] contract for a domain/tool/handler
    # name — even objective text that happens to spell a real domain
    # name is routed like any other text, never taken as a literal
    # instruction.
    plan, error = te.build_plan(["office", "system_shortcut browser youtube"])
    assert error is not None or plan is not None  # doesn't crash either way
    if plan is not None:
        for step in plan:
            assert step.domain == te.route(step.objective)
    print("test_gemini_cannot_supply_a_domain_only_plain_objective_text: PASS")


def _run() -> None:
    test_gemini_shaped_objectives_produce_the_correct_jarvis_routed_plan()
    test_system_to_application_sequencing_executes_both_plansteps_in_order()
    test_battery_percent_enters_context_and_office_consumes_it()
    test_vague_phrasing_with_no_cell_reference_is_honestly_inconclusive_not_guessed()
    test_excel_cell_readback_failure_is_not_reported_as_task_success()
    test_task_reports_verified_success_only_when_both_plansteps_verify()
    test_failed_battery_query_prevents_the_excel_step_from_ever_running()
    test_inconclusive_battery_query_does_not_recover_into_office()
    test_single_objective_battery_query_is_unaffected_by_phase5b()
    test_gemini_cannot_supply_a_domain_only_plain_objective_text()
    print("\nAll task_engine_phase5b tests passed.")


if __name__ == "__main__":
    _run()
