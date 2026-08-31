"""
tests/test_result_envelope.py — actions/result_envelope.py: the ONE
canonical result shape (VERIFIED_SUCCESS/VERIFIED_FAILURE/INCONCLUSIVE/
UI_AMBIGUOUS/CONFIRMATION_REQUIRED) and the ONE centralized risk/
confirmation classifier shared by accomplish() and computer_settings().

Run with:
    .venv/Scripts/python.exe -m tests.test_result_envelope
"""
from actions import result_envelope as env


def test_verified_success_has_no_directive() -> None:
    r = env.envelope(env.STATUS_VERIFIED_SUCCESS, "the header now reads Saroj")
    assert r.startswith("[VERIFIED_SUCCESS]")
    assert "the header now reads Saroj." in r
    # No "do not"/"call"/"ask" instruction text on a genuine success.
    assert "do not" not in r.lower()
    print("test_verified_success_has_no_directive: PASS")


def test_verified_failure_tells_gemini_to_report_it_honestly() -> None:
    r = env.envelope(env.STATUS_VERIFIED_FAILURE, "volume is still 50%")
    assert r.startswith("[VERIFIED_FAILURE]")
    assert "did not work" in r.lower()
    print("test_verified_failure_tells_gemini_to_report_it_honestly: PASS")


def test_inconclusive_forbids_claiming_success() -> None:
    r = env.envelope(env.STATUS_INCONCLUSIVE, "no observable change")
    assert r.startswith("[INCONCLUSIVE]")
    assert "do not tell the user this succeeded" in r.lower()
    print("test_inconclusive_forbids_claiming_success: PASS")


def test_ui_ambiguous_forbids_guessing() -> None:
    r = env.envelope(env.STATUS_UI_AMBIGUOUS, "2 distinct elements matched")
    assert r.startswith("[UI_AMBIGUOUS]")
    assert "do not guess" in r.lower()
    print("test_ui_ambiguous_forbids_guessing: PASS")


def test_confirmation_required_forbids_executing() -> None:
    r = env.envelope(env.STATUS_CONFIRMATION_REQUIRED, "requested: shutdown")
    assert r.startswith("[CONFIRMATION_REQUIRED]")
    assert "do not perform this action yet" in r.lower()
    assert "confirmed=true" in r
    print("test_confirmation_required_forbids_executing: PASS")


def test_envelope_evidence_gets_a_trailing_period() -> None:
    r = env.envelope(env.STATUS_VERIFIED_SUCCESS, "clipboard now contains X")
    assert "clipboard now contains X." in r
    # Already-punctuated evidence isn't double-punctuated.
    r2 = env.envelope(env.STATUS_VERIFIED_SUCCESS, "already ends with a period.")
    assert "period.." not in r2
    print("test_envelope_evidence_gets_a_trailing_period: PASS")


def test_escalatable_statuses_exclude_failure_and_confirmation() -> None:
    # Only genuinely uncertain results are worth a vision look — a real
    # failure is already a known outcome, and a confirmation gate must
    # never be bypassed by "let's just look and decide".
    assert env.STATUS_INCONCLUSIVE in env.ESCALATABLE_STATUSES
    assert env.STATUS_UI_AMBIGUOUS in env.ESCALATABLE_STATUSES
    assert env.STATUS_VERIFIED_FAILURE not in env.ESCALATABLE_STATUSES
    assert env.STATUS_CONFIRMATION_REQUIRED not in env.ESCALATABLE_STATUSES
    assert env.STATUS_VERIFIED_SUCCESS not in env.ESCALATABLE_STATUSES
    print("test_escalatable_statuses_exclude_failure_and_confirmation: PASS")


# ── risk classifier ───────────────────────────────────────────────────

def test_shutdown_and_restart_are_consequential_by_action_name() -> None:
    assert env.is_consequential(action_name="shutdown") is True
    assert env.is_consequential(action_name="restart") is True
    print("test_shutdown_and_restart_are_consequential_by_action_name: PASS")


def test_sleep_is_not_consequential() -> None:
    # Deliberately excluded — reversible, nothing is lost, unlike
    # shutdown/restart which close every running app.
    assert env.is_consequential(action_name="sleep") is False
    print("test_sleep_is_not_consequential: PASS")


def test_ordinary_low_risk_goals_never_require_confirmation() -> None:
    for goal in [
        "open Word", "click the Save tab", "read the visible text",
        "search for flights", "connect my headphones", "open Downloads",
        "change the document title",
    ]:
        assert env.is_consequential(goal=goal) is False, f"false positive on: {goal!r}"
    print("test_ordinary_low_risk_goals_never_require_confirmation: PASS")


def test_consequential_goal_patterns_are_detected() -> None:
    for goal in [
        "send this message to John", "delete the file",
        "buy this on Amazon", "disconnect my headphones",
        "change my password", "factory reset the device",
    ]:
        assert env.is_consequential(goal=goal) is True, f"missed: {goal!r}"
    print("test_consequential_goal_patterns_are_detected: PASS")


def test_is_confirmed_accepts_boolean_and_legacy_string_forms() -> None:
    assert env.is_confirmed({"confirmed": True}) is True
    assert env.is_confirmed({"confirmed": "yes"}) is True
    assert env.is_confirmed({"confirmed": "true"}) is True
    assert env.is_confirmed({"confirmed": "1"}) is True
    assert env.is_confirmed({"confirmed": "confirm"}) is True
    assert env.is_confirmed({"confirmed": False}) is False
    assert env.is_confirmed({"confirmed": "no"}) is False
    assert env.is_confirmed({}) is False
    print("test_is_confirmed_accepts_boolean_and_legacy_string_forms: PASS")


if __name__ == "__main__":
    test_verified_success_has_no_directive()
    test_verified_failure_tells_gemini_to_report_it_honestly()
    test_inconclusive_forbids_claiming_success()
    test_ui_ambiguous_forbids_guessing()
    test_confirmation_required_forbids_executing()
    test_envelope_evidence_gets_a_trailing_period()
    test_escalatable_statuses_exclude_failure_and_confirmation()
    test_shutdown_and_restart_are_consequential_by_action_name()
    test_sleep_is_not_consequential()
    test_ordinary_low_risk_goals_never_require_confirmation()
    test_consequential_goal_patterns_are_detected()
    test_is_confirmed_accepts_boolean_and_legacy_string_forms()
    print("\nAll result_envelope tests passed.")
