"""
tests/test_universal_profile.py — proves the SQLite user/profile system,
language, assistant identity, voice preference, and greeting personalization
are UNIVERSAL to the SARANA brain (desktop and web both go through the same
_user_profile / _build_config() / _send_startup_briefing() mechanism), not
web-only behavior with desktop left out.

Desktop path exercised here: _resolve_desktop_profile(), which resolves the
same SQLite profile from config/api_keys.json's existing user_name field —
no PIN, no dashboard/HTTP involved (see that method's own docstring for why
no PIN is correct here, not a weaker version of web auth).
Web path exercised here: _set_user_profile()/_set_web_username(), exactly
what dashboard/server.py's /login/username fires after a real PIN check.

Every test isolates its own temp SQLite file (patch.object(user_db,
"DB_PATH", ...)) and/or temp config file (patch.object(main,
"API_CONFIG_PATH", ...)) — nothing here touches the real data/sarana.db or
config/api_keys.json.

Run with:
    .venv/Scripts/python.exe -m tests.test_universal_profile
"""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import main
from core.headless_surface import HeadlessSurface
from main import JarvisLive
from users import user_db


class _FakeSession:
    def __init__(self):
        self.sent = []

    async def send_client_content(self, turns, turn_complete=True):
        self.sent.append(turns["parts"][0]["text"])


def _desktop_config(tmp_dir: str, user_name: str) -> Path:
    cfg_path = Path(tmp_dir) / "api_keys.json"
    cfg_path.write_text(
        json.dumps({"gemini_api_key": "x", "assistant_name": "SARANA", "user_name": user_name}),
        encoding="utf-8",
    )
    return cfg_path


# ── 1/2/3: web profile loads, desktop profile loads, both identical ──────

def test_web_profile_loads_correctly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sarana.db"
        with patch.object(user_db, "DB_PATH", db_path):
            user_db.init_db()
            profile = user_db.authenticate("Saroj", "2057")

        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._set_user_profile(profile)   # exactly what set_profile_callback fires
        assert jarvis._user_profile is not None
        assert jarvis._user_profile["nickname"] == "Saroj"
    print("test_web_profile_loads_correctly: PASS")


def test_desktop_profile_loads_correctly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sarana.db"
        cfg_path = _desktop_config(tmp, "Saroj")
        with patch.object(user_db, "DB_PATH", db_path), patch.object(main, "API_CONFIG_PATH", cfg_path):
            user_db.init_db()
            jarvis = JarvisLive(HeadlessSurface())   # auto_start=True — desktop's default
            jarvis._resolve_desktop_profile()
            assert jarvis._user_profile is not None
            assert jarvis._user_profile["nickname"] == "Saroj"
    print("test_desktop_profile_loads_correctly: PASS")


def test_web_and_desktop_produce_the_same_canonical_profile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sarana.db"
        cfg_path = _desktop_config(tmp, "Bandana")
        with patch.object(user_db, "DB_PATH", db_path):
            user_db.init_db()
            web_profile = user_db.authenticate("Bandana", "2060")

        with patch.object(user_db, "DB_PATH", db_path), patch.object(main, "API_CONFIG_PATH", cfg_path):
            jarvis = JarvisLive(HeadlessSurface())
            jarvis._resolve_desktop_profile()
            desktop_profile = jarvis._user_profile

        assert web_profile == desktop_profile, "same alias must resolve to the identical profile dict"
    print("test_web_and_desktop_produce_the_same_canonical_profile: PASS")


def test_unrecognized_desktop_user_name_leaves_profile_none() -> None:
    """No PIN, no crash, no partial state — just today's pre-existing
    (non-personalized) behavior when the config name isn't a seeded alias."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sarana.db"
        cfg_path = _desktop_config(tmp, "SomeoneNotSeeded")
        with patch.object(user_db, "DB_PATH", db_path), patch.object(main, "API_CONFIG_PATH", cfg_path):
            user_db.init_db()
            jarvis = JarvisLive(HeadlessSurface())
            jarvis._resolve_desktop_profile()
            assert jarvis._user_profile is None
            assert jarvis._web_user_name is None
    print("test_unrecognized_desktop_user_name_leaves_profile_none: PASS")


# ── 4/5/6/9: same language / assistant name / voice / _build_config() ────

def _build_config_for(interface: str, tmp: str, alias: str, pin: str = None):
    """Returns (system_instruction, voice_name) for either interface,
    resolving the SAME seeded profile. Always runs against an isolated
    temp config file (never the real config/api_keys.json), regardless of
    interface, so results are deterministic."""
    db_path = Path(tmp) / f"sarana-{interface}.db"
    cfg_path = Path(tmp) / f"api_keys-{interface}.json"
    cfg_path.write_text(
        json.dumps({
            "gemini_api_key": "x",
            "assistant_name": "SARANA",
            "user_name": alias if interface == "desktop" else "",
        }),
        encoding="utf-8",
    )

    with patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        if interface == "web":
            profile = user_db.authenticate(alias, pin)

    with patch.object(main, "API_CONFIG_PATH", cfg_path):
        if interface == "web":
            jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
            jarvis._set_user_profile(profile)
            jarvis._web_user_name = profile["pronunciation"] or profile["nickname"]
        else:
            jarvis = JarvisLive(HeadlessSurface())
            with patch.object(user_db, "DB_PATH", db_path):
                jarvis._resolve_desktop_profile()

        config = jarvis._build_config()

    return config.system_instruction, config.speech_config.voice_config.prebuilt_voice_config.voice_name


def test_language_preference_identical_across_interfaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        web_instr, _ = _build_config_for("web", tmp, "Saroj", "2057")
        desktop_instr, _ = _build_config_for("desktop", tmp, "Saroj")
        assert "Language preference: Nepali" in web_instr
        assert "Language preference: Nepali" in desktop_instr
    print("test_language_preference_identical_across_interfaces: PASS")


def test_assistant_name_identical_across_interfaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        web_instr, _ = _build_config_for("web", tmp, "Bandana", "2060")
        desktop_instr, _ = _build_config_for("desktop", tmp, "Bandana")
        assert "Your name is Kanha" in web_instr
        assert "Your name is Kanha" in desktop_instr
    print("test_assistant_name_identical_across_interfaces: PASS")


def test_voice_preference_identical_across_interfaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _, web_voice = _build_config_for("web", tmp, "Saroj", "2057")
        _, desktop_voice = _build_config_for("desktop", tmp, "Saroj")
        assert web_voice == desktop_voice == "Kore"

        _, web_voice2 = _build_config_for("web", tmp, "Bandana", "2060")
        _, desktop_voice2 = _build_config_for("desktop", tmp, "Bandana")
        assert web_voice2 == desktop_voice2 == "Charon"
    print("test_voice_preference_identical_across_interfaces: PASS")


def test_build_config_user_profile_block_identical_across_interfaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        web_instr, _ = _build_config_for("web", tmp, "Bandana", "2060")
        desktop_instr, _ = _build_config_for("desktop", tmp, "Bandana")

        def _profile_block(instr):
            start = instr.index("[USER PROFILE]")
            end = instr.index("\n\n", start)
            return instr[start:end]

        assert _profile_block(web_instr) == _profile_block(desktop_instr)
    print("test_build_config_user_profile_block_identical_across_interfaces: PASS")


# ── 7/8: timezone source per interface ────────────────────────────────────

def test_desktop_timezone_comes_from_local_os_not_a_hardcoded_zone() -> None:
    from datetime import datetime
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._web_timezone is None   # desktop never sets this
    now = jarvis._local_now()
    assert now.tzinfo is None   # naive == the OS's own local clock, not a fixed IANA zone
    assert abs((now - datetime.now()).total_seconds()) < 5
    print("test_desktop_timezone_comes_from_local_os_not_a_hardcoded_zone: PASS")


def test_web_timezone_comes_from_browser_iana_zone() -> None:
    from zoneinfo import ZoneInfo
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._set_web_timezone("Asia/Kathmandu")
    now = jarvis._local_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == ZoneInfo("Asia/Kathmandu").utcoffset(now)
    print("test_web_timezone_comes_from_browser_iana_zone: PASS")


def test_resolve_desktop_profile_never_touches_web_timezone() -> None:
    """Desktop profile resolution is about identity/preferences only —
    timezone stays governed entirely by _local_now()'s own desktop/web
    branching (see main.py), never set as a side effect of the profile."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sarana.db"
        cfg_path = _desktop_config(tmp, "Saroj")
        with patch.object(user_db, "DB_PATH", db_path), patch.object(main, "API_CONFIG_PATH", cfg_path):
            user_db.init_db()
            jarvis = JarvisLive(HeadlessSurface())
            jarvis._resolve_desktop_profile()
            assert jarvis._web_timezone is None
    print("test_resolve_desktop_profile_never_touches_web_timezone: PASS")


# ── 10: greeting personalization identical across interfaces ─────────────

def test_greeting_prompt_personalized_identically_across_interfaces() -> None:
    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sarana.db"
            with patch.object(user_db, "DB_PATH", db_path):
                user_db.init_db()
                profile = user_db.authenticate("Saroj", "2057")

            web = JarvisLive(HeadlessSurface(), auto_start=False)
            web._set_user_profile(profile)
            web._web_user_name = "Saroj"
            web.session = _FakeSession()

            cfg_path = _desktop_config(tmp, "Saroj")
            with patch.object(user_db, "DB_PATH", db_path), patch.object(main, "API_CONFIG_PATH", cfg_path):
                desktop = JarvisLive(HeadlessSurface())
                desktop._resolve_desktop_profile()
            desktop.session = _FakeSession()

            with patch("main.pop_last_session", return_value=None):
                await web._send_startup_briefing()
                await desktop._send_startup_briefing()

            web_prompt = web.session.sent[0]
            desktop_prompt = desktop.session.sent[0]
            assert "Saroj" in web_prompt and "Saroj" in desktop_prompt
            # Same natural-language greeting guidance either way (not tied
            # to a specific banned phrase — see LANGUAGE clause in main.py).
            assert (
                "generated fresh for this exact moment" in web_prompt
                and "generated fresh for this exact moment" in desktop_prompt
            )
            assert ("never as 'sir' or 'efendim'" in web_prompt) == (
                "never as 'sir' or 'efendim'" in desktop_prompt
            )
    asyncio.run(_run())
    print("test_greeting_prompt_personalized_identically_across_interfaces: PASS")


def test_desktop_greeting_trigger_lifecycle_unaffected_by_profile() -> None:
    """The actual point of failure this feature could have introduced:
    _resolve_desktop_profile() must NOT re-arm _pending_web_greeting (that
    flag is web-login-specific — see _set_web_username()'s docstring), or
    a desktop reconnect would fire a second, unwanted greeting."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sarana.db"
        cfg_path = _desktop_config(tmp, "Saroj")
        with patch.object(user_db, "DB_PATH", db_path), patch.object(main, "API_CONFIG_PATH", cfg_path):
            user_db.init_db()
            jarvis = JarvisLive(HeadlessSurface())
            jarvis._resolve_desktop_profile()
            assert jarvis._pending_web_greeting is False
            assert jarvis._briefing_sent is False   # desktop's own trigger, untouched
    print("test_desktop_greeting_trigger_lifecycle_unaffected_by_profile: PASS")


if __name__ == "__main__":
    test_web_profile_loads_correctly()
    test_desktop_profile_loads_correctly()
    test_web_and_desktop_produce_the_same_canonical_profile()
    test_unrecognized_desktop_user_name_leaves_profile_none()
    test_language_preference_identical_across_interfaces()
    test_assistant_name_identical_across_interfaces()
    test_voice_preference_identical_across_interfaces()
    test_build_config_user_profile_block_identical_across_interfaces()
    test_desktop_timezone_comes_from_local_os_not_a_hardcoded_zone()
    test_web_timezone_comes_from_browser_iana_zone()
    test_resolve_desktop_profile_never_touches_web_timezone()
    test_greeting_prompt_personalized_identically_across_interfaces()
    test_desktop_greeting_trigger_lifecycle_unaffected_by_profile()
    print("\nAll universal-profile tests passed.")
