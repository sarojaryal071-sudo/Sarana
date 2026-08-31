"""
tests/test_computer_settings.py — actions/computer_settings.py's Tier 1/2
deterministic native-capability additions (sleep, Bluetooth radio,
clipboard get/set) and the Result Envelope wrapping now applied to every
verifiable action (volume_set, toggle_wifi, sleep, bluetooth_on/off,
clipboard_get/set, restart, shutdown), sharing the SAME centralized risk
classifier accomplish() uses (actions/result_envelope.py).

Mocks the underlying OS calls (pycaw/subprocess-backed functions) where
real hardware/state makes an integration test inappropriate — per this
project's own testing principle, mocks target the actual readback
mechanism (get_volume/_wifi_state/_bluetooth_radio_state/sleep_computer/
bluetooth_radio_set), never the dispatch function's own final return
value, so "no exception" can never silently look like VERIFIED_SUCCESS
in these tests either. clipboard_get/clipboard_set are tested for REAL
(via pyperclip) since a clipboard round-trip is safe and non-destructive
— already live-verified manually against this machine during
implementation.

Run with:
    .venv/Scripts/python.exe -m tests.test_computer_settings
"""
from unittest.mock import patch, MagicMock

import actions.computer_settings as cs


def _cs(**params):
    return cs.computer_settings(parameters=params)


# ── confirmation gate (shared classifier — result_envelope.py) ─────────

def test_shutdown_without_confirmation_is_blocked_and_never_calls_the_real_command() -> None:
    with patch.object(cs, "shutdown_computer") as m:
        result = _cs(action="shutdown")
    assert result.startswith("[CONFIRMATION_REQUIRED]"), result
    m.assert_not_called()
    print("test_shutdown_without_confirmation_is_blocked_and_never_calls_the_real_command: PASS")


def test_shutdown_with_confirmed_true_proceeds() -> None:
    # NEVER actually shuts the machine down — the dispatch branch looks
    # the function up via ACTION_MAP[action] (captured at dict-definition
    # time), so the entry itself must be patched, not the bare module
    # attribute (patch.object(cs, "shutdown_computer") wouldn't affect
    # what's already stored in the dict).
    m = MagicMock()
    with patch.dict(cs.ACTION_MAP, {"shutdown": m}):
        result = _cs(action="shutdown", confirmed=True)
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    m.assert_called_once()
    print("test_shutdown_with_confirmed_true_proceeds: PASS")


def test_restart_accepts_the_legacy_confirmed_yes_string() -> None:
    # Backward compatibility: this exact string convention was already
    # shipped/tested before result_envelope.py existed. NEVER actually
    # restarts the machine — see the ACTION_MAP-patching note above.
    m = MagicMock()
    with patch.dict(cs.ACTION_MAP, {"restart": m}):
        result = _cs(action="restart", confirmed="yes")
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    m.assert_called_once()
    print("test_restart_accepts_the_legacy_confirmed_yes_string: PASS")


def test_shutdown_failure_is_reported_honestly() -> None:
    m = MagicMock(side_effect=RuntimeError("boom"))
    with patch.dict(cs.ACTION_MAP, {"shutdown": m}):
        result = _cs(action="shutdown", confirmed=True)
    assert result.startswith("[VERIFIED_FAILURE]"), result
    print("test_shutdown_failure_is_reported_honestly: PASS")


# ── sleep: no confirmation required, honest "accepted" wording ─────────

def test_sleep_does_not_require_confirmation() -> None:
    with patch.object(cs, "sleep_computer", return_value=True):
        result = _cs(action="sleep")
    assert not result.startswith("[CONFIRMATION_REQUIRED]"), result
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    print("test_sleep_does_not_require_confirmation: PASS")


def test_sleep_success_wording_never_claims_the_machine_is_asleep() -> None:
    # Only "the OS accepted the request" is an honest claim — see
    # sleep_computer()'s own docstring on why "it IS asleep" can't be
    # confirmed from inside the process that just suspended itself.
    with patch.object(cs, "sleep_computer", return_value=True):
        result = _cs(action="sleep")
    assert "asleep" not in result.lower()
    assert "accepted" in result.lower()
    print("test_sleep_success_wording_never_claims_the_machine_is_asleep: PASS")


def test_sleep_rejected_by_os_is_verified_failure() -> None:
    with patch.object(cs, "sleep_computer", return_value=False):
        result = _cs(action="sleep")
    assert result.startswith("[VERIFIED_FAILURE]"), result
    print("test_sleep_rejected_by_os_is_verified_failure: PASS")


# ── volume: ground-truth readback ───────────────────────────────────────

def test_volume_set_verified_success_when_readback_matches() -> None:
    with patch.object(cs, "volume_set"), patch.object(cs, "get_volume", return_value=42):
        result = _cs(action="volume_set", value=42)
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    assert "42" in result
    print("test_volume_set_verified_success_when_readback_matches: PASS")


def test_volume_set_verified_failure_when_readback_mismatches() -> None:
    with patch.object(cs, "volume_set"), patch.object(cs, "get_volume", return_value=10):
        result = _cs(action="volume_set", value=80)
    assert result.startswith("[VERIFIED_FAILURE]"), result
    print("test_volume_set_verified_failure_when_readback_mismatches: PASS")


def test_volume_set_inconclusive_when_readback_unavailable() -> None:
    with patch.object(cs, "volume_set"), patch.object(cs, "get_volume", return_value=None):
        result = _cs(action="volume_set", value=50)
    assert result.startswith("[INCONCLUSIVE]"), result
    print("test_volume_set_inconclusive_when_readback_unavailable: PASS")


# ── Wi-Fi: before/after toggle readback ─────────────────────────────────

def test_toggle_wifi_verified_success_when_state_flips() -> None:
    with patch.object(cs, "toggle_wifi"), \
         patch.object(cs, "_wifi_state", side_effect=[False, True]):
        result = _cs(action="toggle_wifi")
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    print("test_toggle_wifi_verified_success_when_state_flips: PASS")


def test_toggle_wifi_verified_failure_when_state_unchanged() -> None:
    with patch.object(cs, "toggle_wifi"), \
         patch.object(cs, "_wifi_state", side_effect=[True, True]):
        result = _cs(action="toggle_wifi")
    assert result.startswith("[VERIFIED_FAILURE]"), result
    print("test_toggle_wifi_verified_failure_when_state_unchanged: PASS")


# ── Bluetooth radio: real ground-truth readback, may be None (no admin) ─

def test_bluetooth_on_verified_success() -> None:
    with patch.object(cs, "bluetooth_radio_set", return_value=True):
        result = _cs(action="bluetooth_on")
    assert result.startswith("[VERIFIED_SUCCESS]"), result
    print("test_bluetooth_on_verified_success: PASS")


def test_bluetooth_off_verified_failure_when_still_on() -> None:
    with patch.object(cs, "bluetooth_radio_set", return_value=True):
        result = _cs(action="bluetooth_off")
    assert result.startswith("[VERIFIED_FAILURE]"), result
    print("test_bluetooth_off_verified_failure_when_still_on: PASS")


def test_bluetooth_toggle_inconclusive_when_readback_unavailable() -> None:
    # e.g. Enable-PnpDevice/Disable-PnpDevice silently failing without
    # administrator privileges — must be reported honestly, never assumed.
    with patch.object(cs, "bluetooth_radio_set", return_value=None):
        result = _cs(action="bluetooth_on")
    assert result.startswith("[INCONCLUSIVE]"), result
    assert "accomplish" in result.lower()  # points at the Settings-UI fallback
    print("test_bluetooth_toggle_inconclusive_when_readback_unavailable: PASS")


# ── clipboard: real, safe round trip (no mocking needed) ───────────────

def test_clipboard_set_then_get_real_round_trip() -> None:
    if not cs._PYPERCLIP:
        print("test_clipboard_set_then_get_real_round_trip: SKIPPED (pyperclip unavailable)")
        return
    marker = "jarvis-test-computer-settings-roundtrip"
    set_result = _cs(action="clipboard_set", value=marker)
    assert set_result.startswith("[VERIFIED_SUCCESS]"), set_result
    get_result = _cs(action="clipboard_get")
    assert get_result.startswith("[VERIFIED_SUCCESS]"), get_result
    assert marker in get_result
    print("test_clipboard_set_then_get_real_round_trip: PASS")


def test_clipboard_set_with_no_text_is_inconclusive_not_a_crash() -> None:
    result = _cs(action="clipboard_set", value="")
    assert result.startswith("[INCONCLUSIVE]"), result
    print("test_clipboard_set_with_no_text_is_inconclusive_not_a_crash: PASS")


# ── unverifiable fire-and-forget actions keep their existing behavior ──

def test_unverified_action_keeps_bare_done_string_unchanged() -> None:
    # e.g. window snapping/zoom/tab navigation — deliberately NOT
    # rewrapped (see computer_settings.py's own comment on this scoping
    # decision) — must keep exactly their pre-existing behavior. Patched
    # via ACTION_MAP (dict-definition-time binding — see the shutdown/
    # restart tests' own note above), NEVER the bare module attribute —
    # that mistake was caught live here: an earlier version of this test
    # used patch.object(cs, "minimize_window") and actually minimized the
    # real active window, since the generic ACTION_MAP.get(action)()
    # fallback path never re-reads the module attribute.
    m = MagicMock()
    with patch.dict(cs.ACTION_MAP, {"minimize": m}):
        result = _cs(action="minimize")
    m.assert_called_once()
    assert result == "Done: minimize."
    assert "[" not in result
    print("test_unverified_action_keeps_bare_done_string_unchanged: PASS")


if __name__ == "__main__":
    test_shutdown_without_confirmation_is_blocked_and_never_calls_the_real_command()
    test_shutdown_with_confirmed_true_proceeds()
    test_restart_accepts_the_legacy_confirmed_yes_string()
    test_shutdown_failure_is_reported_honestly()
    test_sleep_does_not_require_confirmation()
    test_sleep_success_wording_never_claims_the_machine_is_asleep()
    test_sleep_rejected_by_os_is_verified_failure()
    test_volume_set_verified_success_when_readback_matches()
    test_volume_set_verified_failure_when_readback_mismatches()
    test_volume_set_inconclusive_when_readback_unavailable()
    test_toggle_wifi_verified_success_when_state_flips()
    test_toggle_wifi_verified_failure_when_state_unchanged()
    test_bluetooth_on_verified_success()
    test_bluetooth_off_verified_failure_when_still_on()
    test_bluetooth_toggle_inconclusive_when_readback_unavailable()
    test_clipboard_set_then_get_real_round_trip()
    test_clipboard_set_with_no_text_is_inconclusive_not_a_crash()
    test_unverified_action_keeps_bare_done_string_unchanged()
    print("\nAll computer_settings tests passed.")
