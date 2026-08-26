"""
tests/test_main_memory_lifecycle.py -- main.py-level wiring for the
PostgreSQL memory migration: login loads the right owner into the session
cache, logout discards it, and the identity-switch session-summary race
(self._session_owner must be frozen at connect time, not re-read lazily)
stays fixed.

Run with:
    .venv/Scripts/python.exe -m tests.test_main_memory_lifecycle
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import main
from core.headless_surface import HeadlessSurface
from main import JarvisLive
from memory import legacy_file_store, memory_cache, memory_manager
from users import user_db


def _isolated_memory(tmp_path: Path):
    return patch.object(legacy_file_store, "MEMORY_PATH", tmp_path / "long_term.json")


def _fresh_cache():
    return patch("memory.memory_cache._cache", memory_cache.SessionMemoryCache())


# ── login / logout wiring ────────────────────────────────────────────────

def test_set_user_profile_loads_that_user_into_the_memory_cache() -> None:
    with tempfile.TemporaryDirectory() as td, _isolated_memory(Path(td)), _fresh_cache():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        profile = user_db.authenticate("Saroj", "2057")
        jarvis._set_user_profile(profile)
        assert memory_manager.current_owner() == "saroj"
    print("test_set_user_profile_loads_that_user_into_the_memory_cache: PASS")


def test_clear_memory_session_discards_the_active_owner() -> None:
    with tempfile.TemporaryDirectory() as td, _isolated_memory(Path(td)), _fresh_cache():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        profile = user_db.authenticate("Saroj", "2057")
        jarvis._set_user_profile(profile)
        assert memory_manager.current_owner() == "saroj"

        jarvis._clear_memory_session()
        assert memory_manager.current_owner() == ""
    print("test_clear_memory_session_discards_the_active_owner: PASS")


def test_build_config_freezes_session_owner_from_the_active_profile() -> None:
    with tempfile.TemporaryDirectory() as td, _isolated_memory(Path(td)), _fresh_cache():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        profile = user_db.authenticate("Sana", "2060")
        jarvis._set_user_profile(profile)
        jarvis._build_config()
        assert jarvis._session_owner == "sana"
    print("test_build_config_freezes_session_owner_from_the_active_profile: PASS")


def test_session_owner_unaffected_by_a_later_profile_switch() -> None:
    """_session_owner is only reassigned inside _build_config() — a later
    _set_user_profile() call (e.g. mid-session, before a reconnect
    actually happens) must NOT retroactively change it."""
    with tempfile.TemporaryDirectory() as td, _isolated_memory(Path(td)), _fresh_cache():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        saroj = user_db.authenticate("Saroj", "2057")
        jarvis._set_user_profile(saroj)
        jarvis._build_config()
        assert jarvis._session_owner == "saroj"

        sana = user_db.authenticate("Sana", "2060")
        jarvis._set_user_profile(sana)   # the callback that WOULD trigger a reconnect
        assert jarvis._session_owner == "saroj", (
            "self._session_owner must stay frozen until the NEXT _build_config() call"
        )
    print("test_session_owner_unaffected_by_a_later_profile_switch: PASS")


# ── the identity-switch session-summary attribution race ────────────────

def _fake_summary_response(text: str):
    resp = MagicMock()
    resp.text = text
    return resp


def test_outgoing_session_summary_attributed_to_outgoing_owner_not_incoming() -> None:
    """Regression test for the exact race run()'s finally block guards
    against: by the time _save_session_summary() actually executes, the
    cache/self._user_profile may already reflect the NEXT user (an
    identity-switch reconnect fires _set_user_profile() BEFORE the
    outgoing connection's teardown code runs) — the summary must still be
    saved under the OUTGOING user, because run() passes that owner in
    explicitly rather than letting the method re-derive it lazily."""
    async def _run():
        with tempfile.TemporaryDirectory() as td, _isolated_memory(Path(td)), _fresh_cache():
            jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
            saroj = user_db.authenticate("Saroj", "2057")
            jarvis._set_user_profile(saroj)
            jarvis._build_config()   # freezes self._session_owner = "saroj"
            jarvis._session_log = ["User: hi", "SARANA: hello", "User: bye"]

            captured_owners = []

            def _fake_save_session_summary(summary, language="", owner=None):
                captured_owners.append(owner)

            with patch("google.genai.Client") as FakeClient, \
                 patch("main.save_session_summary", side_effect=_fake_save_session_summary), \
                 patch("main.owner_language", return_value="English"), \
                 patch("main._get_api_key", return_value="fake-key"):
                FakeClient.return_value.models.generate_content.return_value = _fake_summary_response(
                    "Saroj said hello and goodbye."
                )

                # Simulate run()'s finally block: capture the owner BEFORE
                # the incoming user's _set_user_profile() runs, exactly as
                # run() does with its `_outgoing_owner` local.
                outgoing_owner = jarvis._session_owner
                sana = user_db.authenticate("Sana", "2060")
                jarvis._set_user_profile(sana)   # cache/profile now point at Sana
                assert memory_manager.current_owner() == "sana"   # sanity: the race is real

                await jarvis._save_session_summary(outgoing_owner)

            assert captured_owners == ["saroj"], (
                f"expected the OUTGOING user's summary to be attributed to 'saroj', "
                f"got {captured_owners!r}"
            )
    asyncio.run(_run())
    print("test_outgoing_session_summary_attributed_to_outgoing_owner_not_incoming: PASS")


if __name__ == "__main__":
    test_set_user_profile_loads_that_user_into_the_memory_cache()
    test_clear_memory_session_discards_the_active_owner()
    test_build_config_freezes_session_owner_from_the_active_profile()
    test_session_owner_unaffected_by_a_later_profile_switch()
    test_outgoing_session_summary_attributed_to_outgoing_owner_not_incoming()
    print("\nAll main.py memory-lifecycle tests passed.")
