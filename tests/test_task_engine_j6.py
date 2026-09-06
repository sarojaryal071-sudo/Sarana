"""
tests/test_task_engine_j6.py — J6 (Computer/Application Perception) of
the JARVIS execution-architecture mission.

Inspection finding, stated up front: NONE of the six real domains
task_engine.py currently routes to (youtube/browser/office/
system_volume/system_power/system_shortcut) genuinely need "what window
is focused"/"what UI elements exist"/"a screenshot" to decide anything —
browser_control.py manages its own session state independently of OS
focus, office_control.py decides via its own COM ActiveWorkbook/
ActiveDocument, and the three system_* domains act on OS-wide settings
regardless of what's focused. `office` is the one domain marked
INSPECT-applicable in `_INSPECT_CONFIG` (window only — a real, low-risk,
genuinely relevant signal for a GUI-application domain, even though
office_control() doesn't itself consume it) — see that dict's own
comment for the full per-domain reasoning. This file proves the actual
NEW mechanism (inspect(), the _INSPECT_CONFIG wiring, Step.observation)
using both `office` (real production config) and one synthetic
INSPECT-applicable domain (to prove the mechanism generalizes beyond the
one real opt-in, same "fabricate a domain to prove the engine, not a
new production capability" technique test_task_engine_j5.py's own cycle-
defense test already used).

Per this project's own established convention: every underlying
capability (office_control/etc.) AND the perception primitives
(get_active_window_title/list_ui_elements/_capture_screen) are mocked
here — the real, non-mocked machine verification is reported in the J6
completion report, not repeated on every test pass.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_j6
"""
from unittest.mock import MagicMock, patch

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    return patch.dict(te._HANDLERS, overrides)


def _synthetic_inspect_domain():
    """A test-only domain marked INSPECT-applicable (window + ui_elements
    + screenshot all requested) — proves the mechanism generalizes beyond
    office's one real, narrower (window-only) opt-in, without asserting a
    new permanent production recovery/inspect relationship."""
    fake_domains = te._DOMAINS + [
        {"name": "fakeinspectdomain", "family": te.FAMILY_SYSTEM, "keywords": ["fakeinspectkeyword"]},
    ]
    fake_config = dict(te._INSPECT_CONFIG)
    fake_config["fakeinspectdomain"] = {
        "want_window": True, "want_ui_elements": True, "want_screenshot": True,
    }
    return fake_domains, fake_config


# ── A. INSPECT ordering: strictly before ACT, never after ──────────────

def test_a_inspect_runs_strictly_before_the_handler_call() -> None:
    fake_domains, fake_config = _synthetic_inspect_domain()
    call_order = []

    def fake_inspect(**kwargs):
        call_order.append("inspect")
        return te.Observation("Notepad", "a button", "captured")

    def fake_handler(objective, confirmed=False, context=None):
        call_order.append("act")
        return "[VERIFIED_SUCCESS] done."

    with patch.object(te, "_DOMAINS", fake_domains), \
         patch.object(te, "_INSPECT_CONFIG", fake_config), \
         patch.object(te, "inspect", side_effect=fake_inspect), \
         _handlers(fakeinspectdomain=fake_handler):
        _task(objective="fakeinspectkeyword")
    assert call_order == ["inspect", "act"]
    print("test_a_inspect_runs_strictly_before_the_handler_call: PASS")


def test_a_inspect_is_never_called_for_a_non_applicable_domain() -> None:
    # youtube has no _INSPECT_CONFIG entry -- inspect() must not run at all.
    with patch.object(te, "inspect") as m_inspect, \
         _handlers(youtube=MagicMock(return_value="[VERIFIED_SUCCESS] Playing: X.")):
        _task(objective="play X on youtube")
    m_inspect.assert_not_called()
    print("test_a_inspect_is_never_called_for_a_non_applicable_domain: PASS")


# ── B. Existing perception primitives are reused, not reimplemented ────

def test_b_inspect_calls_the_existing_get_active_window_title_not_a_new_one() -> None:
    with patch.object(te, "get_active_window_title", return_value="Microsoft Excel") as m_title:
        obs = te.inspect(want_window=True)
    m_title.assert_called_once()
    assert obs.active_window == "Microsoft Excel"
    print("test_b_inspect_calls_the_existing_get_active_window_title_not_a_new_one: PASS")


def test_b_inspect_calls_the_existing_list_ui_elements_not_a_new_one() -> None:
    with patch.object(te, "list_ui_elements", return_value="Button 'OK'") as m_ui:
        obs = te.inspect(want_window=False, want_ui_elements=True)
    m_ui.assert_called_once()
    assert obs.ui_elements == "Button 'OK'"
    print("test_b_inspect_calls_the_existing_list_ui_elements_not_a_new_one: PASS")


def test_b_inspect_calls_the_existing_capture_screen_not_a_new_one() -> None:
    with patch.object(te, "_capture_screen", return_value=(b"fakejpeg", "image/jpeg")) as m_cap:
        obs = te.inspect(want_window=False, want_screenshot=True)
    m_cap.assert_called_once()
    assert obs.screenshot == "captured"
    print("test_b_inspect_calls_the_existing_capture_screen_not_a_new_one: PASS")


def test_b_inspect_never_requests_a_signal_that_was_not_asked_for() -> None:
    with patch.object(te, "get_active_window_title") as m_title, \
         patch.object(te, "list_ui_elements") as m_ui, \
         patch.object(te, "_capture_screen") as m_cap:
        te.inspect(want_window=False, want_ui_elements=False, want_screenshot=False)
    m_title.assert_not_called()
    m_ui.assert_not_called()
    m_cap.assert_not_called()
    print("test_b_inspect_never_requests_a_signal_that_was_not_asked_for: PASS")


# ── C. Composed observation can contain all three available signals ────

def test_c_observation_can_carry_all_three_signals_at_once() -> None:
    with patch.object(te, "get_active_window_title", return_value="Notepad"), \
         patch.object(te, "list_ui_elements", return_value="Edit control"), \
         patch.object(te, "_capture_screen", return_value=(b"x", "image/jpeg")):
        obs = te.inspect(want_window=True, want_ui_elements=True, want_screenshot=True)
    assert obs.active_window == "Notepad"
    assert obs.ui_elements == "Edit control"
    assert obs.screenshot == "captured"
    print("test_c_observation_can_carry_all_three_signals_at_once: PASS")


def test_c_raw_screenshot_bytes_are_never_retained() -> None:
    # Section 5's own requirement: never store screenshots -- only a
    # short status word, never the pixel data itself. Observation uses
    # __slots__ (no arbitrary attributes possible) -- checking its own
    # three declared fields directly is the complete, exhaustive proof.
    with patch.object(te, "_capture_screen", return_value=(b"some large fake jpeg payload", "image/jpeg")):
        obs = te.inspect(want_window=False, want_screenshot=True)
    assert obs.screenshot == "captured"
    assert obs.__slots__ == ("active_window", "ui_elements", "screenshot")
    for slot in obs.__slots__:
        assert b"fake jpeg payload" not in str(getattr(obs, slot)).encode()
    print("test_c_raw_screenshot_bytes_are_never_retained: PASS")


# ── D. Partial/failed perception is never reported as a success ────────

def test_d_a_failed_screenshot_capture_is_reported_honestly_not_as_captured() -> None:
    with patch.object(te, "_capture_screen", side_effect=RuntimeError("mss is not installed")):
        obs = te.inspect(want_window=False, want_screenshot=True)
    assert obs.screenshot != "captured"
    assert "unavailable" in obs.screenshot
    print("test_d_a_failed_screenshot_capture_is_reported_honestly_not_as_captured: PASS")


def test_d_an_unavailable_active_window_stays_an_honest_empty_string() -> None:
    with patch.object(te, "get_active_window_title", return_value=""):
        obs = te.inspect(want_window=True)
    assert obs.active_window == ""   # never fabricated as "unknown but fine"
    print("test_d_an_unavailable_active_window_stays_an_honest_empty_string: PASS")


def test_d_ui_element_inspection_failure_is_passed_through_honestly() -> None:
    with patch.object(te, "list_ui_elements", return_value="Could not determine the foreground window."):
        obs = te.inspect(want_window=False, want_ui_elements=True)
    assert "Could not determine" in obs.ui_elements
    print("test_d_ui_element_inspection_failure_is_passed_through_honestly: PASS")


def test_d_a_perception_exception_does_not_crash_the_step_or_the_task() -> None:
    with patch.object(te, "list_ui_elements", side_effect=RuntimeError("UIA backend crashed")), \
         patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] A1 is now 5."), \
         patch.object(te, "_INSPECT_CONFIG", {"office": {"want_window": False, "want_ui_elements": True}}):
        result = _task(objective="set cell A1 to 5")
    assert result.startswith("[VERIFIED_SUCCESS]")   # the REAL action still ran and verified fine
    print("test_d_a_perception_exception_does_not_crash_the_step_or_the_task: PASS")


# ── E. Phase 5A TaskContext mechanism is unaffected ─────────────────────

def test_e_task_context_value_flow_is_unaffected_by_inspection() -> None:
    m_battery = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 61, PluggedIn: True.")
    with patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] A1 is now 61.") as m_oc, \
         _handlers(system_shortcut=m_battery):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    m_oc.assert_called_once_with(parameters={"app": "excel", "action": "set_cell", "cell": "A1", "value": 61})
    assert "Percent: 61" in result
    assert "A1 is now 61" in result
    print("test_e_task_context_value_flow_is_unaffected_by_inspection: PASS")


# ── F. Phase 5A/5B multi-objective flow is unaffected ───────────────────

def test_f_multi_objective_sequencing_still_executes_in_order() -> None:
    order = []
    def battery(objective, confirmed=False, context=None):
        order.append("battery")
        return "[VERIFIED_SUCCESS] Percent: 70, PluggedIn: True."
    def cell(objective, confirmed=False, context=None):
        order.append("cell")
        return "[VERIFIED_SUCCESS] A1 is now 70."
    with _handlers(system_shortcut=battery, office=cell):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    assert order == ["battery", "cell"]
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_f_multi_objective_sequencing_still_executes_in_order: PASS")


# ── G. J4 final reporting still reflects the objective, not inspection ─

def test_g_final_report_never_mentions_inspection_data() -> None:
    with patch.object(te, "get_active_window_title", return_value="Microsoft Excel — Book1"), \
         patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] A1 is now 5."), \
         _handlers(system_shortcut=MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 40, PluggedIn: True.")):
        result = _task(objectives=["check my battery percentage", "set cell A1 to 5"])
    assert "Percent: 40" in result and "A1 is now 5" in result   # the objectives' own evidence
    assert "Excel — Book1" not in result   # never the raw window-title observation text
    print("test_g_final_report_never_mentions_inspection_data: PASS")


def test_g_single_objective_result_is_still_byte_for_byte_the_raw_result() -> None:
    with patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] A1 is now 9."):
        result = _task(objective="set cell A1 to 9")
    assert result == "[VERIFIED_SUCCESS] A1 is now 9."
    print("test_g_single_objective_result_is_still_byte_for_byte_the_raw_result: PASS")


# ── H. J5 recovery is unaffected by INSPECT ─────────────────────────────

def test_h_recovery_still_resolves_within_one_planstep_with_inspection_present() -> None:
    m_yt = MagicMock(return_value="[INCONCLUSIVE] unsure on youtube.")
    m_br = MagicMock(return_value="[VERIFIED_SUCCESS] Opened: https://youtube.com/results")
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play some obscure remix on youtube")
    m_br.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_h_recovery_still_resolves_within_one_planstep_with_inspection_present: PASS")


# ── I. Terminal safety states can't be bypassed by INSPECT/recovery ────

def test_i_blocked_office_step_is_still_terminal_with_inspection_configured() -> None:
    with patch.object(te, "office_control", return_value="[BLOCKED] not allowed."):
        result = _task(objective="set cell A1 to 5")
    assert result.startswith("[BLOCKED]")
    print("test_i_blocked_office_step_is_still_terminal_with_inspection_configured: PASS")


def test_i_confirmation_required_office_step_is_still_terminal() -> None:
    with patch.object(te, "office_control", return_value="[CONFIRMATION_REQUIRED] needs a yes."):
        result = _task(objective="set cell A1 to 5")
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_i_confirmation_required_office_step_is_still_terminal: PASS")


def test_i_cancelled_office_step_is_still_terminal() -> None:
    with patch.object(te, "office_control", return_value="[CANCELLED] stopped."):
        result = _task(objective="set cell A1 to 5")
    assert result.startswith("[CANCELLED]")
    print("test_i_cancelled_office_step_is_still_terminal: PASS")


# ── Step.observation: the actual recorded artifact ──────────────────────

def test_step_records_the_observation_that_preceded_its_own_action() -> None:
    task = te.Task(objectives=["set cell A1 to 5"])
    task.plan, _ = te.build_plan(task.objectives)
    task.state = te.TASK_EXECUTING
    with patch.object(te, "get_active_window_title", return_value="Microsoft Excel"), \
         patch.object(te, "office_control", return_value="[VERIFIED_SUCCESS] A1 is now 5."):
        te._execute_step(task, 0, task.plan[0], confirmed=False)
    assert len(task.steps) == 1
    assert task.steps[0].observation is not None
    assert task.steps[0].observation.active_window == "Microsoft Excel"
    print("test_step_records_the_observation_that_preceded_its_own_action: PASS")


def test_step_observation_is_none_for_a_non_applicable_domain() -> None:
    task = te.Task(objectives=["play X on youtube"])
    task.plan, _ = te.build_plan(task.objectives)
    task.state = te.TASK_EXECUTING
    with patch.object(te, "youtube_video", return_value="[VERIFIED_SUCCESS] Playing: X."):
        te._execute_step(task, 0, task.plan[0], confirmed=False)
    assert task.steps[0].observation is None
    print("test_step_observation_is_none_for_a_non_applicable_domain: PASS")


def _run() -> None:
    test_a_inspect_runs_strictly_before_the_handler_call()
    test_a_inspect_is_never_called_for_a_non_applicable_domain()
    test_b_inspect_calls_the_existing_get_active_window_title_not_a_new_one()
    test_b_inspect_calls_the_existing_list_ui_elements_not_a_new_one()
    test_b_inspect_calls_the_existing_capture_screen_not_a_new_one()
    test_b_inspect_never_requests_a_signal_that_was_not_asked_for()
    test_c_observation_can_carry_all_three_signals_at_once()
    test_c_raw_screenshot_bytes_are_never_retained()
    test_d_a_failed_screenshot_capture_is_reported_honestly_not_as_captured()
    test_d_an_unavailable_active_window_stays_an_honest_empty_string()
    test_d_ui_element_inspection_failure_is_passed_through_honestly()
    test_d_a_perception_exception_does_not_crash_the_step_or_the_task()
    test_e_task_context_value_flow_is_unaffected_by_inspection()
    test_f_multi_objective_sequencing_still_executes_in_order()
    test_g_final_report_never_mentions_inspection_data()
    test_g_single_objective_result_is_still_byte_for_byte_the_raw_result()
    test_h_recovery_still_resolves_within_one_planstep_with_inspection_present()
    test_i_blocked_office_step_is_still_terminal_with_inspection_configured()
    test_i_confirmation_required_office_step_is_still_terminal()
    test_i_cancelled_office_step_is_still_terminal()
    test_step_records_the_observation_that_preceded_its_own_action()
    test_step_observation_is_none_for_a_non_applicable_domain()
    print("\nAll task_engine_j6 tests passed.")


if __name__ == "__main__":
    _run()
