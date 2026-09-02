"""
tests/test_app_audio_control.py — Phase 1 of the "unused capabilities"
follow-up: per-application volume/mute (pycaw's ISimpleAudioVolume, the
per-SESSION interface) and read-only playback-device listing, wired into
actions/computer_settings.py's dispatcher alongside the existing
master-volume/Bluetooth/Wi-Fi actions.

Deliberately does NOT include switching the default output device — see
computer_settings.py's own comment above list_audio_devices() for why
(no officially documented Windows API for it; the common workaround is
an undocumented COM interface whose vtable layout can't be verified
without live-testing against a real machine, so it isn't shipped blind).

Two layers, matching this project's own established convention
(test_computer_settings.py): _find_audio_session()/app_volume_set() etc.
are tested against a MOCKED pycaw.pycaw.AudioUtilities (fake session/
device objects, never a real audio session); computer_settings()'s
dispatch branches are tested against MOCKED cs.app_volume_set() etc.
directly, so a dispatch test failure can't hide behind a pycaw mock
being subtly wrong, and vice versa.

Run with:
    .venv/Scripts/python.exe -m tests.test_app_audio_control
"""
from unittest.mock import patch, MagicMock

import actions.computer_settings as cs


def _cs(**params):
    return cs.computer_settings(parameters=params)


def _fake_session(proc_name: str | None, volume: float = 0.5, muted: bool = False):
    sess = MagicMock()
    if proc_name is None:
        sess.Process = None
    else:
        sess.Process = MagicMock()
        sess.Process.name.return_value = proc_name
    sess.SimpleAudioVolume.GetMasterVolume.return_value = volume
    sess.SimpleAudioVolume.GetMute.return_value = int(muted)
    return sess


# ── _find_audio_session: matching logic ─────────────────────────────────

def test_find_audio_session_matches_case_and_exe_suffix_insensitively() -> None:
    target = _fake_session("Spotify.exe")
    other  = _fake_session("chrome.exe")
    with patch.object(cs, "_OS", "Windows"), \
         patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=[other, target]):
        found = cs._find_audio_session("spotify")
    assert found is target
    print("test_find_audio_session_matches_case_and_exe_suffix_insensitively: PASS")

def test_find_audio_session_skips_sessions_with_no_process_system_sounds() -> None:
    system_sound = _fake_session(None)
    with patch.object(cs, "_OS", "Windows"), \
         patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=[system_sound]):
        found = cs._find_audio_session("spotify")
    assert found is None
    print("test_find_audio_session_skips_sessions_with_no_process_system_sounds: PASS")

def test_find_audio_session_returns_none_when_nothing_matches() -> None:
    with patch.object(cs, "_OS", "Windows"), \
         patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=[_fake_session("chrome.exe")]):
        found = cs._find_audio_session("spotify")
    assert found is None
    print("test_find_audio_session_returns_none_when_nothing_matches: PASS")


# ── app_volume_set/get, app_mute_set/get: real readback discipline ─────

def test_app_volume_set_returns_none_when_no_matching_session() -> None:
    with patch.object(cs, "_OS", "Windows"), \
         patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=[]):
        result = cs.app_volume_set("spotify", 50)
    assert result is None
    print("test_app_volume_set_returns_none_when_no_matching_session: PASS")

def test_app_volume_set_calls_set_master_volume_as_a_0_to_1_fraction() -> None:
    sess = _fake_session("spotify.exe")
    with patch.object(cs, "_OS", "Windows"), \
         patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=[sess]):
        result = cs.app_volume_set("spotify", 70)
    assert result is True
    sess.SimpleAudioVolume.SetMasterVolume.assert_called_once_with(0.7, None)
    print("test_app_volume_set_calls_set_master_volume_as_a_0_to_1_fraction: PASS")

def test_app_mute_set_calls_set_mute_with_the_requested_state() -> None:
    sess = _fake_session("discord.exe")
    with patch.object(cs, "_OS", "Windows"), \
         patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=[sess]):
        cs.app_mute_set("discord", True)
    sess.SimpleAudioVolume.SetMute.assert_called_once_with(1, None)
    print("test_app_mute_set_calls_set_mute_with_the_requested_state: PASS")

def test_app_volume_get_reads_back_as_a_0_to_100_int() -> None:
    sess = _fake_session("spotify.exe", volume=0.42)
    with patch.object(cs, "_OS", "Windows"), \
         patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=[sess]):
        result = cs.app_volume_get("spotify")
    assert result == 42
    print("test_app_volume_get_reads_back_as_a_0_to_100_int: PASS")

def test_non_windows_functions_return_none_immediately_no_pycaw_import_attempted() -> None:
    with patch.object(cs, "_OS", "Darwin"):
        assert cs.app_volume_set("spotify", 50) is None
        assert cs.app_mute_set("spotify", True) is None
        assert cs.list_audio_devices() is None
    print("test_non_windows_functions_return_none_immediately_no_pycaw_import_attempted: PASS")


# ── list_audio_devices: read-only, marks the real default ──────────────

def test_list_audio_devices_marks_the_correct_default_and_skips_inactive() -> None:
    speakers = MagicMock()
    speakers.GetId.return_value = "id-1"
    dev1 = MagicMock(id="id-1", FriendlyName="Speakers", state=1)
    dev2 = MagicMock(id="id-2", FriendlyName="Headphones", state=1)
    dev3 = MagicMock(id="id-3", FriendlyName="Disabled Device", state=0)  # inactive
    with patch.object(cs, "_OS", "Windows"), \
         patch("pycaw.pycaw.AudioUtilities.GetSpeakers", return_value=speakers), \
         patch("pycaw.pycaw.AudioUtilities.GetAllDevices", return_value=[dev1, dev2, dev3]):
        result = cs.list_audio_devices()
    names = {d["name"]: d["default"] for d in result}
    assert names == {"Speakers": True, "Headphones": False}
    print("test_list_audio_devices_marks_the_correct_default_and_skips_inactive: PASS")


# ── computer_settings() dispatch wiring ─────────────────────────────────

def test_dispatch_app_volume_set_verifies_readback_before_claiming_success() -> None:
    with patch.object(cs, "app_volume_set", return_value=True), \
         patch.object(cs, "app_volume_get", return_value=70):
        result = _cs(action="app_volume_set", app="spotify", value="70")
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "70" in result
    print("test_dispatch_app_volume_set_verifies_readback_before_claiming_success: PASS")

def test_dispatch_app_volume_set_reports_failure_when_readback_disagrees() -> None:
    with patch.object(cs, "app_volume_set", return_value=True), \
         patch.object(cs, "app_volume_get", return_value=20):
        result = _cs(action="app_volume_set", app="spotify", value="70")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_dispatch_app_volume_set_reports_failure_when_readback_disagrees: PASS")

def test_dispatch_app_volume_set_with_no_matching_app_is_honest_not_inconclusive() -> None:
    with patch.object(cs, "app_volume_set", return_value=None) as m:
        result = _cs(action="app_volume_set", app="not_running", value="50")
    m.assert_called_once()
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "not_running" in result
    print("test_dispatch_app_volume_set_with_no_matching_app_is_honest_not_inconclusive: PASS")

def test_dispatch_app_volume_set_with_no_app_name_never_calls_pycaw() -> None:
    with patch.object(cs, "app_volume_set") as m:
        result = _cs(action="app_volume_set", value="50")
    m.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_dispatch_app_volume_set_with_no_app_name_never_calls_pycaw: PASS")

def test_dispatch_app_mute_and_app_unmute_route_the_correct_target_state() -> None:
    with patch.object(cs, "app_mute_set", return_value=True) as m_set, \
         patch.object(cs, "app_mute_get", return_value=True):
        result = _cs(action="app_mute", app="discord")
    m_set.assert_called_once_with("discord", True)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "muted" in result

    with patch.object(cs, "app_mute_set", return_value=True) as m_set2, \
         patch.object(cs, "app_mute_get", return_value=False):
        result2 = _cs(action="app_unmute", app="discord")
    m_set2.assert_called_once_with("discord", False)
    assert "unmuted" in result2
    print("test_dispatch_app_mute_and_app_unmute_route_the_correct_target_state: PASS")

def test_dispatch_list_audio_devices_reports_the_real_default() -> None:
    fake = [{"name": "Speakers", "default": True}, {"name": "Headphones", "default": False}]
    with patch.object(cs, "list_audio_devices", return_value=fake):
        result = _cs(action="list_audio_devices")
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "Speakers (default)" in result
    assert "Headphones" in result and "Headphones (default)" not in result
    print("test_dispatch_list_audio_devices_reports_the_real_default: PASS")

def test_dispatch_list_audio_devices_when_pycaw_fails_is_inconclusive_not_empty() -> None:
    with patch.object(cs, "list_audio_devices", return_value=None):
        result = _cs(action="list_audio_devices")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_dispatch_list_audio_devices_when_pycaw_fails_is_inconclusive_not_empty: PASS")


if __name__ == "__main__":
    test_find_audio_session_matches_case_and_exe_suffix_insensitively()
    test_find_audio_session_skips_sessions_with_no_process_system_sounds()
    test_find_audio_session_returns_none_when_nothing_matches()
    test_app_volume_set_returns_none_when_no_matching_session()
    test_app_volume_set_calls_set_master_volume_as_a_0_to_1_fraction()
    test_app_mute_set_calls_set_mute_with_the_requested_state()
    test_app_volume_get_reads_back_as_a_0_to_100_int()
    test_non_windows_functions_return_none_immediately_no_pycaw_import_attempted()
    test_list_audio_devices_marks_the_correct_default_and_skips_inactive()
    test_dispatch_app_volume_set_verifies_readback_before_claiming_success()
    test_dispatch_app_volume_set_reports_failure_when_readback_disagrees()
    test_dispatch_app_volume_set_with_no_matching_app_is_honest_not_inconclusive()
    test_dispatch_app_volume_set_with_no_app_name_never_calls_pycaw()
    test_dispatch_app_mute_and_app_unmute_route_the_correct_target_state()
    test_dispatch_list_audio_devices_reports_the_real_default()
    test_dispatch_list_audio_devices_when_pycaw_fails_is_inconclusive_not_empty()
    print("\nAll app_audio_control tests passed.")
