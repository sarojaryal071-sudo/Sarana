"""
tests/test_send_message.py — J2 (Universal Actions) of the locked JARVIS
roadmap: send_message() now returns a real Result Envelope, and — a
genuine, pre-existing safety gap found and fixed during this migration —
is now gated by the SAME centralized is_consequential()/is_confirmed()
classifier computer_settings.py/accomplish() already use.
result_envelope.py's own _CONSEQUENTIAL_GOAL_PATTERNS has always listed
"send"; send_message() simply never checked it before this stage.

Per this project's own established convention: no test here sends a
real message — _resolve_platform()'s underlying per-platform automation
functions are always mocked (via patching _resolve_platform itself, the
plain module-level name send_message() actually calls). What's verified
is that confirmation is required before anything runs, and that a
"successful" send is reported as INCONCLUSIVE (never fabricated
VERIFIED_SUCCESS — there is no delivery read-back for any of these
platforms).

Run with:
    .venv/Scripts/python.exe -m tests.test_send_message
"""
from unittest.mock import patch, MagicMock

import actions.send_message as sm


_ARGS = {"receiver": "Alice", "message_text": "hello", "platform": "whatsapp"}


# ── Safety: the newly-closed confirmation gap ───────────────────────────

def test_send_message_without_confirmation_is_confirmation_required_and_sends_nothing() -> None:
    m_resolve = MagicMock()
    with patch.object(sm, "_resolve_platform", return_value=m_resolve):
        result = sm.send_message(parameters=dict(_ARGS))
    m_resolve.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_send_message_without_confirmation_is_confirmation_required_and_sends_nothing: PASS")


def test_send_message_with_confirmed_true_actually_sends() -> None:
    m_handler = MagicMock(return_value="Message sent to Alice via whatsapp.")
    with patch.object(sm, "_resolve_platform", return_value=m_handler):
        result = sm.send_message(parameters=dict(_ARGS, confirmed=True))
    m_handler.assert_called_once_with("Alice", "hello")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_send_message_with_confirmed_true_actually_sends: PASS")


def test_send_message_accepts_the_legacy_truthy_string_confirmed_convention() -> None:
    # is_confirmed() already accepts "yes"/"true"/"1"/"confirm" as well as
    # a real bool (see result_envelope.py) — this function must not
    # reimplement that reading, just reuse it.
    m_handler = MagicMock(return_value="Message sent to Alice via whatsapp.")
    with patch.object(sm, "_resolve_platform", return_value=m_handler):
        result = sm.send_message(parameters=dict(_ARGS, confirmed="yes"))
    m_handler.assert_called_once()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_send_message_accepts_the_legacy_truthy_string_confirmed_convention: PASS")


# ── Honest verification: "sent" is never fabricated success ────────────

def test_a_normal_send_is_inconclusive_never_verified_success() -> None:
    m_handler = MagicMock(return_value="Message sent to Alice via whatsapp.")
    with patch.object(sm, "_resolve_platform", return_value=m_handler):
        result = sm.send_message(parameters=dict(_ARGS, confirmed=True))
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_a_normal_send_is_inconclusive_never_verified_success: PASS")


def test_a_real_failure_is_verified_failure() -> None:
    m_handler = MagicMock(return_value="Could not open WhatsApp.")
    with patch.object(sm, "_resolve_platform", return_value=m_handler):
        result = sm.send_message(parameters=dict(_ARGS, confirmed=True))
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_a_real_failure_is_verified_failure: PASS")


def test_an_exception_during_send_is_verified_failure() -> None:
    with patch.object(sm, "_resolve_platform", side_effect=RuntimeError("boom")):
        result = sm.send_message(parameters=dict(_ARGS, confirmed=True))
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "boom" in result
    print("test_an_exception_during_send_is_verified_failure: PASS")


# ── Input validation: honest, not fabricated, and confirmation-gate-free ─

def test_missing_receiver_is_inconclusive_and_calls_nothing() -> None:
    m_resolve = MagicMock()
    with patch.object(sm, "_resolve_platform", m_resolve):
        result = sm.send_message(parameters={"receiver": "", "message_text": "hi", "platform": "whatsapp", "confirmed": True})
    m_resolve.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_missing_receiver_is_inconclusive_and_calls_nothing: PASS")


def test_missing_message_text_is_inconclusive_and_calls_nothing() -> None:
    m_resolve = MagicMock()
    with patch.object(sm, "_resolve_platform", m_resolve):
        result = sm.send_message(parameters={"receiver": "Alice", "message_text": "", "platform": "whatsapp", "confirmed": True})
    m_resolve.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_missing_message_text_is_inconclusive_and_calls_nothing: PASS")


# ── Classifier unit tests ───────────────────────────────────────────────

def test_classify_send_result_recognizes_a_could_not_failure() -> None:
    assert sm._classify_send_result("Could not open Telegram.") == sm._envelope.STATUS_VERIFIED_FAILURE
    print("test_classify_send_result_recognizes_a_could_not_failure: PASS")


def test_classify_send_result_defaults_a_sent_looking_string_to_inconclusive() -> None:
    assert sm._classify_send_result("Message sent to Bob via Discord.") == sm._envelope.STATUS_INCONCLUSIVE
    print("test_classify_send_result_defaults_a_sent_looking_string_to_inconclusive: PASS")


def _run() -> None:
    test_send_message_without_confirmation_is_confirmation_required_and_sends_nothing()
    test_send_message_with_confirmed_true_actually_sends()
    test_send_message_accepts_the_legacy_truthy_string_confirmed_convention()
    test_a_normal_send_is_inconclusive_never_verified_success()
    test_a_real_failure_is_verified_failure()
    test_an_exception_during_send_is_verified_failure()
    test_missing_receiver_is_inconclusive_and_calls_nothing()
    test_missing_message_text_is_inconclusive_and_calls_nothing()
    test_classify_send_result_recognizes_a_could_not_failure()
    test_classify_send_result_defaults_a_sent_looking_string_to_inconclusive()
    print("\nAll send_message tests passed.")


if __name__ == "__main__":
    _run()
