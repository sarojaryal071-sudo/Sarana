"""
tests/test_live_session_voice_config.py -- targeted corrections for the
pre-J4 web-voice-latency investigation, both in JarvisLive._build_config()
(main.py):

  1. realtime_input_config.automatic_activity_detection was never
     explicitly configured, so turn-taking relied entirely on Gemini
     Live's own unconfigured VAD defaults. Now tuned explicitly (see
     _build_config()'s own comment for the reasoning behind each value).
  2. Proactive audio (ProactivityConfig(proactive_audio=True)) used to be
     turned on unconditionally by self._enhanced_live, alongside affective
     dialog -- a documented extra latency source on every turn. Split out
     into its own self._proactive_audio switch, OFF by default; affective
     dialog is kept on when self._enhanced_live is True (it wasn't the
     thing implicated in the response-delay complaint).

Deterministic config assertions only -- no live Gemini Live session is
ever opened here (that's what the "current SDK/API version already used
by this repository" instruction meant to inspect, not to connect to).

Run with:
    .venv/Scripts/python.exe -m tests.test_live_session_voice_config
"""
from google.genai import types

from core.headless_surface import HeadlessSurface
from main import JarvisLive


# ── automatic_activity_detection: explicit VAD tuning ───────────────────

def test_build_config_sets_explicit_automatic_activity_detection() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    config = jarvis._build_config()
    ric = config.realtime_input_config
    assert ric is not None, "realtime_input_config must be explicitly set, not left as the API default"
    aad = ric.automatic_activity_detection
    assert aad is not None
    assert aad.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_HIGH
    assert aad.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_HIGH
    assert aad.prefix_padding_ms == 100
    assert aad.silence_duration_ms == 600
    print("test_build_config_sets_explicit_automatic_activity_detection: PASS")


def test_build_config_never_disables_automatic_activity_detection() -> None:
    # This task explicitly requires server-side Gemini Live VAD to remain
    # the primary turn detector -- `disabled` must never be set True.
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    aad = jarvis._build_config().realtime_input_config.automatic_activity_detection
    assert not aad.disabled
    print("test_build_config_never_disables_automatic_activity_detection: PASS")


def test_build_config_leaves_activity_handling_and_turn_coverage_untouched() -> None:
    # Barge-in (main.py's own "Barge-in: Gemini's own server-side VAD
    # detected..." handling) depends on the API's existing default
    # activity_handling behavior -- this fix must not touch it.
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    ric = jarvis._build_config().realtime_input_config
    assert ric.activity_handling is None
    assert ric.turn_coverage is None
    print("test_build_config_leaves_activity_handling_and_turn_coverage_untouched: PASS")


def test_build_config_vad_tuning_applies_to_both_desktop_and_web_sessions() -> None:
    # Not a desktop-only or web-only fix -- both auto_start states share
    # the one _build_config().
    desktop = JarvisLive(HeadlessSurface())            # auto_start=True
    web = JarvisLive(HeadlessSurface(), auto_start=False)
    d_aad = desktop._build_config().realtime_input_config.automatic_activity_detection
    w_aad = web._build_config().realtime_input_config.automatic_activity_detection
    assert d_aad.silence_duration_ms == w_aad.silence_duration_ms == 600
    print("test_build_config_vad_tuning_applies_to_both_desktop_and_web_sessions: PASS")


# ── proactive audio: now deliberate/opt-in, not unconditional ──────────

def test_proactive_audio_is_off_by_default() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    assert jarvis._proactive_audio is False
    print("test_proactive_audio_is_off_by_default: PASS")


def test_build_config_default_enhanced_live_enables_affective_dialog_but_not_proactivity() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    assert jarvis._enhanced_live is True     # unchanged default
    assert jarvis._proactive_audio is False  # the actual fix
    config = jarvis._build_config()
    assert config.enable_affective_dialog is True   # preserved -- not implicated in the latency complaint
    assert config.proactivity is None                # the latency source is now off by default
    print("test_build_config_default_enhanced_live_enables_affective_dialog_but_not_proactivity: PASS")


def test_build_config_proactivity_can_still_be_deliberately_enabled() -> None:
    # The mechanism is preserved for a future mode that genuinely wants
    # it -- just no longer unconditional.
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._proactive_audio = True
    config = jarvis._build_config()
    assert config.proactivity is not None
    assert config.proactivity.proactive_audio is True
    print("test_build_config_proactivity_can_still_be_deliberately_enabled: PASS")


def test_build_config_proactive_audio_flag_is_ignored_without_enhanced_live() -> None:
    # proactivity/affective dialog both require the v1alpha preview
    # surface enable_affective_dialog itself lives under -- if enhanced
    # live has been auto-disabled (server rejection), neither should be
    # sent, regardless of self._proactive_audio.
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._enhanced_live = False
    jarvis._proactive_audio = True
    config = jarvis._build_config()
    assert config.enable_affective_dialog is None
    assert config.proactivity is None
    print("test_build_config_proactive_audio_flag_is_ignored_without_enhanced_live: PASS")


def _run() -> None:
    test_build_config_sets_explicit_automatic_activity_detection()
    test_build_config_never_disables_automatic_activity_detection()
    test_build_config_leaves_activity_handling_and_turn_coverage_untouched()
    test_build_config_vad_tuning_applies_to_both_desktop_and_web_sessions()
    test_proactive_audio_is_off_by_default()
    test_build_config_default_enhanced_live_enables_affective_dialog_but_not_proactivity()
    test_build_config_proactivity_can_still_be_deliberately_enabled()
    test_build_config_proactive_audio_flag_is_ignored_without_enhanced_live()
    print("\nAll live_session_voice_config tests passed.")


if __name__ == "__main__":
    _run()
