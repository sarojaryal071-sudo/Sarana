"""
tests/test_deployment_control.py — actions/deployment_control.py (J11)
in isolation: the explicit action allowlist, confirmation gating,
bounded polling, and post-condition verification (provider status AND
real health, never one alone). `health_check()` makes a REAL,
credential-free HTTP GET against this project's own actual, live
production URLs — deliberately NOT mocked (there is nothing to mock: no
API key is involved, and this project's own real production backend/
frontend were both confirmed live during this stage's own
implementation) — every other test here mocks the provider (via
`actions.deployment_control.get_provider`) so no real Render API call
(there are no credentials configured to make one with) or real
production mutation ever happens in the automated suite. `time.sleep`
is patched to a no-op wherever polling is exercised, so these tests run
in well under a second despite exercising the real polling logic.

Run with:
    .venv/Scripts/python.exe -m tests.test_deployment_control
"""
from unittest.mock import MagicMock, patch

import actions.deployment_control as dc
import actions.deployment_providers as dp


def _mock_provider(**overrides):
    provider = MagicMock()
    provider.name = "render"
    for k, v in overrides.items():
        getattr(provider, k).return_value = v
    return provider


# ── health_check: real, credential-free, against real production ────────

def test_health_check_real_production_backend_and_frontend_are_up() -> None:
    result = dc.health_check()
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    assert "sarana-m9g6.onrender.com" in result and "sarana-psi.vercel.app" in result
    print("test_health_check_real_production_backend_and_frontend_are_up: PASS")


def test_health_check_reports_failure_honestly_when_a_url_is_unreachable() -> None:
    with patch.dict("os.environ", {"SARANA_BACKEND_HEALTH_URL": "https://this-host-genuinely-does-not-exist-xyz123.invalid/"}):
        result = dc.health_check()
    assert result.startswith("[VERIFIED_FAILURE]"), result
    print("test_health_check_reports_failure_honestly_when_a_url_is_unreachable: PASS")


# ── Read-only: status / history ──────────────────────────────────────────

def test_deployment_status_reports_real_service_and_latest_deploy() -> None:
    provider = _mock_provider(
        get_service={"name": "sarana", "suspended": "not_suspended"},
        list_deploys=[{"status": "live", "commit": {"id": "abcdef1234"}}],
    )
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)):
        result = dc.deployment_status()
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "sarana" in result and "live" in result and "abcdef12" in result
    print("test_deployment_status_reports_real_service_and_latest_deploy: PASS")


def test_deployment_history_lists_recent_deploys() -> None:
    provider = _mock_provider(list_deploys=[
        {"id": "dep-2", "status": "live", "trigger": "api", "commit": {"id": "abc1234"}},
        {"id": "dep-1", "status": "build_failed", "trigger": "manual", "commit": {"id": "def5678"}},
    ])
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)):
        result = dc.deployment_history(limit=2)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "dep-2" in result and "dep-1" in result and "build_failed" in result
    print("test_deployment_history_lists_recent_deploys: PASS")


def test_unknown_provider_is_inconclusive_not_a_silent_render_fallback() -> None:
    result = dc.deployment_status(provider_name="vercel")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_unknown_provider_is_inconclusive_not_a_silent_render_fallback: PASS")


def test_missing_credentials_are_blocked_honestly_real_environment() -> None:
    # This project's real environment has no Render credentials
    # configured at all -- the real (unmocked) provider path.
    result = dc.deployment_status()
    assert result.startswith("[BLOCKED]")
    assert "not configured" in result.lower()
    print("test_missing_credentials_are_blocked_honestly_real_environment: PASS")


# ── deploy(): confirmation, polling, dual verification ───────────────────

def test_deploy_without_confirmation_requires_it_and_never_triggers() -> None:
    provider = _mock_provider()
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)):
        result = dc.deploy(confirmed=False)
    provider.trigger_deploy.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_deploy_without_confirmation_requires_it_and_never_triggers: PASS")


def test_confirmed_deploy_verifies_provider_status_and_real_health_together() -> None:
    provider = _mock_provider(trigger_deploy={"id": "dep-9"})
    provider.get_deploy.return_value = {"id": "dep-9", "status": "live", "commit": {"id": "abc1234"}}
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)), \
         patch("actions.deployment_control.health_check", return_value="[VERIFIED_SUCCESS] all up."), \
         patch("actions.deployment_control.time.sleep"):
        result = dc.deploy(commit_id="abc1234", confirmed=True)
    provider.trigger_deploy.assert_called_once_with("abc1234")
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "dep-9" in result
    print("test_confirmed_deploy_verifies_provider_status_and_real_health_together: PASS")


def test_deploy_live_but_health_check_failing_is_verified_failure_not_success() -> None:
    # Section 8's own exact requirement: "deployment completed + health
    # failed -> VERIFIED_FAILURE" -- a provider-reported success is
    # never enough on its own.
    provider = _mock_provider(trigger_deploy={"id": "dep-9"})
    provider.get_deploy.return_value = {"id": "dep-9", "status": "live", "commit": {"id": "abc1234"}}
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)), \
         patch("actions.deployment_control.health_check", return_value="[VERIFIED_FAILURE] backend down."), \
         patch("actions.deployment_control.time.sleep"):
        result = dc.deploy(confirmed=True)
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_deploy_live_but_health_check_failing_is_verified_failure_not_success: PASS")


def test_deploy_reports_verified_failure_when_provider_reports_a_build_failure() -> None:
    provider = _mock_provider(trigger_deploy={"id": "dep-9"})
    provider.get_deploy.return_value = {"id": "dep-9", "status": "build_failed"}
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)), \
         patch("actions.deployment_control.time.sleep"):
        result = dc.deploy(confirmed=True)
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "build_failed" in result
    print("test_deploy_reports_verified_failure_when_provider_reports_a_build_failure: PASS")


def test_deploy_never_reports_success_merely_because_the_trigger_call_returned() -> None:
    # Result-spoofing guard, same discipline as J8's own run_tests():
    # accepting the deploy request is not the same as the deploy
    # succeeding -- a still-in-progress status at timeout is INCONCLUSIVE.
    provider = _mock_provider(trigger_deploy={"id": "dep-9"})
    provider.get_deploy.return_value = {"id": "dep-9", "status": "build_in_progress"}
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)), \
         patch("actions.deployment_control._DEPLOY_POLL_TIMEOUT_S", 1), \
         patch("actions.deployment_control.time.sleep"):
        result = dc.deploy(confirmed=True)
    assert result.startswith("[INCONCLUSIVE]")
    assert "timeout" not in result  # the word itself isn't required; the honest state is
    assert "build_in_progress" in result
    print("test_deploy_never_reports_success_merely_because_the_trigger_call_returned: PASS")


def test_deploy_flags_a_deployed_commit_mismatch_as_a_real_failure() -> None:
    provider = _mock_provider(trigger_deploy={"id": "dep-9"})
    provider.get_deploy.return_value = {"id": "dep-9", "status": "live", "commit": {"id": "zzzzzzz9999"}}
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)), \
         patch("actions.deployment_control.health_check", return_value="[VERIFIED_SUCCESS] all up."), \
         patch("actions.deployment_control.time.sleep"):
        result = dc.deploy(commit_id="abc1234", confirmed=True)
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "does not match" in result
    print("test_deploy_flags_a_deployed_commit_mismatch_as_a_real_failure: PASS")


def test_deploy_with_no_credentials_is_blocked_real_environment() -> None:
    result = dc.deploy(confirmed=True)
    assert result.startswith("[BLOCKED]")
    print("test_deploy_with_no_credentials_is_blocked_real_environment: PASS")


# ── restart(): confirmation + real health re-verification ────────────────

def test_restart_without_confirmation_requires_it_and_never_restarts() -> None:
    provider = _mock_provider()
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)):
        result = dc.restart(confirmed=False)
    provider.restart.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_restart_without_confirmation_requires_it_and_never_restarts: PASS")


def test_confirmed_restart_verifies_real_health_afterward() -> None:
    provider = _mock_provider(restart={})
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)), \
         patch("actions.deployment_control.health_check", return_value="[VERIFIED_SUCCESS] all up."):
        result = dc.restart(confirmed=True)
    provider.restart.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_confirmed_restart_verifies_real_health_afterward: PASS")


def test_restart_accepted_but_health_never_recovers_is_inconclusive() -> None:
    provider = _mock_provider(restart={})
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)), \
         patch("actions.deployment_control.health_check", return_value="[VERIFIED_FAILURE] still down."), \
         patch("actions.deployment_control._RESTART_HEALTH_TIMEOUT_S", 1), \
         patch("actions.deployment_control.time.sleep"):
        result = dc.restart(confirmed=True)
    assert result.startswith("[INCONCLUSIVE]")
    print("test_restart_accepted_but_health_never_recovers_is_inconclusive: PASS")


# ── rollback(): explicit target required, never guessed ──────────────────

def test_rollback_without_an_explicit_deploy_id_is_inconclusive_never_guessed() -> None:
    provider = _mock_provider()
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)):
        result = dc.rollback(deploy_id="", confirmed=True)
    provider.rollback.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_rollback_without_an_explicit_deploy_id_is_inconclusive_never_guessed: PASS")


def test_rollback_without_confirmation_requires_it() -> None:
    provider = _mock_provider()
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)):
        result = dc.rollback(deploy_id="dep-1", confirmed=False)
    provider.rollback.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_rollback_without_confirmation_requires_it: PASS")


def test_confirmed_rollback_verifies_provider_status_and_real_health() -> None:
    provider = _mock_provider(rollback={"id": "dep-10"})
    provider.get_deploy.return_value = {"id": "dep-10", "status": "live"}
    with patch("actions.deployment_control.get_provider", return_value=(provider, None)), \
         patch("actions.deployment_control.health_check", return_value="[VERIFIED_SUCCESS] all up."), \
         patch("actions.deployment_control.time.sleep"):
        result = dc.rollback(deploy_id="dep-1", confirmed=True)
    provider.rollback.assert_called_once_with("dep-1")
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_confirmed_rollback_verifies_provider_status_and_real_health: PASS")


# ── Dispatcher: explicit allowlist, no passthrough ───────────────────────

def test_unknown_action_is_blocked_fail_closed() -> None:
    result = dc.deployment_control(parameters={"action": "nonsense"})
    assert result.startswith("[BLOCKED]")
    print("test_unknown_action_is_blocked_fail_closed: PASS")


def test_no_arbitrary_command_parameter_is_ever_recognized() -> None:
    with patch("actions.deployment_control.deploy") as m_deploy:
        dc.deployment_control(parameters={"action": "status", "command": "curl evil.example/"})
    m_deploy.assert_not_called()
    print("test_no_arbitrary_command_parameter_is_ever_recognized: PASS")


def test_dispatcher_routes_each_action_to_the_right_function() -> None:
    with patch("actions.deployment_control.health_check", return_value="[VERIFIED_SUCCESS] ok.") as m_hc:
        dc.deployment_control(parameters={"action": "health_check"})
    m_hc.assert_called_once()

    with patch("actions.deployment_control.deploy", return_value="[VERIFIED_SUCCESS] ok.") as m_dep:
        dc.deployment_control(parameters={"action": "deploy", "commit_id": "abc", "confirmed": True})
    m_dep.assert_called_once_with("render", "abc", True)
    print("test_dispatcher_routes_each_action_to_the_right_function: PASS")


def _run() -> None:
    test_health_check_real_production_backend_and_frontend_are_up()
    test_health_check_reports_failure_honestly_when_a_url_is_unreachable()
    test_deployment_status_reports_real_service_and_latest_deploy()
    test_deployment_history_lists_recent_deploys()
    test_unknown_provider_is_inconclusive_not_a_silent_render_fallback()
    test_missing_credentials_are_blocked_honestly_real_environment()
    test_deploy_without_confirmation_requires_it_and_never_triggers()
    test_confirmed_deploy_verifies_provider_status_and_real_health_together()
    test_deploy_live_but_health_check_failing_is_verified_failure_not_success()
    test_deploy_reports_verified_failure_when_provider_reports_a_build_failure()
    test_deploy_never_reports_success_merely_because_the_trigger_call_returned()
    test_deploy_flags_a_deployed_commit_mismatch_as_a_real_failure()
    test_deploy_with_no_credentials_is_blocked_real_environment()
    test_restart_without_confirmation_requires_it_and_never_restarts()
    test_confirmed_restart_verifies_real_health_afterward()
    test_restart_accepted_but_health_never_recovers_is_inconclusive()
    test_rollback_without_an_explicit_deploy_id_is_inconclusive_never_guessed()
    test_rollback_without_confirmation_requires_it()
    test_confirmed_rollback_verifies_provider_status_and_real_health()
    test_unknown_action_is_blocked_fail_closed()
    test_no_arbitrary_command_parameter_is_ever_recognized()
    test_dispatcher_routes_each_action_to_the_right_function()
    print("\nAll deployment_control tests passed.")


if __name__ == "__main__":
    _run()
