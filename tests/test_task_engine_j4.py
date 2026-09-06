"""
tests/test_task_engine_j4.py — J4 (Plan -> Act -> Verify) of the JARVIS
execution-architecture mission.

Phase 5A/5B already built PLAN (build_plan), ACT (a handler call inside
_execute_step), and VERIFY (that same call's classifier/Result-Envelope
status) as real, distinct steps — this file is NOT re-proving those (see
test_task_engine_phase5a.py/test_task_engine_phase5b.py, both re-verified
passing unchanged alongside this one). What's actually new for J4, and
what this file actually covers:

  1. A multi-objective Task's FINAL REPORT now covers every objective
     actually attempted, not just the terminating one's raw evidence
     (task_engine.py's _build_final_report()/_finalize_result()) — the
     roadmap's own J4 exit criteria ("the FINAL report matches the
     ORIGINAL objective, not just the last step").
  2. TASK_INCONCLUSIVE as its own task.state, distinct from TASK_FAILED
     (a verified failure and "couldn't tell" are different outcomes).
  3. CANCELLED is now explicitly terminal in _execute_step() — never
     silently escalated into a recovery-chain hop the way INCONCLUSIVE/
     UI_AMBIGUOUS correctly are.
  4. A single-objective Task's return value is completely untouched by
     any of the above (still byte-for-byte the raw handler result).

Per this project's own established convention: every underlying
capability (computer_settings/office_control/system_shortcuts/
youtube_video/browser_control) is ALWAYS mocked here — this file proves
task_engine.py's OWN orchestration logic, not the capabilities
underneath it (the real, non-mocked battery -> Excel / Settings-pane
end-to-end runs are reported in the J4 completion report, not repeated
here on every test pass, same convention test_task_engine_phase5b.py's
own header already documents).

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_j4
"""
from unittest.mock import MagicMock

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    from unittest.mock import patch
    return patch.dict(te._HANDLERS, overrides)


# ── A. Successful single objective ──────────────────────────────────────

def test_a_successful_single_objective_reaches_verified_success_unchanged() -> None:
    m = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 80, PluggedIn: True.")
    with _handlers(system_shortcut=m):
        result = _task(objective="check my battery percentage")
    # J4 requirement 4: a single-objective task's return is byte-for-byte
    # the raw handler result — no report synthesis applied.
    assert result == "[VERIFIED_SUCCESS] Percent: 80, PluggedIn: True."
    print("test_a_successful_single_objective_reaches_verified_success_unchanged: PASS")


# ── B. Action appears successful but cannot be verified -> INCONCLUSIVE ─

def test_b_action_attempted_but_unverifiable_reports_inconclusive_not_success() -> None:
    # system_shortcuts.py's own real design: a pane-OPEN path has no
    # ground truth to confirm the pane is now actually visible, so it
    # returns a plain (untagged) string — _classify_system_shortcut_result
    # honestly classifies that as INCONCLUSIVE, never VERIFIED_SUCCESS
    # merely because the call didn't raise. Exercised via the REAL
    # classifier (system_shortcut is NOT mocked here) with a stand-in
    # system_shortcuts.system_shortcut() call.
    from unittest.mock import patch
    with patch.object(te.system_shortcuts, "system_shortcut", return_value="Opened Bluetooth settings."):
        result = _task(objective="open bluetooth settings")
    assert result.startswith("[INCONCLUSIVE]")
    assert "assume it worked" in result.lower()  # result_envelope.py's own honesty directive
    print("test_b_action_attempted_but_unverifiable_reports_inconclusive_not_success: PASS")


# ── C. Objective verification failure -> VERIFIED_FAILURE ──────────────

def test_c_verification_proves_objective_failed() -> None:
    m = MagicMock(return_value="[VERIFIED_FAILURE] requested 52 but A1 now reads 0.")
    with _handlers(office=m):
        result = _task(objective="set cell A1 to 52")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_c_verification_proves_objective_failed: PASS")


# ── D. Multi-objective success: final report covers EVERY objective ────

def test_d_multi_objective_success_report_covers_every_objective_not_just_the_last() -> None:
    m1 = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 63, PluggedIn: False.")
    m2 = MagicMock(return_value="[VERIFIED_SUCCESS] A1 is now 63.")
    with _handlers(system_shortcut=m1, office=m2):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    assert result.startswith("[VERIFIED_SUCCESS]")
    # THE J4 gap this closes: the FIRST objective's own evidence must be
    # present, not just the terminating (second) objective's.
    assert "Percent: 63" in result
    assert "A1 is now 63" in result
    assert "battery percentage" in result   # the objective TEXT itself, not just its evidence
    assert "cell A1" in result
    print("test_d_multi_objective_success_report_covers_every_objective_not_just_the_last: PASS")


# ── E. Earlier objective failure stops later ones from ever running ────

def test_e_earlier_failure_prevents_later_objectives_from_executing() -> None:
    m1 = MagicMock(return_value="[VERIFIED_FAILURE] could not read battery: no sensor.")
    m2 = MagicMock()
    m3 = MagicMock()
    with _handlers(system_shortcut=m1, office=m2, system_volume=m3):
        result = _task(objectives=[
            "check my battery percentage", "set cell A1 to 1", "set my volume to 40 percent",
        ])
    m2.assert_not_called()
    m3.assert_not_called()
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_e_earlier_failure_prevents_later_objectives_from_executing: PASS")


# ── F. Last-step-success is NOT the same as whole-task-success ─────────

def test_f_final_result_is_never_just_the_last_steps_own_status() -> None:
    # Every PlanStep individually reaching a non-failure status is not
    # enough — the report must make clear which objective actually
    # determined the (non-success) outcome, with earlier verified
    # objectives still visible for context, never silently dropped.
    m1 = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 90, PluggedIn: True.")
    m2 = MagicMock(return_value="[INCONCLUSIVE] not sure this worked.")
    with _handlers(system_shortcut=m1, office=m2):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[INCONCLUSIVE]")
    assert "Percent: 90" in result   # the earlier, genuinely-verified objective is still reported
    print("test_f_final_result_is_never_just_the_last_steps_own_status: PASS")


# ── G. BLOCKED remains terminal — never converted to success/recovery ──

def test_g_blocked_step_never_recovers_even_when_a_recovery_chain_entry_exists() -> None:
    from unittest.mock import patch
    m_yt = MagicMock(return_value="[BLOCKED] not allowed by policy.")
    m_br = MagicMock()  # youtube -> browser IS a real _RECOVERY_CHAIN entry
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play something on youtube")
    m_br.assert_not_called()   # BLOCKED must never trigger the recovery hop
    assert result.startswith("[BLOCKED]")
    print("test_g_blocked_step_never_recovers_even_when_a_recovery_chain_entry_exists: PASS")


def test_g_task_state_is_blocked_not_failed() -> None:
    assert te._task_state_for(te._envelope.STATUS_BLOCKED) == te.TASK_BLOCKED
    print("test_g_task_state_is_blocked_not_failed: PASS")


# ── H. CANCELLED remains terminal — the actual J4 correctness fix ──────

def test_h_cancelled_step_never_recovers_even_when_a_recovery_chain_entry_exists() -> None:
    # Confirmed gap this closes: before this fix, CANCELLED matched none
    # of _execute_step()'s explicit terminal branches and fell through to
    # the SAME implicit recovery-chain path INCONCLUSIVE/UI_AMBIGUOUS
    # correctly use — silently overriding an explicit stop request by
    # trying a different method.
    m_yt = MagicMock(return_value="[CANCELLED] stopped before completion.")
    m_br = MagicMock()
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play something on youtube")
    m_br.assert_not_called()
    assert result.startswith("[CANCELLED]")
    print("test_h_cancelled_step_never_recovers_even_when_a_recovery_chain_entry_exists: PASS")


# ── I. Existing recovery still works, remains family-scoped ────────────

def test_i_existing_recovery_still_resolves_within_one_planstep() -> None:
    m_yt = MagicMock(return_value="[INCONCLUSIVE] unsure on youtube.")
    m_br = MagicMock(return_value="[VERIFIED_SUCCESS] Opened: https://youtube.com/results")
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play some obscure remix on youtube")
    m_br.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_i_existing_recovery_still_resolves_within_one_planstep: PASS")


# ── Task state: TASK_INCONCLUSIVE distinct from TASK_FAILED (new) ──────
# _task_state_for() is the exact function execute_task() itself calls to
# decide task.state — tested directly here (deterministic, no mocking
# needed) per section 9's own 'keep state transitions testable' ask.

def test_task_state_inconclusive_is_distinct_from_failed() -> None:
    assert te._task_state_for(te._envelope.STATUS_INCONCLUSIVE) == te.TASK_INCONCLUSIVE
    assert te._task_state_for(te._envelope.STATUS_UI_AMBIGUOUS) == te.TASK_INCONCLUSIVE
    assert te._task_state_for(te._envelope.STATUS_VERIFIED_FAILURE) == te.TASK_FAILED
    assert te.TASK_INCONCLUSIVE != te.TASK_FAILED
    print("test_task_state_inconclusive_is_distinct_from_failed: PASS")


def test_task_state_cancelled_is_reachable_and_distinct_from_blocked() -> None:
    assert te._task_state_for(te._envelope.STATUS_CANCELLED) == te.TASK_CANCELLED
    assert te.TASK_CANCELLED != te.TASK_BLOCKED
    print("test_task_state_cancelled_is_reachable_and_distinct_from_blocked: PASS")


def test_task_state_confirmation_required_maps_to_awaiting_confirmation() -> None:
    assert te._task_state_for(te._envelope.STATUS_CONFIRMATION_REQUIRED) == te.TASK_AWAITING_CONFIRMATION
    print("test_task_state_confirmation_required_maps_to_awaiting_confirmation: PASS")


def test_task_state_unrecognized_status_defaults_to_failed_not_silently_ignored() -> None:
    assert te._task_state_for("SOME_FUTURE_STATUS_THIS_MODULE_DOESNT_KNOW_YET") == te.TASK_FAILED
    print("test_task_state_unrecognized_status_defaults_to_failed_not_silently_ignored: PASS")


# ── _build_final_report() / _strip_status_tag() direct coverage ────────

def test_strip_status_tag_removes_a_leading_bracketed_tag() -> None:
    assert te._strip_status_tag("[VERIFIED_SUCCESS] A1 is now 52.") == "A1 is now 52."
    print("test_strip_status_tag_removes_a_leading_bracketed_tag: PASS")


def test_strip_status_tag_leaves_an_untagged_string_unchanged() -> None:
    assert te._strip_status_tag("Opened Bluetooth settings.") == "Opened Bluetooth settings."
    print("test_strip_status_tag_leaves_an_untagged_string_unchanged: PASS")


def test_build_final_report_never_mentions_internal_domain_names() -> None:
    m1 = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 41, PluggedIn: True.")
    m2 = MagicMock(return_value="[VERIFIED_SUCCESS] A1 is now 41.")
    with _handlers(system_shortcut=m1, office=m2):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    # Internal routing detail — never surfaced to Gemini/the user (J4's
    # own "do not expose internal implementation details" requirement).
    assert "system_shortcut" not in result
    assert "office" not in result.lower().split("percentage")[0]  # no bare domain-name leakage
    print("test_build_final_report_never_mentions_internal_domain_names: PASS")


def test_build_final_report_reports_remaining_objectives_as_not_attempted() -> None:
    m1 = MagicMock(return_value="[VERIFIED_FAILURE] could not read battery.")
    m2 = MagicMock()
    with _handlers(system_shortcut=m1, office=m2):
        result = _task(objectives=["check my battery percentage", "set cell A1 to 1", "set my volume to 40 percent"])
    assert "remaining" in result.lower()
    assert "not attempted" in result.lower()
    print("test_build_final_report_reports_remaining_objectives_as_not_attempted: PASS")


def _run() -> None:
    test_a_successful_single_objective_reaches_verified_success_unchanged()
    test_b_action_attempted_but_unverifiable_reports_inconclusive_not_success()
    test_c_verification_proves_objective_failed()
    test_d_multi_objective_success_report_covers_every_objective_not_just_the_last()
    test_e_earlier_failure_prevents_later_objectives_from_executing()
    test_f_final_result_is_never_just_the_last_steps_own_status()
    test_g_blocked_step_never_recovers_even_when_a_recovery_chain_entry_exists()
    test_g_task_state_is_blocked_not_failed()
    test_h_cancelled_step_never_recovers_even_when_a_recovery_chain_entry_exists()
    test_i_existing_recovery_still_resolves_within_one_planstep()
    test_task_state_inconclusive_is_distinct_from_failed()
    test_task_state_cancelled_is_reachable_and_distinct_from_blocked()
    test_task_state_confirmation_required_maps_to_awaiting_confirmation()
    test_task_state_unrecognized_status_defaults_to_failed_not_silently_ignored()
    test_strip_status_tag_removes_a_leading_bracketed_tag()
    test_strip_status_tag_leaves_an_untagged_string_unchanged()
    test_build_final_report_never_mentions_internal_domain_names()
    test_build_final_report_reports_remaining_objectives_as_not_attempted()
    print("\nAll task_engine_j4 tests passed.")


if __name__ == "__main__":
    _run()
