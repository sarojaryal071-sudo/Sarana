"""
tests/test_voice_reconnect.py — the follow-up fix: addressing/greeting now
switches correctly on a mid-session account change (see
tests/test_identity_switch.py), but voice (speech_config) and
system_instruction are BOTH fixed at Gemini Live connect time and have no
in-conversation equivalent to the identity_switch prompt correction — so
the voice kept sounding like whoever connected first, even after a
correct-sounding text/name switch.

Fix: a genuine account switch (different profile id) while a session is
already connected now requests a full reconnect
(_set_user_profile() -> self._reconnect_requested.set() ->
_watch_for_reconnect_request() raises _IdentityChanged -> run()'s except
block recognizes it and reconnects immediately, with no error print/
backoff). The fresh connection's _build_config() then picks up the new
profile's voice_preference/assistant_name/ADDRESS correctly, exactly like
any other fresh connection already did.

Run with:
    .venv/Scripts/python.exe -m tests.test_voice_reconnect
"""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import main
from core.headless_surface import HeadlessSurface
from main import JarvisLive, _IdentityChanged
from users import user_db
from tests.test_phase7_lifecycle import _FakeDashboard


def _isolated_desktop_config(tmp_dir: str, user_name: str) -> Path:
    """Never let a test read/depend on the real config/api_keys.json —
    _resolve_desktop_profile() (auto_start=True) reads this at run() time."""
    cfg_path = Path(tmp_dir) / "api_keys.json"
    cfg_path.write_text(
        json.dumps({"gemini_api_key": "x", "assistant_name": "SARANA", "user_name": user_name}),
        encoding="utf-8",
    )
    return cfg_path


# ── _set_user_profile()'s reconnect-trigger decision ──────────────────────

def test_set_user_profile_requests_reconnect_on_genuine_switch() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis.session = object()   # truthy stand-in for "a Gemini session is connected"
    jarvis._reconnect_requested = asyncio.Event()
    jarvis._user_profile = user_db.authenticate("Saroj", "2057")

    new_profile = user_db.authenticate("Sana", "2060")
    jarvis._set_user_profile(new_profile)

    assert jarvis._user_profile == new_profile
    assert jarvis._reconnect_requested.is_set()
    print("test_set_user_profile_requests_reconnect_on_genuine_switch: PASS")


def test_set_user_profile_does_not_reconnect_when_no_session_yet() -> None:
    """First-ever login (gate still waiting) — nothing to reconnect FROM."""
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    assert jarvis.session is None
    jarvis._reconnect_requested = asyncio.Event()

    profile = user_db.authenticate("Saroj", "2057")
    jarvis._set_user_profile(profile)

    assert not jarvis._reconnect_requested.is_set()
    print("test_set_user_profile_does_not_reconnect_when_no_session_yet: PASS")


def test_set_user_profile_does_not_reconnect_for_the_same_profile_resubmitted() -> None:
    """Re-firing a login for the SAME already-active account (e.g. a page
    refresh re-posting the same session) must not force a pointless
    reconnect."""
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    jarvis.session = object()
    jarvis._reconnect_requested = asyncio.Event()
    profile = user_db.authenticate("Saroj", "2057")
    jarvis._user_profile = profile

    jarvis._set_user_profile(dict(profile))   # same id, fresh dict instance

    assert not jarvis._reconnect_requested.is_set()
    print("test_set_user_profile_does_not_reconnect_for_the_same_profile_resubmitted: PASS")


def test_set_user_profile_safe_before_reconnect_event_exists() -> None:
    """_reconnect_requested is None until the first connection is
    established (see run()) — must never crash if a profile is set before
    that (e.g. _resolve_desktop_profile() at startup)."""
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._reconnect_requested is None
    jarvis._set_user_profile(user_db.authenticate("Saroj", "2057"))   # must not raise
    print("test_set_user_profile_safe_before_reconnect_event_exists: PASS")


# ── _watch_for_reconnect_request() ────────────────────────────────────────

def test_watch_for_reconnect_request_raises_identity_changed() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._reconnect_requested = asyncio.Event()
        jarvis._reconnect_requested.set()
        try:
            await asyncio.wait_for(jarvis._watch_for_reconnect_request(), timeout=1)
            assert False, "must raise _IdentityChanged"
        except _IdentityChanged:
            pass
    asyncio.run(_run())
    print("test_watch_for_reconnect_request_raises_identity_changed: PASS")


def test_watch_for_reconnect_request_waits_when_not_set() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._reconnect_requested = asyncio.Event()
        task = asyncio.create_task(jarvis._watch_for_reconnect_request())
        await asyncio.sleep(0.2)
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(_run())
    print("test_watch_for_reconnect_request_waits_when_not_set: PASS")


# ── end-to-end: a real reconnect actually happens, with the new voice ────

class _FakeLiveSession:
    def __init__(self, sent_messages):
        self._sent_messages = sent_messages   # shared across every connection this test makes

    async def send_client_content(self, turns, turn_complete=True):
        self._sent_messages.append(turns["parts"][0]["text"])

    async def send_realtime_input(self, media):
        pass

    async def receive(self):
        while True:
            await asyncio.sleep(3600)
            yield None   # never reached — keeps _receive_audio() parked, not erroring


class _FakeConnectCM:
    def __init__(self, sent_messages):
        self._sent_messages = sent_messages

    async def __aenter__(self):
        return _FakeLiveSession(self._sent_messages)

    async def __aexit__(self, *exc):
        return False


class _FakeLive:
    def __init__(self, recorder, sent_messages):
        self._recorder = recorder
        self._sent_messages = sent_messages

    def connect(self, *, model, config):
        self._recorder.append(config)
        return _FakeConnectCM(self._sent_messages)


class _FakeGenaiClient:
    def __init__(self, recorder, sent_messages, *a, **kw):
        self.aio = type("Aio", (), {"live": _FakeLive(recorder, sent_messages)})()


def test_account_switch_triggers_a_real_reconnect_with_the_new_voice() -> None:
    """The actual end-to-end proof: connect once as Saroj (Female voice ->
    Kore), switch to Sana (Male voice -> Charon) while still connected,
    and confirm a SECOND genai.Client().aio.live.connect() call happens
    with the new profile's voice/assistant baked into its config — not
    just a corrected greeting text."""
    async def _run():
        recorder = []
        sent_messages = []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        # auto_start=True (desktop's default) so the connect loop starts
        # immediately -- simulating "already logged in as Saroj" is just
        # pre-setting the profile before run() begins, same as
        # _resolve_desktop_profile() would have.
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

            assert len(recorder) == 1, "first connection must have happened"
            first_voice = recorder[0].speech_config.voice_config.prebuilt_voice_config.voice_name
            assert first_voice == "Kore", "Saroj's voice_preference (Female) -> Kore"
            assert len(sent_messages) == 1, "the first connection's own greeting must have fired"

            # A short (< 3 turn) leftover conversation from Saroj's session
            # — too short to summarize, but must still not leak into Sana's.
            jarvis._session_log = ["User: hi", "Sara: hello"]

            # Sana logs in on this SAME already-connected session — profile
            # callback first, username callback second, matching dashboard/
            # server.py's actual (fixed) ordering.
            new_profile = user_db.authenticate("Sana", "2060")
            jarvis._set_user_profile(new_profile)
            jarvis._set_web_username(new_profile["pronunciation"] or new_profile["nickname"])

            await asyncio.sleep(0.8)

            assert len(recorder) == 2, "a genuine account switch must trigger a real reconnect"
            second_voice = recorder[1].speech_config.voice_config.prebuilt_voice_config.voice_name
            assert second_voice == "Charon", "Sana's voice_preference (Male) -> Charon"

            second_instr = recorder[1].system_instruction
            assert "Your name is Kanha" in second_instr
            assert "'Saanaa'" in second_instr

            assert jarvis._session_log == [], (
                "the outgoing account's short activity log must not carry into the new account's session"
            )

            # The core complaint this fixes: the switch must not go silent
            # ("still waiting for me to start the conversation") — a second
            # greeting must actually go out, on the NEW (reconnected)
            # session, addressed to Sana/Saanaa.
            assert len(sent_messages) == 2, (
                "the account switch must still produce its own greeting, not silence"
            )
            assert "Saanaa" in sent_messages[1]

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_account_switch_triggers_a_real_reconnect_with_the_new_voice: PASS")


def test_same_account_relogin_greets_every_time_without_reconnecting() -> None:
    """The other half of the user's report: logging out and back in as the
    SAME account repeatedly (e.g. five times in two minutes) must not go
    silent after the first time — every login gets its own greeting, with
    no reconnect needed (the session/voice/identity are already correct)."""
    async def _run():
        recorder = []
        sent_messages = []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        jarvis = JarvisLive(HeadlessSurface())   # auto_start=True — desktop's default
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
            assert len(sent_messages) == 1, "first login's own greeting"

            # "Logged out and back in" 4 more times, same account each time
            # — logout itself is frontend-only (see App.jsx's handleLogout);
            # from the backend's point of view this is just Saroj's
            # /login/username firing again, same as any other re-login.
            for i in range(4):
                same_profile = user_db.authenticate("Saroj", "2057")
                jarvis._set_user_profile(same_profile)
                jarvis._set_web_username(same_profile["nickname"])
                await asyncio.sleep(0.5)

            assert len(recorder) == 1, "same account, same voice/identity — never needs a reconnect"
            assert len(sent_messages) == 5, (
                f"expected a fresh greeting for every one of the 5 logins, got {len(sent_messages)}"
            )
            for msg in sent_messages:
                assert "Saroj" in msg

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_same_account_relogin_greets_every_time_without_reconnecting: PASS")


if __name__ == "__main__":
    test_set_user_profile_requests_reconnect_on_genuine_switch()
    test_set_user_profile_does_not_reconnect_when_no_session_yet()
    test_set_user_profile_does_not_reconnect_for_the_same_profile_resubmitted()
    test_set_user_profile_safe_before_reconnect_event_exists()
    test_watch_for_reconnect_request_raises_identity_changed()
    test_watch_for_reconnect_request_waits_when_not_set()
    test_account_switch_triggers_a_real_reconnect_with_the_new_voice()
    test_same_account_relogin_greets_every_time_without_reconnecting()
    print("\nAll voice-reconnect tests passed.")
