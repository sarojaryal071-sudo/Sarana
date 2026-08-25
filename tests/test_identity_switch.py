"""
tests/test_identity_switch.py — regression tests for the "SARANA always
greets the user as Saroj" bug.

Root cause: _build_config()'s system_instruction (ADDRESS clause,
[USER PROFILE] block, the assistant's own name) is computed once, at
Gemini connect time, and is never rebuilt mid-session — a Gemini Live API
constraint, not a bug in the profile-loading code itself (which was
already correct — see tests/test_universal_profile.py). A second login on
an ALREADY-CONNECTED session (e.g. logging out and back in as a different
account without the backend process restarting/reconnecting) updated
JarvisLive's own state (_user_profile/_web_user_name) correctly, but the
live session kept speaking as whoever connected FIRST, because nothing
told Gemini the identity had changed.

Fix: _set_web_username()'s "session already connected" branch now calls
_send_startup_briefing(identity_switch=True), which appends an explicit,
forceful in-conversation correction (new name + new assistant identity)
instead of a plain greeting. A fresh connection's own first greeting is
unaffected (identity_switch defaults to False there).

Run with:
    .venv/Scripts/python.exe -m tests.test_identity_switch
"""
import asyncio

from core.headless_surface import HeadlessSurface
from main import JarvisLive
from users import user_db


class _FakeSession:
    def __init__(self):
        self.sent = []

    async def send_client_content(self, turns, turn_complete=True):
        self.sent.append(turns["parts"][0]["text"])


def _connected_jarvis(loop, stale_alias: str, stale_pin: str) -> JarvisLive:
    """A JarvisLive that's already mid-session under `stale_alias`'s
    identity — simulates a login that happened earlier in this same
    process/connection, before the switch being tested."""
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis._loop = loop
    jarvis.session = _FakeSession()
    stale_profile = user_db.authenticate(stale_alias, stale_pin)
    jarvis._user_profile = stale_profile
    jarvis._asst_name = stale_profile["assistant_name"]
    jarvis._web_user_name = stale_profile["pronunciation"] or stale_profile["nickname"]
    return jarvis


# ── 1/2/3: correct identity per profile (fresh connection, sanity) ───────

def _address_clause(system_instruction: str) -> str:
    start = system_instruction.index("ADDRESS:")
    end = system_instruction.index("\n", start)
    return system_instruction[start:end]


def test_sana_profile_produces_saanaa_not_saroj() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    profile = user_db.authenticate("Sana", "2060")
    jarvis._set_user_profile(profile)
    jarvis._set_web_username(profile["pronunciation"] or profile["nickname"])
    config = jarvis._build_config()
    addr = _address_clause(config.system_instruction)
    assert "'Saanaa'" in addr
    assert "Saroj" not in addr   # the ADDRESS clause itself, not memory content elsewhere
    assert "Your name is Kanha" in config.system_instruction
    print("test_sana_profile_produces_saanaa_not_saroj: PASS")


def test_saroj_profile_produces_saroj_identity() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    profile = user_db.authenticate("Saroj", "2057")
    jarvis._set_user_profile(profile)
    jarvis._set_web_username(profile["nickname"])
    config = jarvis._build_config()
    addr = _address_clause(config.system_instruction)
    assert "'Saroj'" in addr
    assert "Your name is Sara" in config.system_instruction
    print("test_saroj_profile_produces_saroj_identity: PASS")


# ── 4: re-login on an already-connected session actually switches ───────

def test_relogin_on_connected_session_corrects_stale_identity() -> None:
    """The literal bug report: session already active as Saroj/Sara,
    Bandana/Sana/Radhe logs in next -- the injected correction must name
    the NEW identity, not repeat the stale one."""
    async def _run():
        loop = asyncio.get_event_loop()
        jarvis = _connected_jarvis(loop, "Saroj", "2057")
        assert jarvis._asst_name == "Sara"   # sanity: stale identity really is Saroj/Sara

        profile = user_db.authenticate("Bandana", "2060")
        jarvis._set_user_profile(profile)
        jarvis._set_web_username(profile["pronunciation"] or profile["nickname"])
        await asyncio.sleep(0.5)

        assert len(jarvis.session.sent) == 1
        prompt = jarvis.session.sent[0]
        assert "your name is Kanha" in prompt
        assert "address the user as Saanaa" in prompt
        assert "Address them as Saanaa" in prompt
        # The stale identity must not be what the correction tells Gemini to use.
        assert "your name is Sara" not in prompt
        assert "address the user as Saroj" not in prompt
    asyncio.run(_run())
    print("test_relogin_on_connected_session_corrects_stale_identity: PASS")


def test_relogin_reverse_direction_also_corrects() -> None:
    """Same fix, opposite direction: Sana's session already active,
    Saroj logs in next."""
    async def _run():
        loop = asyncio.get_event_loop()
        jarvis = _connected_jarvis(loop, "Sana", "2060")
        assert jarvis._asst_name == "Kanha"

        profile = user_db.authenticate("Saroj", "2057")
        jarvis._set_user_profile(profile)
        jarvis._set_web_username(profile["nickname"])
        await asyncio.sleep(0.5)

        prompt = jarvis.session.sent[0]
        assert "your name is Sara" in prompt
        assert "address the user as Saroj" in prompt
        assert "your name is Kanha" not in prompt
        assert "address the user as Saanaa" not in prompt
    asyncio.run(_run())
    print("test_relogin_reverse_direction_also_corrects: PASS")


# ── session isolation: Saroj -> Sana -> Saroj, no leakage ────────────────

def test_sequential_account_switches_never_leak_a_stale_identity() -> None:
    """Saroj -> Sana -> Saroj again -- each switch's correction reflects
    ONLY the incoming profile, never anything from two logins ago."""
    async def _run():
        loop = asyncio.get_event_loop()
        jarvis = _connected_jarvis(loop, "Saroj", "2057")

        sana = user_db.authenticate("Sana", "2060")
        jarvis._set_user_profile(sana)
        jarvis._set_web_username(sana["pronunciation"] or sana["nickname"])
        await asyncio.sleep(0.5)

        saroj_again = user_db.authenticate("Saroj", "2057")
        jarvis._set_user_profile(saroj_again)
        jarvis._set_web_username(saroj_again["nickname"])
        await asyncio.sleep(0.5)

        assert len(jarvis.session.sent) == 2
        second_prompt = jarvis.session.sent[1]
        assert "your name is Sara" in second_prompt
        assert "address the user as Saroj" in second_prompt
        # Nothing about Sana/Kanha should linger in the SECOND switch's message.
        assert "Kanha" not in second_prompt
        assert "Saanaa" not in second_prompt

        assert jarvis._user_profile["username"] == "saroj"
        assert jarvis._web_user_name == "Saroj"
    asyncio.run(_run())
    print("test_sequential_account_switches_never_leak_a_stale_identity: PASS")


# ── 5: fresh (first) connection unaffected — no correction clause ───────

def test_first_login_greeting_has_no_identity_switch_clause() -> None:
    """The normal, common case (no prior session on this connection) must
    stay exactly as before — a plain greeting, no 'a different user has
    just started this session' correction, since there's nothing stale to
    correct."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis.session = _FakeSession()
        profile = user_db.authenticate("Saroj", "2057")
        jarvis._set_user_profile(profile)
        from unittest.mock import patch
        with patch("main.pop_last_session", return_value=None):
            await jarvis._send_startup_briefing()   # default identity_switch=False
        prompt = jarvis.session.sent[0]
        assert "a different user has just started this session" not in prompt
    asyncio.run(_run())
    print("test_first_login_greeting_has_no_identity_switch_clause: PASS")


# ── 6: existing greeting lifecycle/trigger mechanics unaffected ─────────

def test_pending_web_greeting_path_still_uses_plain_greeting() -> None:
    """run()'s own post-connect _pending_web_greeting branch calls
    _send_startup_briefing() with no arguments -- must still default to
    identity_switch=False (this is a fresh connection, nothing stale)."""
    import inspect
    sig = inspect.signature(JarvisLive._send_startup_briefing)
    assert sig.parameters["identity_switch"].default is False
    print("test_pending_web_greeting_path_still_uses_plain_greeting: PASS")


def test_desktop_profile_resolution_still_never_triggers_a_switch_greeting() -> None:
    """_resolve_desktop_profile() must keep bypassing _set_web_username()'s
    greeting logic entirely (desktop's own _briefing_sent trigger is
    untouched) -- no session is connected yet at that point in run(), so
    nothing should be sent."""
    import json
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    import main

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sarana.db"
        cfg_path = Path(tmp) / "api_keys.json"
        cfg_path.write_text(
            json.dumps({"gemini_api_key": "x", "assistant_name": "SARANA", "user_name": "Saroj"}),
            encoding="utf-8",
        )
        with patch.object(user_db, "DB_PATH", db_path), patch.object(main, "API_CONFIG_PATH", cfg_path):
            user_db.init_db()
            jarvis = JarvisLive(HeadlessSurface())   # auto_start=True
            jarvis._resolve_desktop_profile()
            assert jarvis.session is None
            assert jarvis._pending_web_greeting is False
    print("test_desktop_profile_resolution_still_never_triggers_a_switch_greeting: PASS")


if __name__ == "__main__":
    test_sana_profile_produces_saanaa_not_saroj()
    test_saroj_profile_produces_saroj_identity()
    test_relogin_on_connected_session_corrects_stale_identity()
    test_relogin_reverse_direction_also_corrects()
    test_sequential_account_switches_never_leak_a_stale_identity()
    test_first_login_greeting_has_no_identity_switch_clause()
    test_pending_web_greeting_path_still_uses_plain_greeting()
    test_desktop_profile_resolution_still_never_triggers_a_switch_greeting()
    print("\nAll identity-switch regression tests passed.")
