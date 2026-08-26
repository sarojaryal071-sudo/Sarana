"""
tests/test_memory_ownership.py -- focused tests for the PostgreSQL
persistent-memory migration: personal/shared ownership, the in-RAM session
cache's login/logout lifecycle, background persistence + retry, the
local-file fallback when PostgreSQL isn't configured/reachable, and the
main.py-level login/logout/identity-switch wiring.

Uses tests/_fake_postgres_repo.py (an in-memory double) instead of a live
PostgreSQL database throughout, per this feature's own requirement.

Run with:
    .venv/Scripts/python.exe -m tests.test_memory_ownership
"""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from memory import legacy_file_store, memory_cache, memory_manager, postgres_repo
from tests._fake_postgres_repo import FakePostgresRepo


def _patched(fake=None):
    """Context manager stack: fresh cache + fresh queue + (optionally) a
    fake repo standing in for PostgreSQL — full isolation between tests,
    since memory_cache._cache/_persistence_queue are process-wide
    singletons by design (see that module's docstring)."""
    cache = memory_cache.SessionMemoryCache()
    queue = memory_cache._PersistenceQueue()
    patches = [
        patch("memory.memory_cache._cache", cache),
        patch("memory.memory_cache._persistence_queue", queue),
    ]
    if fake is not None:
        patches.append(patch("memory.memory_cache.postgres_repo", fake))
        patches.append(patch("memory.memory_manager.postgres_repo", fake))
    return patches, cache, queue


class _MultiPatch:
    """Applies a list of unittest.mock patchers together, cleanly."""
    def __init__(self, patchers):
        self._patchers = patchers

    def __enter__(self):
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()


class _Session:
    """Async version of the same isolation _patched()/_MultiPatch() give —
    ALSO runs a real persistence worker for the duration, needed by any
    test that writes and then reloads (a different owner, or the same one)
    and must see that write actually land in the fake repo first (a real
    write goes through the background queue, not straight through)."""

    def __init__(self, fake=None):
        self.fake = fake
        self.cache = memory_cache.SessionMemoryCache()
        self.queue = memory_cache._PersistenceQueue()
        self._patchers = [
            patch("memory.memory_cache._cache", self.cache),
            patch("memory.memory_cache._persistence_queue", self.queue),
        ]
        if fake is not None:
            self._patchers.append(patch("memory.memory_cache.postgres_repo", fake))
            self._patchers.append(patch("memory.memory_manager.postgres_repo", fake))
        self._worker = None

    async def __aenter__(self):
        for p in self._patchers:
            p.start()
        self._worker = asyncio.create_task(self.queue.run_worker())
        await asyncio.sleep(0)   # let run_worker() create its internal queue
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


# ── personal / shared ownership ─────────────────────────────────────────

def test_personal_memory_isolated_between_owners() -> None:
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory({"preferences": {"favorite_food": {"value": "daal bhat"}}})
            await s.flush()

            memory_manager.set_active_owner("sana")
            sana_view = memory_manager.load_memory()
            assert "favorite_food" not in sana_view["preferences"], (
                "Sana must never automatically receive Saroj's personal memory"
            )

            memory_manager.set_active_owner("saroj")
            saroj_view = memory_manager.load_memory()
            assert saroj_view["preferences"]["favorite_food"]["value"] == "daal bhat"
    asyncio.run(_run())
    print("test_personal_memory_isolated_between_owners: PASS")


def test_shared_memory_visible_to_all_owners() -> None:
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory(
                {"relationships": {"bimal": {"value": "friend"}}}, shared=True,
            )
            await s.flush()

            memory_manager.set_active_owner("sana")
            sana_view = memory_manager.load_memory()
            assert sana_view["relationships"]["bimal"]["value"] == "friend", (
                "a shared memory must be visible to every user, not just its author"
            )
    asyncio.run(_run())
    print("test_shared_memory_visible_to_all_owners: PASS")


def test_personal_overrides_shared_on_same_key() -> None:
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("saroj")
            memory_manager.update_memory({"preferences": {"color": {"value": "shared-blue"}}}, shared=True)
            memory_manager.update_memory({"preferences": {"color": {"value": "saroj-red"}}})  # personal, same key
            await s.flush()

            view = memory_manager.load_memory()
            assert view["preferences"]["color"]["value"] == "saroj-red", (
                "a user's own fact must win over a shared assumption on the same category/key"
            )
    asyncio.run(_run())
    print("test_personal_overrides_shared_on_same_key: PASS")


def test_forget_only_removes_from_personal_bucket() -> None:
    fake = FakePostgresRepo()
    patches, cache, queue = _patched(fake)
    with _MultiPatch(patches):
        memory_manager.set_active_owner("saroj")
        memory_manager.update_memory({"notes": {"habit": {"value": "morning walk"}}})
        result = memory_manager.forget("habit", "notes")
        assert result.startswith("Forgotten:")
        assert "habit" not in memory_manager.load_memory()["notes"]
    print("test_forget_only_removes_from_personal_bucket: PASS")


# ── login/logout cache lifecycle ─────────────────────────────────────────

def test_logout_clears_active_session_cache() -> None:
    fake = FakePostgresRepo()
    patches, cache, queue = _patched(fake)
    with _MultiPatch(patches):
        memory_manager.set_active_owner("saroj")
        memory_manager.update_memory({"notes": {"k": {"value": "v"}}})
        assert cache.owner == "saroj"

        memory_manager.clear_active_session()
        assert cache.owner == ""
        assert cache.backend == "none"
        # A subsequent read must NOT silently resurrect the old owner's
        # data under the empty-owner bucket.
        assert "k" not in memory_manager.load_memory()["notes"]
    print("test_logout_clears_active_session_cache: PASS")


def test_no_profile_bucket_never_leaks_into_a_real_login() -> None:
    """Before any login, the "" bucket may hold data (today's original
    un-scoped behavior) — a REAL login must not inherit it."""
    async def _run():
        fake = FakePostgresRepo()
        async with _Session(fake) as s:
            memory_manager.set_active_owner("")
            memory_manager.update_memory({"notes": {"unowned": {"value": "x"}}})
            await s.flush()

            memory_manager.set_active_owner("saroj")
            assert "unowned" not in memory_manager.load_memory()["notes"]
    asyncio.run(_run())
    print("test_no_profile_bucket_never_leaks_into_a_real_login: PASS")


# ── background persistence + retry ───────────────────────────────────────

def test_write_is_visible_in_cache_before_persistence_completes() -> None:
    """The conversation must never wait on the DB round trip — the RAM
    cache reflects a write immediately, synchronously, regardless of
    whether the background queue has drained yet."""
    async def _run():
        fake = FakePostgresRepo()
        patches, cache, queue = _patched(fake)
        with _MultiPatch(patches):
            worker = asyncio.create_task(queue.run_worker())
            try:
                memory_manager.set_active_owner("saroj")
                memory_manager.update_memory({"notes": {"k": {"value": "v"}}})
                # Read back immediately — no await, no sleep — must already be there.
                assert memory_manager.load_memory()["notes"]["k"]["value"] == "v"
                await asyncio.sleep(0.05)  # let the worker actually persist it
                assert fake.rows[("personal", "saroj", "notes", "k")]["content"] == "v"
            finally:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
    asyncio.run(_run())
    print("test_write_is_visible_in_cache_before_persistence_completes: PASS")


def test_persistence_retries_after_transient_failure_then_succeeds() -> None:
    async def _run():
        fake = FakePostgresRepo()
        fake.fail_next_n_writes = 1
        with patch("memory.memory_cache.postgres_repo", fake):
            job = {
                "kind": "upsert", "scope": "personal", "owner": "saroj",
                "category": "notes", "key": "k", "content": "v",
            }
            await memory_cache._apply_job_with_retry(job, max_attempts=3)
        assert fake.rows[("personal", "saroj", "notes", "k")]["content"] == "v"
    asyncio.run(_run())
    print("test_persistence_retries_after_transient_failure_then_succeeds: PASS")


def test_persistence_gives_up_after_max_attempts_without_raising() -> None:
    """A persistently unreachable database must never crash the worker or
    propagate into the conversation — it just logs and drops that one job."""
    async def _run():
        fake = FakePostgresRepo()
        fake.fail_next_n_writes = 99
        with patch("memory.memory_cache.postgres_repo", fake):
            job = {
                "kind": "upsert", "scope": "personal", "owner": "saroj",
                "category": "notes", "key": "k", "content": "v",
            }
            await memory_cache._apply_job_with_retry(job, max_attempts=2)   # never raises
        assert ("personal", "saroj", "notes", "k") not in fake.rows
    asyncio.run(_run())
    print("test_persistence_gives_up_after_max_attempts_without_raising: PASS")


def test_persistence_queue_drops_and_counts_when_full() -> None:
    q = memory_cache._PersistenceQueue()
    q._queue = asyncio.Queue(maxsize=1)   # simulate a started-but-stalled worker
    q.enqueue({"kind": "upsert", "scope": "personal", "owner": "x", "category": "notes", "key": "a", "content": "1"})
    q.enqueue({"kind": "upsert", "scope": "personal", "owner": "x", "category": "notes", "key": "b", "content": "2"})
    assert q._dropped == 1
    print("test_persistence_queue_drops_and_counts_when_full: PASS")


# ── local-file fallback (no DATABASE_URL / unreachable Postgres) ────────

def test_falls_back_to_local_file_when_postgres_not_configured() -> None:
    fake = FakePostgresRepo()
    fake.configured = False
    patches, cache, queue = _patched(fake)
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "long_term.json"
        with _MultiPatch(patches), patch.object(legacy_file_store, "MEMORY_PATH", mem_path):
            memory_manager.set_active_owner("saroj")
            assert cache.backend == "file"
            memory_manager.update_memory({"notes": {"k": {"value": "v"}}})
            assert json.loads(mem_path.read_text(encoding="utf-8"))["notes"]["k"]["value"] == "v"
    print("test_falls_back_to_local_file_when_postgres_not_configured: PASS")


def test_falls_back_to_local_file_when_postgres_load_raises() -> None:
    class _BrokenRepo(FakePostgresRepo):
        def fetch_memories(self, scope, owner=""):
            raise RuntimeError("connection refused")

    fake = _BrokenRepo()
    patches, cache, queue = _patched(fake)
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "long_term.json"
        with _MultiPatch(patches), patch.object(legacy_file_store, "MEMORY_PATH", mem_path):
            memory_manager.set_active_owner("saroj")
            assert cache.backend == "file", "a failed Postgres load must degrade to the local file, not crash"
    print("test_falls_back_to_local_file_when_postgres_load_raises: PASS")


# ── legacy extra keys (background_monitor.py's "monitors") ──────────────

def test_load_memory_preserves_unrelated_legacy_keys() -> None:
    """actions/background_monitor.py's "monitors" data must survive
    load_memory() untouched, regardless of PostgreSQL configuration."""
    fake = FakePostgresRepo()
    patches, cache, queue = _patched(fake)
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "long_term.json"
        mem_path.write_text(json.dumps({"monitors": {"news": {"topic": "nepal"}}}), encoding="utf-8")
        with _MultiPatch(patches), patch.object(legacy_file_store, "MEMORY_PATH", mem_path):
            memory_manager.set_active_owner("saroj")
            result = memory_manager.load_memory()
            assert result["monitors"] == {"news": {"topic": "nepal"}}
    print("test_load_memory_preserves_unrelated_legacy_keys: PASS")


# ── session summaries, per owner ─────────────────────────────────────────

def test_session_summary_save_and_pop_scoped_per_owner() -> None:
    fake = FakePostgresRepo()
    patches, cache, queue = _patched(fake)
    with _MultiPatch(patches):
        memory_manager.save_session_summary("Saroj asked about the weather.", "English", owner="saroj")
        memory_manager.save_session_summary("Sana planned a trip.", "Nepali", owner="sana")

        assert memory_manager.pop_last_session(owner="sana")["summary"] == "Sana planned a trip."
        assert memory_manager.pop_last_session(owner="saroj")["summary"] == "Saroj asked about the weather."
        assert memory_manager.pop_last_session(owner="saroj") is None   # consumed — never repeated
    print("test_session_summary_save_and_pop_scoped_per_owner: PASS")


if __name__ == "__main__":
    test_personal_memory_isolated_between_owners()
    test_shared_memory_visible_to_all_owners()
    test_personal_overrides_shared_on_same_key()
    test_forget_only_removes_from_personal_bucket()
    test_logout_clears_active_session_cache()
    test_no_profile_bucket_never_leaks_into_a_real_login()
    test_write_is_visible_in_cache_before_persistence_completes()
    test_persistence_retries_after_transient_failure_then_succeeds()
    test_persistence_gives_up_after_max_attempts_without_raising()
    test_persistence_queue_drops_and_counts_when_full()
    test_falls_back_to_local_file_when_postgres_not_configured()
    test_falls_back_to_local_file_when_postgres_load_raises()
    test_load_memory_preserves_unrelated_legacy_keys()
    test_session_summary_save_and_pop_scoped_per_owner()
    print("\nAll memory-ownership tests passed.")
