"""
tests/test_task_engine_j5.py — J5 (Recovery) of the JARVIS execution-
architecture mission.

Inspection finding, stated up front because it drives this whole file's
shape: J5's engine-level mechanism was ALREADY implemented before this
stage started (Phase 0-2's original recovery chain, Phase 3-4's family-
scoping, J4's CANCELLED-terminal fix) — tiered fallback (the chain dict
is re-looked-up on whatever domain _execute_step()'s loop currently
holds, so a>b>c already works mechanically), bounded attempts, no-blind-
same-domain-retry, family-scoped hops, and BLOCKED/CONFIRMATION_REQUIRED/
CANCELLED as hard recovery-ineligible terminals were all already real
and already covered by tests/test_task_engine.py's own recovery tests
(kept there, not duplicated here) plus J4's cancellation-recovery test.

This file covers exactly the J5-specific proof points that did NOT
already exist anywhere: cycle-defense under a hypothetical misconfigured
chain, family-scoping's ALLOW side for a second family (SYSTEM, not just
APPLICATION), the original objective actually being byte-identical
across every attempt within one PlanStep, a fallback that ALSO can't
verify (never upgraded to success), the recovery path being reconstructible
from the existing Task/Step model (no new history mechanism), and
confirmation/block never being bypassable via a recovery hop even when
one is temporarily configured to exist.

Per this project's own established convention: every underlying
capability is ALWAYS mocked here; SYSTEM->SYSTEM and the cyclic-chain
scenarios use a TEMPORARY patch.dict on te._RECOVERY_CHAIN (same
technique test_task_engine.py's own test_recovery_cannot_cross_families
already uses for its negative case) — proving the ENGINE MECHANISM
generalizes, never asserting a new permanent production recovery pair
(no such pair exists among today's real SYSTEM capabilities — see
_RECOVERY_CHAIN's own comment on why none is invented).

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_j5
"""
from unittest.mock import MagicMock, patch

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    return patch.dict(te._HANDLERS, overrides)


# ── E. Bounded recovery: a misconfigured cycle can't loop indefinitely ─

def test_e_a_cyclic_recovery_chain_terminates_instead_of_bouncing_forever() -> None:
    # A deliberately fabricated pathological config ({"a": "b", "b": "a"})
    # — proves the ENGINE (the `tried` list, checked before every hop) is
    # what prevents an infinite bounce, not just "the real chain happens
    # to be acyclic today."
    call_count = {"a": 0, "b": 0}

    def domain_a(objective, confirmed=False, context=None):
        call_count["a"] += 1
        return "[INCONCLUSIVE] a is unsure."

    def domain_b(objective, confirmed=False, context=None):
        call_count["b"] += 1
        return "[INCONCLUSIVE] b is unsure."

    fake_domains = [
        {"name": "a", "family": te.FAMILY_SYSTEM, "keywords": ["fakedomainaaa"]},
        {"name": "b", "family": te.FAMILY_SYSTEM, "keywords": ["fakedomainbbb"]},
    ]
    with patch.object(te, "_DOMAINS", fake_domains), \
         patch.object(te, "_RECOVERY_CHAIN", {"a": "b", "b": "a"}), \
         _handlers(a=domain_a, b=domain_b):
        result = _task(objective="fakedomainaaa")
    # Each domain is attempted AT MOST ONCE — never bounced back and forth.
    assert call_count["a"] == 1
    assert call_count["b"] == 1
    assert result.startswith("[INCONCLUSIVE]")
    print("test_e_a_cyclic_recovery_chain_terminates_instead_of_bouncing_forever: PASS")


# ── F. Family enforcement: SYSTEM->SYSTEM allowed when configured, ─────
#      cross-family rejected regardless (the reject side is already
#      proven in test_task_engine.py's test_recovery_cannot_cross_families;
#      this proves the ALLOW side for a family OTHER than APPLICATION).

def test_f_same_family_recovery_hop_is_permitted_when_configured_for_system() -> None:
    assert te.family_of("system_volume") == te.FAMILY_SYSTEM
    assert te.family_of("system_power") == te.FAMILY_SYSTEM
    m_power = MagicMock(return_value="[VERIFIED_SUCCESS] Sleeping now.")
    with patch.object(te, "_RECOVERY_CHAIN", {"system_volume": "system_power"}), \
         _handlers(system_volume=MagicMock(return_value="[INCONCLUSIVE] volume unsure."),
                   system_power=m_power):
        result = _task(objective="set my volume to 40 percent")
    m_power.assert_called_once()   # the SAME-family hop was actually taken
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_f_same_family_recovery_hop_is_permitted_when_configured_for_system: PASS")


def test_f_cross_family_recovery_hop_is_rejected_even_when_configured() -> None:
    # Mirrors test_task_engine.py's own test_recovery_cannot_cross_families,
    # kept here too so this file stands on its own for J5's own explicit
    # "SYSTEM -> APPLICATION is rejected" requirement.
    m_office = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"system_volume": "office"}), \
         _handlers(system_volume=MagicMock(return_value="[INCONCLUSIVE] volume unsure."),
                   office=m_office):
        result = _task(objective="set my volume to 40 percent")
    m_office.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_f_cross_family_recovery_hop_is_rejected_even_when_configured: PASS")


# ── G. Ordinary cross-family task SEQUENCING is unaffected (not recovery) ─

def test_g_cross_family_sequencing_is_unaffected_by_recovery_scoping() -> None:
    m_sys = MagicMock(return_value="[VERIFIED_SUCCESS] Percent: 55, PluggedIn: True.")
    m_office = MagicMock(return_value="[VERIFIED_SUCCESS] A1 is now 55.")
    with _handlers(system_shortcut=m_sys, office=m_office):
        result = _task(objectives=["check my battery percentage", "put that percentage into cell A1"])
    m_sys.assert_called_once()
    m_office.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_g_cross_family_sequencing_is_unaffected_by_recovery_scoping: PASS")


# ── K. The original objective is preserved, byte-identical, across ─────
#      every attempt within one PlanStep (never rewritten into a
#      different request the way a naive "search instead" fallback would).

def test_k_fallback_receives_the_exact_same_objective_text_as_the_primary() -> None:
    objective = "play some obscure Kafle remix on YouTube"
    m_yt = MagicMock(return_value="[INCONCLUSIVE] unsure on youtube.")
    m_br = MagicMock(return_value="[VERIFIED_SUCCESS] Opened: https://youtube.com/results")
    with _handlers(youtube=m_yt, browser=m_br):
        _task(objective=objective)
    yt_call_objective = m_yt.call_args.args[0]
    br_call_objective = m_br.call_args.args[0]
    assert yt_call_objective == objective
    assert br_call_objective == objective   # NOT rewritten into a different request
    print("test_k_fallback_receives_the_exact_same_objective_text_as_the_primary: PASS")


# ── L. Verification remains mandatory: a fallback that ALSO can't ──────
#      verify must not be upgraded to success.

def test_l_fallback_that_is_also_inconclusive_never_becomes_success() -> None:
    m_yt = MagicMock(return_value="[INCONCLUSIVE] unsure on youtube.")
    m_br = MagicMock(return_value="[INCONCLUSIVE] browser also unsure.")
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play some Kafle video")
    m_br.assert_called_once()
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_l_fallback_that_is_also_inconclusive_never_becomes_success: PASS")


# ── Recovery history is reconstructible from the EXISTING Task/Step ────
#    model — no new history mechanism was built (section 12's own
#    "runtime task state is sufficient" instruction).

def test_recovery_path_is_reconstructible_from_the_existing_step_history() -> None:
    task = te.Task(objectives=["play some obscure Kafle remix on YouTube"])
    task.plan, _ = te.build_plan(task.objectives)
    task.state = te.TASK_EXECUTING
    with _handlers(
        youtube=MagicMock(return_value="[INCONCLUSIVE] unsure on youtube."),
        browser=MagicMock(return_value="[VERIFIED_SUCCESS] Opened: https://youtube.com/results"),
    ):
        te._execute_step(task, 0, task.plan[0], confirmed=False)
    # The full attempt sequence for this ONE objective is inspectable
    # directly off task.steps — no separate recovery-history structure.
    domains_tried = [s.domain for s in task.steps if s.plan_index == 0]
    statuses = [s.status for s in task.steps if s.plan_index == 0]
    assert domains_tried == ["youtube", "browser"]
    assert statuses == [te._envelope.STATUS_INCONCLUSIVE, te._envelope.STATUS_VERIFIED_SUCCESS]
    print("test_recovery_path_is_reconstructible_from_the_existing_step_history: PASS")


# ── Safety: recovery can never be used to bypass CONFIRMATION_REQUIRED ──
#    or BLOCKED, even if a recovery-chain entry is (hypothetically)
#    configured FROM the domain that returned them.

def test_confirmation_required_is_never_bypassed_by_a_configured_recovery_hop() -> None:
    m_power = MagicMock(return_value="[CONFIRMATION_REQUIRED] this will shut down the computer.")
    m_shortcut = MagicMock()   # would be the "fallback" if this were wrongly escalatable
    with patch.object(te, "_RECOVERY_CHAIN", {"system_power": "system_shortcut"}), \
         _handlers(system_power=m_power, system_shortcut=m_shortcut):
        result = _task(objective="shut down my computer")
    m_shortcut.assert_not_called()   # CONFIRMATION_REQUIRED returns before the chain is ever consulted
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_confirmation_required_is_never_bypassed_by_a_configured_recovery_hop: PASS")


def test_blocked_is_never_bypassed_by_a_configured_recovery_hop() -> None:
    m_yt = MagicMock(return_value="[BLOCKED] not allowed by policy.")
    m_br = MagicMock()
    with _handlers(youtube=m_yt, browser=m_br):   # youtube->browser is the REAL, existing entry
        result = _task(objective="play something on youtube")
    m_br.assert_not_called()
    assert result.startswith("[BLOCKED]")
    print("test_blocked_is_never_bypassed_by_a_configured_recovery_hop: PASS")


def test_cancelled_is_never_bypassed_by_a_configured_recovery_hop() -> None:
    m_yt = MagicMock(return_value="[CANCELLED] stopped before completion.")
    m_br = MagicMock()
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play something on youtube")
    m_br.assert_not_called()
    assert result.startswith("[CANCELLED]")
    print("test_cancelled_is_never_bypassed_by_a_configured_recovery_hop: PASS")


def _run() -> None:
    test_e_a_cyclic_recovery_chain_terminates_instead_of_bouncing_forever()
    test_f_same_family_recovery_hop_is_permitted_when_configured_for_system()
    test_f_cross_family_recovery_hop_is_rejected_even_when_configured()
    test_g_cross_family_sequencing_is_unaffected_by_recovery_scoping()
    test_k_fallback_receives_the_exact_same_objective_text_as_the_primary()
    test_l_fallback_that_is_also_inconclusive_never_becomes_success()
    test_recovery_path_is_reconstructible_from_the_existing_step_history()
    test_confirmation_required_is_never_bypassed_by_a_configured_recovery_hop()
    test_blocked_is_never_bypassed_by_a_configured_recovery_hop()
    test_cancelled_is_never_bypassed_by_a_configured_recovery_hop()
    print("\nAll task_engine_j5 tests passed.")


if __name__ == "__main__":
    _run()
