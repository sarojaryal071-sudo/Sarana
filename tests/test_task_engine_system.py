"""
tests/test_task_engine_system.py — Phase 3 (System Capabilities) of the
JARVIS execution-architecture mission.

Covers the three new SYSTEM-family domains added to actions/task_engine.py
(system_volume, system_power, system_shortcut) — routing, family
classification, family-scoped recovery (including that SYSTEM<->APPLICATION
recovery is refused, not just untested), real execution against the
EXISTING computer_settings.py/system_shortcuts.py modules (never
duplicated), and honest result classification (including the specific
case Phase 3 exists to prove: a fire-and-forget Settings-page open must
NOT be reported as verified success).

Per this project's own established convention: computer_settings() and
system_shortcuts.system_shortcut() are ALWAYS mocked here — no test
actually changes the real system volume, puts the machine to sleep, or
opens a real Settings window. See test_task_engine_system_real.py for
the separate, explicitly-labeled real-machine verification.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_system
"""
from unittest.mock import patch, MagicMock

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    """Same dict-capture pitfall as _HANDLERS elsewhere in this project
    (computer_settings.ACTION_MAP, system_shortcuts._PSUTIL_HANDLERS) —
    patch.dict on the dict ENTRIES, not patch.object on the bare
    _run_* names."""
    return patch.dict(te._HANDLERS, overrides)


# ── Routing ──────────────────────────────────────────────────────────

def test_route_matches_system_volume_domain() -> None:
    assert te.route("set my volume to 40 percent") == "system_volume"
    assert te.route("turn the volume down") == "system_volume"
    print("test_route_matches_system_volume_domain: PASS")

def test_route_matches_system_power_domain() -> None:
    assert te.route("put the computer to sleep") == "system_power"
    assert te.route("restart my computer") == "system_power"
    assert te.route("shut down my computer") == "system_power"
    assert te.route("reboot the pc") == "system_power"
    print("test_route_matches_system_power_domain: PASS")

def test_route_matches_system_shortcut_domain() -> None:
    assert te.route("check bluetooth devices") == "system_shortcut"
    assert te.route("open display settings") == "system_shortcut"
    assert te.route("what's my battery status") == "system_shortcut"
    print("test_route_matches_system_shortcut_domain: PASS")

def test_route_application_domains_unaffected_by_system_additions() -> None:
    assert te.route("play a Kafle song on YouTube") == "youtube"
    assert te.route("open google.com and search for restaurants") == "browser"
    print("test_route_application_domains_unaffected_by_system_additions: PASS")

def test_route_ambiguous_objective_is_not_misrouted_to_system() -> None:
    assert te.route("what's 2 plus 2") is None
    assert te.route("tell me a joke") is None
    print("test_route_ambiguous_objective_is_not_misrouted_to_system: PASS")


# ── Family ───────────────────────────────────────────────────────────

def test_all_three_system_domains_are_family_system() -> None:
    assert te.family_of("system_volume") == te.FAMILY_SYSTEM
    assert te.family_of("system_power") == te.FAMILY_SYSTEM
    assert te.family_of("system_shortcut") == te.FAMILY_SYSTEM
    print("test_all_three_system_domains_are_family_system: PASS")


# ── Recovery: family-scoping actually enforced, not just untested ──────

def test_system_to_system_recovery_is_permitted_when_explicitly_configured() -> None:
    # Fabricated fixture (mirrors the cross-family fixture technique from
    # the Phase 2.5 correction) — proves the MECHANISM allows a same-
    # family SYSTEM hop; this is not a claim that a real production
    # system_volume->system_shortcut chain exists (it doesn't — see the
    # final report on why no natural SYSTEM recovery pair was found).
    fake_chain = dict(te._RECOVERY_CHAIN)
    fake_chain["system_volume"] = "system_shortcut"
    m_volume = MagicMock(return_value="[INCONCLUSIVE] volume state unclear.")
    m_shortcut = MagicMock(return_value="[VERIFIED_SUCCESS] recovered via a different system method.")
    with patch.object(te, "_RECOVERY_CHAIN", fake_chain), \
         _handlers(system_volume=m_volume, system_shortcut=m_shortcut):
        result = _task(objective="set my volume to 40 percent")
    m_shortcut.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_system_to_system_recovery_is_permitted_when_explicitly_configured: PASS")

def test_system_to_application_recovery_is_rejected() -> None:
    fake_chain = dict(te._RECOVERY_CHAIN)
    fake_chain["system_volume"] = "browser"  # deliberately cross-family
    m_volume = MagicMock(return_value="[INCONCLUSIVE] volume state unclear.")
    m_browser = MagicMock(return_value="[VERIFIED_SUCCESS] should never be reached")
    with patch.object(te, "_RECOVERY_CHAIN", fake_chain), \
         _handlers(system_volume=m_volume, browser=m_browser):
        result = _task(objective="set my volume to 40 percent")
    m_browser.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_system_to_application_recovery_is_rejected: PASS")

def test_application_to_system_recovery_is_rejected() -> None:
    fake_chain = dict(te._RECOVERY_CHAIN)
    fake_chain["youtube"] = "system_volume"  # deliberately cross-family
    m_youtube = MagicMock(return_value="[INCONCLUSIVE] unsure on youtube.")
    m_volume = MagicMock(return_value="[VERIFIED_SUCCESS] should never be reached")
    with patch.object(te, "_RECOVERY_CHAIN", fake_chain), \
         _handlers(youtube=m_youtube, system_volume=m_volume):
        result = _task(objective="play a Kafle song on YouTube")
    m_volume.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_application_to_system_recovery_is_rejected: PASS")

def test_no_real_recovery_chain_entry_exists_for_any_system_domain() -> None:
    # Honest regression guard matching the final report: no NATURAL
    # SYSTEM recovery pair was found in this phase, so none should be
    # silently present in the real (non-fixture) chain.
    for domain in ("system_volume", "system_power", "system_shortcut"):
        assert domain not in te._RECOVERY_CHAIN
    print("test_no_real_recovery_chain_entry_exists_for_any_system_domain: PASS")


# ── Execution: calls the EXISTING modules, never duplicates them ───────

def test_run_system_volume_calls_computer_settings_with_extracted_value() -> None:
    with patch.object(te, "computer_settings", return_value="[VERIFIED_SUCCESS] volume is now 40%.") as m_cs:
        result = te._run_system_volume("set my volume to 40 percent")
    m_cs.assert_called_once_with(parameters={"action": "volume_set", "value": "40"})
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_run_system_volume_calls_computer_settings_with_extracted_value: PASS")

def test_run_system_volume_defaults_to_50_when_no_number_given() -> None:
    with patch.object(te, "computer_settings", return_value="[VERIFIED_SUCCESS] volume is now 50%.") as m_cs:
        te._run_system_volume("turn the volume down")
    m_cs.assert_called_once_with(parameters={"action": "volume_set", "value": "50"})
    print("test_run_system_volume_defaults_to_50_when_no_number_given: PASS")

def test_run_system_power_routes_sleep_restart_shutdown_correctly() -> None:
    with patch.object(te, "computer_settings", return_value="ok") as m_cs:
        te._run_system_power("put the computer to sleep", confirmed=False)
    assert m_cs.call_args.kwargs["parameters"]["action"] == "sleep"

    with patch.object(te, "computer_settings", return_value="ok") as m_cs:
        te._run_system_power("restart my computer", confirmed=True)
    assert m_cs.call_args.kwargs["parameters"]["action"] == "restart"

    with patch.object(te, "computer_settings", return_value="ok") as m_cs:
        te._run_system_power("shut down my computer", confirmed=True)
    assert m_cs.call_args.kwargs["parameters"]["action"] == "shutdown"
    print("test_run_system_power_routes_sleep_restart_shutdown_correctly: PASS")

def test_run_system_power_threads_confirmed_through_unchanged() -> None:
    with patch.object(te, "computer_settings", return_value="ok") as m_cs:
        te._run_system_power("restart my computer", confirmed=True)
    assert m_cs.call_args.kwargs["parameters"]["confirmed"] is True

    with patch.object(te, "computer_settings", return_value="ok") as m_cs:
        te._run_system_power("restart my computer", confirmed=False)
    assert m_cs.call_args.kwargs["parameters"]["confirmed"] is False
    print("test_run_system_power_threads_confirmed_through_unchanged: PASS")

def test_run_system_shortcut_reuses_system_shortcuts_resolver_directly() -> None:
    # Proves reuse, not reimplementation: the RAW objective is handed
    # straight to system_shortcuts.system_shortcut() — task_engine does
    # NOT re-parse which pane/query is meant.
    with patch.object(te.system_shortcuts, "system_shortcut",
                       return_value="[VERIFIED_SUCCESS] found 2 device(s): AirPods, Mouse.") as m_ss:
        result = te._run_system_shortcut("check bluetooth devices")
    m_ss.assert_called_once_with("check bluetooth devices")
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_run_system_shortcut_reuses_system_shortcuts_resolver_directly: PASS")


# ── Result handling: honest classification, including the core Phase 3 proof ─

def test_system_volume_verified_success() -> None:
    with _handlers(system_volume=MagicMock(return_value="[VERIFIED_SUCCESS] volume is now 40%.")):
        result = _task(objective="set my volume to 40 percent")
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_system_volume_verified_success: PASS")

def test_system_volume_verified_failure() -> None:
    with _handlers(system_volume=MagicMock(return_value="[VERIFIED_FAILURE] requested 40% but volume now reads 20%.")):
        result = _task(objective="set my volume to 40 percent")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_system_volume_verified_failure: PASS")

def test_system_shortcut_pane_open_is_honestly_inconclusive_not_fabricated_success() -> None:
    # THE core Phase 3 verification proof: system_shortcuts.open_pane()
    # returns a bare, unverifiable "Opened X settings." string by its own
    # deliberate design (no real ground truth exists to confirm the pane
    # is now visible) — Task Engine must NOT upgrade that into a
    # fabricated [VERIFIED_SUCCESS], matching Step 6's explicit rule.
    with patch.object(te.system_shortcuts, "system_shortcut", return_value="Opened Bluetooth & devices settings."):
        result = te._run_system_shortcut("open bluetooth settings")
    assert result.startswith("[INCONCLUSIVE]")
    assert "Opened Bluetooth & devices settings" in result
    print("test_system_shortcut_pane_open_is_honestly_inconclusive_not_fabricated_success: PASS")

def test_system_shortcut_real_failure_propagates() -> None:
    with patch.object(te.system_shortcuts, "system_shortcut", return_value="Could not open Bluetooth & devices settings: access denied"):
        result = te._run_system_shortcut("open bluetooth settings")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_system_shortcut_real_failure_propagates: PASS")

def test_system_shortcut_query_envelope_passes_through_unchanged() -> None:
    tagged = "[VERIFIED_SUCCESS] found 2 device(s): AirPods, Mouse."
    with patch.object(te.system_shortcuts, "system_shortcut", return_value=tagged):
        result = te._run_system_shortcut("check bluetooth devices")
    assert result == tagged
    print("test_system_shortcut_query_envelope_passes_through_unchanged: PASS")

def test_system_power_confirmation_required_is_terminal_no_recovery_attempted() -> None:
    m_power = MagicMock(return_value="[CONFIRMATION_REQUIRED] this will shutdown the computer.")
    with _handlers(system_power=m_power):
        result = _task(objective="shut down my computer")
    m_power.assert_called_once()  # exactly once — no retry, no recovery hop
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_system_power_confirmation_required_is_terminal_no_recovery_attempted: PASS")

def test_blocked_status_is_terminal_no_recovery_attempted() -> None:
    fake_chain = dict(te._RECOVERY_CHAIN)
    fake_chain["system_shortcut"] = "system_volume"  # same-family, would normally be eligible
    m_shortcut = MagicMock(return_value="[BLOCKED] this action is blocked by policy.")
    m_volume = MagicMock(return_value="[VERIFIED_SUCCESS] should never be reached")
    with patch.object(te, "_RECOVERY_CHAIN", fake_chain), \
         _handlers(system_shortcut=m_shortcut, system_volume=m_volume):
        result = _task(objective="check bluetooth devices")
    m_volume.assert_not_called()
    assert result.startswith("[BLOCKED]")
    print("test_blocked_status_is_terminal_no_recovery_attempted: PASS")


if __name__ == "__main__":
    test_route_matches_system_volume_domain()
    test_route_matches_system_power_domain()
    test_route_matches_system_shortcut_domain()
    test_route_application_domains_unaffected_by_system_additions()
    test_route_ambiguous_objective_is_not_misrouted_to_system()
    test_all_three_system_domains_are_family_system()
    test_system_to_system_recovery_is_permitted_when_explicitly_configured()
    test_system_to_application_recovery_is_rejected()
    test_application_to_system_recovery_is_rejected()
    test_no_real_recovery_chain_entry_exists_for_any_system_domain()
    test_run_system_volume_calls_computer_settings_with_extracted_value()
    test_run_system_volume_defaults_to_50_when_no_number_given()
    test_run_system_power_routes_sleep_restart_shutdown_correctly()
    test_run_system_power_threads_confirmed_through_unchanged()
    test_run_system_shortcut_reuses_system_shortcuts_resolver_directly()
    test_system_volume_verified_success()
    test_system_volume_verified_failure()
    test_system_shortcut_pane_open_is_honestly_inconclusive_not_fabricated_success()
    test_system_shortcut_real_failure_propagates()
    test_system_shortcut_query_envelope_passes_through_unchanged()
    test_system_power_confirmation_required_is_terminal_no_recovery_attempted()
    test_blocked_status_is_terminal_no_recovery_attempted()
    print("\nAll task_engine_system tests passed.")
