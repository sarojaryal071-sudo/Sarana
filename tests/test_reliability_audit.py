"""
tests/test_reliability_audit.py — regression tests for the reliability
audit: explicit runtime language switching, a single resolved-language
source of truth, logout suppressing background speech, and a rapid-login
race during an in-flight Gemini connect.

Run with:
    .venv/Scripts/python.exe -m tests.test_reliability_audit
"""
import asyncio
import inspect
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import main
from core.headless_surface import HeadlessSurface
from main import JarvisLive
from users import user_db
from tests.test_phase7_lifecycle import _FakeDashboard
from tests.test_voice_reconnect import (
    _FakeConnectCM, _FakeLive, _FakeGenaiClient, _FakeLiveSession,
)


def _isolated_desktop_config(tmp_dir: str, user_name: str) -> Path:
    cfg_path = Path(tmp_dir) / "api_keys.json"
    cfg_path.write_text(
        json.dumps({"gemini_api_key": "x", "assistant_name": "SARANA", "user_name": user_name}),
        encoding="utf-8",
    )
    return cfg_path


# ── _resolve_effective_language() — single source of truth ───────────────

def test_resolve_effective_language_defaults_to_nepali() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    with patch("main.load_memory", return_value={}):
        assert jarvis._resolve_effective_language() == "Nepali"
    print("test_resolve_effective_language_defaults_to_nepali: PASS")


def test_resolve_effective_language_prefers_memory_over_profile() -> None:
    """This is the exact live bug: a previously-saved identity.language
    fact (from an earlier explicit "speak English" request) must now
    actually govern the effective language, beating a profile default
    that still says Nepali."""
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = {"language_preference": "Nepali"}
    fake_memory = {"identity": {"language": {"value": "English"}}}
    with patch("main.load_memory", return_value=fake_memory):
        assert jarvis._resolve_effective_language() == "English"
    print("test_resolve_effective_language_prefers_memory_over_profile: PASS")


def test_resolve_effective_language_falls_back_to_profile_when_memory_empty() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._user_profile = {"language_preference": "Turkish"}
    with patch("main.load_memory", return_value={}):
        assert jarvis._resolve_effective_language() == "Turkish"
    print("test_resolve_effective_language_falls_back_to_profile_when_memory_empty: PASS")


def test_resolve_effective_language_explicit_override_wins_for_same_identity() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._session_owner = "saroj"
    jarvis._effective_language = "English"
    jarvis._effective_language_owner = "saroj"
    fake_memory = {"identity": {"language": {"value": "Nepali"}}}  # would contradict if not overridden
    with patch("main.load_memory", return_value=fake_memory):
        assert jarvis._resolve_effective_language() == "English"
    print("test_resolve_effective_language_explicit_override_wins_for_same_identity: PASS")


def test_resolve_effective_language_override_does_not_leak_to_a_different_identity() -> None:
    """The exact identity-switch requirement: an explicit runtime language
    change made by ONE user must never leak into a DIFFERENT user's
    session — only the persisted default applies for them."""
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._effective_language = "English"
    jarvis._effective_language_owner = "saroj"
    jarvis._session_owner = "sana"   # a different identity is now active
    with patch("main.load_memory", return_value={}):
        assert jarvis._resolve_effective_language() == "Nepali"
    print("test_resolve_effective_language_override_does_not_leak_to_a_different_identity: PASS")


# ── _build_config()'s LANGUAGE directive reflects the resolved language ──

def test_build_config_uses_non_nepali_effective_language() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._effective_language = "English"
    jarvis._effective_language_owner = ""   # matches the default session_owner ("")
    with patch("main.load_memory", return_value={}):
        config = jarvis._build_config()
    instr = config.system_instruction
    assert "LANGUAGE:" in instr
    assert "natural, conversational English" in instr
    assert "Sanskritized" not in instr   # the Nepali-specific block must not also appear
    print("test_build_config_uses_non_nepali_effective_language: PASS")


# ── explicit runtime switch via save_memory ───────────────────────────────

class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


def test_save_memory_language_change_returns_language_changed_directive() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        with patch("main.update_memory") as mock_update:
            fc = _FakeFunctionCall("save_memory", {
                "category": "identity", "key": "language", "value": "English",
            })
            resp = await jarvis._execute_tool(fc)
            mock_update.assert_called_once()
        result = resp.response["result"]
        assert "[LANGUAGE_CHANGED]" in result
        assert "English" in result
        assert jarvis._effective_language == "English"
        assert jarvis._effective_language_owner == jarvis._session_owner
    asyncio.run(_run())
    print("test_save_memory_language_change_returns_language_changed_directive: PASS")


def test_save_memory_non_language_identity_field_unaffected() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        with patch("main.update_memory"):
            fc = _FakeFunctionCall("save_memory", {
                "category": "identity", "key": "name", "value": "Saroj",
            })
            resp = await jarvis._execute_tool(fc)
        assert resp.response["result"] == "ok"
        assert jarvis._effective_language == ""
    asyncio.run(_run())
    print("test_save_memory_non_language_identity_field_unaffected: PASS")


def test_save_memory_language_change_immediately_affects_this_connections_config() -> None:
    """End-to-end within one connection: an explicit switch recorded via
    _execute_tool() must be picked up by a LATER _resolve_effective_language()
    call in the SAME identity — this is what makes greeting/proactive/
    monitor calls agree with a switch that just happened."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        with patch("main.update_memory"), patch("main.load_memory", return_value={}):
            fc = _FakeFunctionCall("save_memory", {
                "category": "identity", "key": "language", "value": "Turkish",
            })
            await jarvis._execute_tool(fc)
            assert jarvis._resolve_effective_language() == "Turkish"
    asyncio.run(_run())
    print("test_save_memory_language_change_immediately_affects_this_connections_config: PASS")


# ── ProactiveEngine.build_prompt() — device-local time + explicit language ─

def test_proactive_build_prompt_uses_supplied_time_and_language() -> None:
    from datetime import datetime
    from actions.proactive import ProactiveEngine

    engine = ProactiveEngine()
    fixed = datetime(2026, 6, 15, 21, 0)   # 9 PM — unlikely to match server "now" by accident
    prompt = engine.build_prompt(memory={}, now=fixed, language="English")
    assert "9:00 PM" in prompt or "09:00 PM" in prompt
    assert "Speak in English." in prompt
    assert "check memory; default" not in prompt
    print("test_proactive_build_prompt_uses_supplied_time_and_language: PASS")


def test_proactive_build_prompt_defaults_safely_without_args() -> None:
    """Backward compatible — a caller with no better source still works."""
    from actions.proactive import ProactiveEngine
    engine = ProactiveEngine()
    prompt = engine.build_prompt(memory={})
    assert "[PROACTIVE_CHECK]" in prompt
    print("test_proactive_build_prompt_defaults_safely_without_args: PASS")


# ── logged-out session suppresses background speech ───────────────────────

def test_clear_memory_session_marks_logged_out() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._logged_out is False
    with patch("main.clear_active_session"):
        jarvis._clear_memory_session()
    assert jarvis._logged_out is True
    print("test_clear_memory_session_marks_logged_out: PASS")


def test_set_user_profile_clears_logged_out_flag() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._logged_out = True
    with patch("main.set_active_owner"):
        jarvis._set_user_profile(user_db.authenticate("Saroj", "2057"))
    assert jarvis._logged_out is False
    print("test_set_user_profile_clears_logged_out_flag: PASS")


def test_background_speech_loops_check_logged_out_flag() -> None:
    """Source-level guard check: real-time background loops sleep for
    minutes between evaluations (300s/1800s/10s), which makes waiting for
    a live iteration impractical in a test — confirm the actual gating
    condition is present in each loop instead, alongside the direct
    flag-lifecycle tests above."""
    for fn in (
        JarvisLive._run_proactive_mode,
        JarvisLive._run_background_monitor,
        JarvisLive._run_system_monitor,
    ):
        src = inspect.getsource(fn)
        assert "self._logged_out" in src, f"{fn.__name__} must check self._logged_out"
    print("test_background_speech_loops_check_logged_out_flag: PASS")


def test_desktop_never_sets_logged_out() -> None:
    """Desktop has no logout concept — _clear_memory_session() is never
    called there, so this must stay False for the whole process life."""
    jarvis = JarvisLive(HeadlessSurface())   # auto_start=True, desktop's default
    assert jarvis._logged_out is False
    print("test_desktop_never_sets_logged_out: PASS")


# ── connection-scoped state reset ─────────────────────────────────────────

def test_connect_loop_resets_last_user_speech_and_phone_active() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis._last_user_speech = 0.0   # simulate a long-stale value
        jarvis._phone_active = True

        recorder, sent_messages = [], []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        tmp = tempfile.TemporaryDirectory()
        cfg_path = _isolated_desktop_config(tmp.name, "Saroj")
        with tmp, \
             patch.object(main, "API_CONFIG_PATH", cfg_path), \
             patch("dashboard.server.DashboardServer", return_value=_FakeDashboard()), \
             patch("main.genai.Client", side_effect=make_client), \
             patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
             patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")):
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.5)

            assert len(recorder) == 1
            assert jarvis._phone_active is False
            assert (time.monotonic() - jarvis._last_user_speech) < 5, (
                "a fresh connection must reset the proactive-mode silence "
                "baseline, not inherit a stale one from before it connected"
            )

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(_run())
    print("test_connect_loop_resets_last_user_speech_and_phone_active: PASS")


# ── rapid-login race during an in-flight Gemini connect ───────────────────

class _RaceConnectCM(_FakeConnectCM):
    def __init__(self, sent_messages, on_enter=None):
        super().__init__(sent_messages)
        self._on_enter = on_enter

    async def __aenter__(self):
        # The moment a real SDK would be doing actual network I/O — this
        # is exactly where a second login can race in before the first
        # connection is considered "up".
        if self._on_enter:
            self._on_enter()
        return await super().__aenter__()


class _RacyFakeLive(_FakeLive):
    def __init__(self, recorder, sent_messages, race_fn, fired):
        super().__init__(recorder, sent_messages)
        self._race_fn = race_fn
        self._fired = fired   # shared, process-wide "has the race already happened" flag

    def connect(self, *, model, config):
        self._recorder.append(config)
        on_enter = None
        if not self._fired[0]:
            self._fired[0] = True
            on_enter = self._race_fn
        return _RaceConnectCM(self._sent_messages, on_enter)


class _RacyFakeGenaiClient:
    def __init__(self, recorder, sent_messages, race_fn, fired, *a, **kw):
        self.aio = type("Aio", (), {"live": _RacyFakeLive(recorder, sent_messages, race_fn, fired)})()


def test_login_racing_in_during_connect_forces_a_clean_reconnect() -> None:
    """Saroj is already the active profile when the connect loop starts;
    while that very first connection is still being established (still
    inside __aenter__ — the network handshake), Sana logs in. The
    in-flight connection must be discarded and a second, correct one made
    — using Sana's voice/identity — with exactly one greeting, addressed
    to Sana, ever going out."""
    async def _run():
        recorder, sent_messages = [], []
        fired = [False]
        saroj = user_db.authenticate("Saroj", "2057")
        sana = user_db.authenticate("Sana", "2060")

        def race_fn():
            jarvis._set_user_profile(sana)
            jarvis._set_web_username(sana["pronunciation"] or sana["nickname"])

        def make_client(*a, **kw):
            return _RacyFakeGenaiClient(recorder, sent_messages, race_fn, fired, *a, **kw)

        jarvis = JarvisLive(HeadlessSurface())   # auto_start=True
        jarvis._user_profile = saroj
        jarvis._web_user_name = "Saroj"

        tmp = tempfile.TemporaryDirectory()
        cfg_path = _isolated_desktop_config(tmp.name, "Saroj")
        with tmp, \
             patch.object(main, "API_CONFIG_PATH", cfg_path), \
             patch("dashboard.server.DashboardServer", return_value=_FakeDashboard()), \
             patch("main.genai.Client", side_effect=make_client), \
             patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
             patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")):
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.8)

            assert len(recorder) == 2, (
                "the raced (stale-profile) connection must be discarded and "
                "a fresh one made with the CURRENT profile"
            )
            second_instr = recorder[1].system_instruction
            assert "Your name is Kanha" in second_instr
            assert "'Saanaa'" in second_instr
            assert "Your name is Sara" not in second_instr

            assert len(sent_messages) == 1, (
                "only the FINAL (correct) connection's greeting must ever "
                "go out — the discarded connection must never greet anyone"
            )
            assert "Saanaa" in sent_messages[0]

            assert jarvis._user_profile["username"] == "sana"
            assert jarvis._session_owner == "sana"

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(_run())
    print("test_login_racing_in_during_connect_forces_a_clean_reconnect: PASS")


def test_no_race_means_exactly_one_connection() -> None:
    """Sanity companion: when nothing races in, the generation check must
    never cause a spurious extra reconnect."""
    async def _run():
        recorder, sent_messages = [], []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        jarvis = JarvisLive(HeadlessSurface())
        jarvis._user_profile = user_db.authenticate("Saroj", "2057")
        jarvis._web_user_name = "Saroj"

        tmp = tempfile.TemporaryDirectory()
        cfg_path = _isolated_desktop_config(tmp.name, "Saroj")
        with tmp, \
             patch.object(main, "API_CONFIG_PATH", cfg_path), \
             patch("dashboard.server.DashboardServer", return_value=_FakeDashboard()), \
             patch("main.genai.Client", side_effect=make_client), \
             patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
             patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")):
            task = asyncio.create_task(jarvis.run())
            await asyncio.sleep(0.5)

            assert len(recorder) == 1
            assert len(sent_messages) == 1

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(_run())
    print("test_no_race_means_exactly_one_connection: PASS")


if __name__ == "__main__":
    test_resolve_effective_language_defaults_to_nepali()
    test_resolve_effective_language_prefers_memory_over_profile()
    test_resolve_effective_language_falls_back_to_profile_when_memory_empty()
    test_resolve_effective_language_explicit_override_wins_for_same_identity()
    test_resolve_effective_language_override_does_not_leak_to_a_different_identity()
    test_build_config_uses_non_nepali_effective_language()
    test_save_memory_language_change_returns_language_changed_directive()
    test_save_memory_non_language_identity_field_unaffected()
    test_save_memory_language_change_immediately_affects_this_connections_config()
    test_proactive_build_prompt_uses_supplied_time_and_language()
    test_proactive_build_prompt_defaults_safely_without_args()
    test_clear_memory_session_marks_logged_out()
    test_set_user_profile_clears_logged_out_flag()
    test_background_speech_loops_check_logged_out_flag()
    test_desktop_never_sets_logged_out()
    test_connect_loop_resets_last_user_speech_and_phone_active()
    test_login_racing_in_during_connect_forces_a_clean_reconnect()
    test_no_race_means_exactly_one_connection()
    print("\nAll reliability-audit tests passed.")
