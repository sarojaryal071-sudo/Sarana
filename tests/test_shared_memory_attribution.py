"""
tests/test_shared_memory_attribution.py -- regression tests for the
shared-memory attribution-loss bug: a shared fact must retain WHO it's
about (the subject — whoever told SARANA it), never collapse into an
anonymous fact that a different reader could misattribute to themselves.

Root cause (see memory/memory_cache.py's update() before this fix):
SessionMemoryCache.update() wrote every shared fact with owner="" —
scope='shared' already controls VISIBILITY (loaded for every login), but
the code was ALSO using owner (which should mean "subject") to mean
"nobody in particular", discarding who the fact was actually about. Fixed
by writing owner=<current session owner> for shared facts too (the
subject), and having postgres_repo.fetch_memories()/format_memory_for_prompt()
surface it back out as each entry's "subject" key / a "[fact about X]"
tag, instead of filtering shared reads by owner="" (which no longer holds
since owner now varies per shared row's real subject).

Uses tests/_fake_postgres_repo.py instead of a live PostgreSQL database.

Run with:
    .venv/Scripts/python.exe -m tests.test_shared_memory_attribution
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive
from memory import legacy_file_store, memory_cache, memory_manager
from tests._fake_postgres_repo import FakePostgresRepo
from users import user_db


class _Session:
    """Fresh, isolated cache + a real draining persistence worker for the
    duration of the block — see tests/test_memory_ownership.py's identical
    helper for the full rationale (a write must actually reach the fake
    repo before a same-owner reload can see it)."""

    def __init__(self, fake: FakePostgresRepo):
        self.fake = fake
        self.cache = memory_cache.SessionMemoryCache()
        self.queue = memory_cache._PersistenceQueue()
        self._patchers = [
            patch("memory.memory_cache._cache", self.cache),
            patch("memory.memory_cache._persistence_queue", self.queue),
            patch("memory.memory_cache.postgres_repo", fake),
            patch("memory.memory_manager.postgres_repo", fake),
        ]
        self._worker = None

    async def __aenter__(self):
        for p in self._patchers:
            p.start()
        self._worker = asyncio.create_task(self.queue.run_worker())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc):
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        for p in reversed(self._patchers):
            p.stop()

    @staticmethod
    async def flush() -> None:
        await asyncio.sleep(0.05)


# ── 1/2: personal memory privacy is untouched by this fix ───────────────

def test_saroj_personal_memory_invisible_to_sana() -> None:
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory({"identity": {"nickname": {"value": "Saru"}}})
            await s.flush()

            memory_manager.set_active_owner("sana")
            assert "nickname" not in memory_manager.load_memory()["identity"]
    asyncio.run(_run())
    print("test_saroj_personal_memory_invisible_to_sana: PASS")


def test_sana_personal_memory_invisible_to_saroj() -> None:
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("sana")
            memory_manager.update_memory({"identity": {"nickname": {"value": "Sanu"}}})
            await s.flush()

            memory_manager.set_active_owner("saroj")
            assert "nickname" not in memory_manager.load_memory()["identity"]
    asyncio.run(_run())
    print("test_sana_personal_memory_invisible_to_saroj: PASS")


# ── 3/4/5: the actual attribution-loss bug, relationships example ───────

def test_saroj_creates_shared_relationship_bimal_is_my_friend() -> None:
    """The exact scenario reported: Saroj tells SARANA 'Bimal is my
    friend', saved as shared. The stored row must retain Saroj as the
    subject — not owner=''."""
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory(
                {"relationships": {"friend_name": {"value": "Bimal"}}}, shared=True,
            )
            await s.flush()

            row = fake.rows[("shared", "saroj", "relationships", "friend_name")]
            assert row["content"] == "Bimal"
    asyncio.run(_run())
    print("test_saroj_creates_shared_relationship_bimal_is_my_friend: PASS")


def test_saroj_reading_own_shared_fact_sees_correct_subject() -> None:
    """Saroj asking 'who is Bimal?' — the memory context handed to Gemini
    must mark the fact as being about Saroj (from which "your friend" is a
    natural, correct paraphrase — actual NL phrasing is Gemini's job, not
    tested here; what IS tested is that the underlying data unambiguously
    identifies the right subject)."""
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory(
                {"relationships": {"friend_name": {"value": "Bimal"}}}, shared=True,
            )
            await s.flush()

            memory_manager.set_active_owner("saroj")   # reload, as a fresh login would
            merged = memory_manager.load_memory()
            entry = merged["relationships"]["friend_name"]
            assert entry["value"] == "Bimal"
            assert entry["subject"] == "saroj"

            prompt_text = memory_manager.format_memory_for_prompt(merged)
            assert "Bimal" in prompt_text
            assert "[fact about saroj]" in prompt_text.lower()
    asyncio.run(_run())
    print("test_saroj_reading_own_shared_fact_sees_correct_subject: PASS")


def test_sana_reading_saroj_shared_fact_sees_saroj_not_herself() -> None:
    """The literal bug report: Sana asks 'who is Bimal?' after SAROJ saved
    it as shared. The memory context must identify Bimal as SAROJ's
    friend — never silently become Sana's own fact."""
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory(
                {"relationships": {"friend_name": {"value": "Bimal"}}}, shared=True,
            )
            await s.flush()

            memory_manager.set_active_owner("sana")
            merged = memory_manager.load_memory()
            entry = merged["relationships"]["friend_name"]
            assert entry["value"] == "Bimal"
            assert entry["subject"] == "saroj", (
                "Bimal must remain attributed to Saroj even when Sana is the current user"
            )
            assert entry["subject"] != "sana"

            prompt_text = memory_manager.format_memory_for_prompt(merged)
            assert "[fact about saroj]" in prompt_text.lower()
            assert "[fact about sana]" not in prompt_text.lower()
    asyncio.run(_run())
    print("test_sana_reading_saroj_shared_fact_sees_saroj_not_herself: PASS")


# ── 6: shared events (not just relationships) keep their subject ────────

def test_shared_event_created_by_saroj_stays_attributed_when_sana_reads_it() -> None:
    """'Yesterday I visited Pashupatinath...' saved as shared by Saroj —
    Sana must see it as Saroj's experience, not her own."""
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory(
                {"notes": {"pashupatinath_visit": {
                    "value": "Saroj visited Pashupatinath with his family and was very happy",
                }}},
                shared=True,
            )
            await s.flush()

            memory_manager.set_active_owner("sana")
            merged = memory_manager.load_memory()
            entry = merged["notes"]["pashupatinath_visit"]
            assert entry["subject"] == "saroj"
            prompt_text = memory_manager.format_memory_for_prompt(merged)
            assert "Pashupatinath" in prompt_text
            assert "[fact about saroj]" in prompt_text.lower()
    asyncio.run(_run())
    print("test_shared_event_created_by_saroj_stays_attributed_when_sana_reads_it: PASS")


# ── 7: existing (unattributed / legacy) shared memories still load ──────

def test_legacy_shared_memory_with_no_subject_still_loads_cleanly() -> None:
    """A shared fact written before this fix (owner='', e.g. via the
    long_term.json migration) must still load and render — just without a
    subject tag, exactly as it always has."""
    async def _run():
        fake = FakePostgresRepo()
        fake.rows[("shared", "", "notes", "household_wifi")] = {
            "content": "The wifi password is on the fridge", "importance": 3,
            "entities": [], "event_date": None, "source": "migration:long_term.json",
            "updated_at": "2026-08-24",
        }
        async with _Session(fake) as s:
            memory_manager.set_active_owner("sana")
            merged = memory_manager.load_memory()
            entry = merged["notes"]["household_wifi"]
            assert entry["value"] == "The wifi password is on the fridge"
            assert "subject" not in entry

            prompt_text = memory_manager.format_memory_for_prompt(merged)
            assert "wifi password" in prompt_text
            # The header itself explains the "[fact about X]" tag in the
            # abstract (as instructional text) — that's expected and must
            # not be confused with an actual tag. What must NOT happen is
            # this specific unattributed fact's own line growing a tag.
            fact_line = next(line for line in prompt_text.splitlines() if "wifi password" in line)
            assert "[fact about" not in fact_line.lower()
    asyncio.run(_run())
    print("test_legacy_shared_memory_with_no_subject_still_loads_cleanly: PASS")


# ── 8: existing cache mechanics (write-visible-immediately, personal
# overrides shared, login/logout) remain intact ──────────────────────────

def test_personal_still_overrides_shared_on_same_key_after_fix() -> None:
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory({"preferences": {"color": {"value": "shared-blue"}}}, shared=True)
            memory_manager.update_memory({"preferences": {"color": {"value": "saroj-red"}}})
            await s.flush()

            merged = memory_manager.load_memory()
            assert merged["preferences"]["color"]["value"] == "saroj-red"
            assert "subject" not in merged["preferences"]["color"]   # personal entries never carry one
    asyncio.run(_run())
    print("test_personal_still_overrides_shared_on_same_key_after_fix: PASS")


def test_write_still_visible_in_cache_before_persistence_completes() -> None:
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory(
                {"relationships": {"friend_name": {"value": "Bimal"}}}, shared=True,
            )
            # No flush — must already be correct in RAM, subject included.
            entry = memory_manager.load_memory()["relationships"]["friend_name"]
            assert entry["value"] == "Bimal"
            assert entry["subject"] == "saroj"
    asyncio.run(_run())
    print("test_write_still_visible_in_cache_before_persistence_completes: PASS")


def test_logout_still_clears_cache_after_fix() -> None:
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory({"notes": {"k": {"value": "v"}}})
            memory_manager.clear_active_session()
            assert memory_manager.current_owner() == ""
    asyncio.run(_run())
    print("test_logout_still_clears_cache_after_fix: PASS")


# ── 9: desktop and web still funnel through the same mechanism ──────────

def test_desktop_profile_resolution_uses_the_same_cache_as_web_login() -> None:
    """_resolve_desktop_profile() (desktop's own login-equivalent) must
    still end up calling the exact same set_active_owner() path a web
    /login/username does (see main.py's _set_user_profile()) — no
    separate desktop/web memory mechanism exists."""
    import json as _json

    fake = FakePostgresRepo()
    with tempfile.TemporaryDirectory() as tmp, \
         patch("memory.memory_cache._cache", memory_cache.SessionMemoryCache()), \
         patch("memory.memory_cache.postgres_repo", fake), \
         patch("memory.memory_manager.postgres_repo", fake):
        db_path = Path(tmp) / "sarana.db"
        cfg_path = Path(tmp) / "api_keys.json"
        cfg_path.write_text(
            _json.dumps({"gemini_api_key": "x", "assistant_name": "SARANA", "user_name": "Saroj"}),
            encoding="utf-8",
        )
        with patch.object(user_db, "DB_PATH", db_path):
            user_db.init_db()
            import main as main_module
            with patch.object(main_module, "API_CONFIG_PATH", cfg_path):
                jarvis = JarvisLive(HeadlessSurface())   # auto_start=True (desktop)
                jarvis._resolve_desktop_profile()
        assert memory_manager.current_owner() == "saroj"
    print("test_desktop_profile_resolution_uses_the_same_cache_as_web_login: PASS")


if __name__ == "__main__":
    test_saroj_personal_memory_invisible_to_sana()
    test_sana_personal_memory_invisible_to_saroj()
    test_saroj_creates_shared_relationship_bimal_is_my_friend()
    test_saroj_reading_own_shared_fact_sees_correct_subject()
    test_sana_reading_saroj_shared_fact_sees_saroj_not_herself()
    test_shared_event_created_by_saroj_stays_attributed_when_sana_reads_it()
    test_legacy_shared_memory_with_no_subject_still_loads_cleanly()
    test_personal_still_overrides_shared_on_same_key_after_fix()
    test_write_still_visible_in_cache_before_persistence_completes()
    test_logout_still_clears_cache_after_fix()
    test_desktop_profile_resolution_uses_the_same_cache_as_web_login()
    print("\nAll shared-memory attribution tests passed.")
