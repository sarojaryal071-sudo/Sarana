"""
memory/memory_cache.py -- the in-RAM session memory cache sitting between
main.py's conversation loop and memory/postgres_repo.py's persistent
PostgreSQL storage. This is the piece that keeps PostgreSQL OUT of
SARANA's hot conversational path:

                    Login (_set_user_profile())
                          |
              memory_manager.set_active_owner(owner)
                          |
              SessionMemoryCache.load(owner)   <-- the ONLY moment a normal
                          |                        login hits Postgres:
                 RAM: personal[owner] + shared     two SELECTs, once
                          |
      every load_memory()/update_memory() call during the WHOLE session
      (system-instruction build, save_memory tool calls, proactive/
      background-monitor context, startup-briefing identity lookup)
      reads/writes THIS in-RAM dict — zero DB round trips
                          |
      a write is ALSO handed to _PersistenceQueue below — the caller
      (main.py's _execute_tool(), the conversation itself) never waits on
      the DB write to finish, and a temporarily unreachable Postgres never
      stalls or breaks the conversation (see _apply_job_with_retry())
                          |
                    Logout (_reset on username logout)
                          |
              memory_manager.clear_active_session() — cache discarded;
              the NEXT login's load() starts clean, so no session can ever
              inherit a leftover cache from a previous user

Ownership model
----------------
- scope='personal': owned by exactly one user (the canonical
  users/user_db.py `username`, e.g. "saroj", "sana") — loaded only when
  that user is the active session.
- scope='shared': visible to every user — loaded on every login regardless
  of who's logging in, merged UNDER personal facts (a user's own fact wins
  over a shared assumption on the same category/key). "Shared" is a
  VISIBILITY setting, not an ownership one: a shared fact still has a
  SUBJECT (whoever told SARANA it), carried as that entry's "subject" key
  so "Bimal is my friend" (told by Saroj) is never flattened into an
  anonymous fact that a different reader (e.g. Sana) could misread as
  being about them. See postgres_repo.fetch_memories()'s docstring for
  exactly how this is stored/returned.
- owner="" (no profile resolved — an unrecognized/unset name on either
  interface) is its own personal bucket, exactly matching the ORIGINAL
  pre-migration behavior of a single, un-scoped memory store — no
  regression for a session that never identifies a user.

Fallback
--------
If PostgreSQL isn't configured (no DATABASE_URL — the common local/desktop
case) or a call to it fails, this cache transparently degrades to
memory/legacy_file_store.py's local JSON file — the exact mechanism SARANA
used before this migration. The failure is always printed (never silent),
per the "make failures visible in logs" requirement.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime

from memory import legacy_file_store, postgres_repo

_MANAGED_CATEGORIES = (
    "identity", "preferences", "projects", "relationships", "wishes", "notes",
)


def _empty() -> dict:
    return {cat: {} for cat in _MANAGED_CATEGORIES}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class SessionMemoryCache:
    """One instance per process (see the module-level `_cache` singleton
    below) — this brain serves one active logged-in identity at a time, so
    a single cache object (swapped on login/logout) is the correct shape,
    not a per-JarvisLive-instance or per-connection cache."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.owner: str = ""
        self.personal: dict = _empty()
        self.shared: dict = _empty()
        self.backend: str = "none"   # "postgres" | "file" | "none" (before first load)

    # ── login / logout ───────────────────────────────────────────────────

    def load(self, owner: str) -> None:
        """Call once per login. Loads `owner`'s personal memories + the
        shared set. owner="" loads the "no profile resolved" personal
        bucket (see module docstring)."""
        owner = (owner or "").strip()
        if postgres_repo.is_configured():
            try:
                postgres_repo.init_schema()
                personal = postgres_repo.fetch_memories("personal", owner) if owner else _empty()
                # No owner filter — see fetch_memories()'s docstring: for
                # scope='shared', `owner` means the fact's SUBJECT (who it's
                # about), not "who may see it" — every shared row is loaded
                # for every login regardless of whose subject it is.
                shared = postgres_repo.fetch_memories("shared")
                with self._lock:
                    self.owner, self.personal, self.shared, self.backend = owner, personal, shared, "postgres"
                n_personal = sum(len(v) for v in personal.values())
                n_shared = sum(len(v) for v in shared.values())
                print(
                    f"[Memory][Postgres] Session cache loaded for "
                    f"'{owner or '(no profile)'}' ({n_personal} personal, {n_shared} shared facts)."
                )
                return
            except Exception as e:
                print(f"[Memory][Postgres] Load failed ({e}) — falling back to local file for this session.")
        with self._lock:
            self.owner = owner
            self.personal = legacy_file_store.load_memory()
            self.shared = _empty()
            self.backend = "file"

    def clear(self) -> None:
        """Call on logout — discards the cache. Never touches Postgres or
        the local file themselves, only this process's RAM copy."""
        with self._lock:
            self.owner = ""
            self.personal = _empty()
            self.shared = _empty()
            self.backend = "none"
        print("[Memory] Session cache cleared (logout).")

    # ── reads ─────────────────────────────────────────────────────────────

    def merged(self) -> dict:
        """Personal facts override shared facts on a category/key
        collision. In "file" (no-Postgres) backend there is no separate
        shared bucket — personal alone IS the whole store, exactly as
        before this migration."""
        if self.backend == "none":
            # Safety net: main.py's run() always calls set_active_owner("")
            # before anything else can reach this, but any other caller
            # that somehow reads memory before that (e.g. a script, a
            # future entry point) gets the "no profile" bucket loaded
            # lazily here instead of silently seeing an empty store.
            self.load("")
        with self._lock:
            # Restricted to the fixed managed-category set in BOTH
            # backends — self.personal in "file" backend is the raw
            # legacy-file dict, which also carries unrelated keys like
            # "sessions"/"monitors" (see legacy_file_store.load_memory());
            # passing those through here would silently corrupt them (e.g.
            # dict(a_list) on "sessions"'s list value). Those extra keys
            # are handled separately, unmodified, by
            # memory_manager._read_legacy_extras().
            if self.backend != "postgres":
                return {cat: dict(self.personal.get(cat, {})) for cat in _MANAGED_CATEGORIES}
            out: dict = {}
            for cat in _MANAGED_CATEGORIES:
                merged_cat = dict(self.shared.get(cat, {}))
                merged_cat.update(self.personal.get(cat, {}))
                out[cat] = merged_cat
            return out

    # ── writes ───────────────────────────────────────────────────────────

    def update(
        self, memory_update: dict, *, shared: bool = False, importance: int = 3,
        entities: list | None = None, event_date: str | None = None, source: str = "",
    ) -> dict:
        """memory_update: {category: {key: value_or_{"value": value}}} —
        the exact shape main.py's save_memory tool and remember() have
        always used. Updates the RAM cache immediately; a changed leaf is
        then persisted via the background queue (Postgres backend) or
        written straight back to the local file (file backend — a local
        write is cheap enough not to need queuing)."""
        if not isinstance(memory_update, dict) or not memory_update:
            return self.merged()
        if self.backend == "none":
            self.load("")   # see merged()'s identical safety net

        use_shared = shared and self.backend == "postgres"
        # The SUBJECT of this fact — who told SARANA it / who it's about —
        # is always the current session's owner, regardless of scope. For
        # scope='personal' this was already true (the owner IS the subject
        # by definition). For scope='shared' this is the fix for the
        # attribution-loss bug: a shared fact used to be written with no
        # owner at all, so "Bimal is my friend" became an anonymous
        # "Bimal is [someone's] friend" the moment anyone else read it back
        # — see fetch_memories()'s docstring and format_memory_for_prompt().
        subject = self.owner
        changed: list[tuple[str, str, str]] = []
        with self._lock:
            target = self.shared if use_shared else self.personal
            for category, entries in memory_update.items():
                if not isinstance(entries, dict):
                    continue
                for key, value in entries.items():
                    raw = value["value"] if isinstance(value, dict) else value
                    if raw is None or (isinstance(raw, str) and not raw.strip()):
                        continue
                    new_val = str(raw)
                    if len(new_val) > legacy_file_store.MAX_VALUE_LENGTH:
                        new_val = new_val[: legacy_file_store.MAX_VALUE_LENGTH].rstrip() + "…"
                    bucket = target.setdefault(category, {})
                    existing = bucket.get(key)
                    # A shared write must still land even if the VALUE
                    # string happens to be unchanged, when the SUBJECT is
                    # different (e.g. Sana later says the same words about
                    # a different "Bimal" fact) — otherwise the old
                    # subject would silently stick to the new speaker.
                    existing_subject = existing.get("subject") if isinstance(existing, dict) else None
                    if (
                        isinstance(existing, dict)
                        and existing.get("value") == new_val
                        and (not use_shared or existing_subject == subject)
                    ):
                        continue
                    entry = {"value": new_val, "updated": _today()}
                    if use_shared and subject:
                        entry["subject"] = subject
                    bucket[key] = entry
                    changed.append((category, key, new_val))
            personal_snapshot = self.personal

        if not changed:
            return self.merged()

        print(f"[Memory] Saved: {[f'{c}/{k}' for c, k, _ in changed]}")
        if self.backend == "postgres":
            scope = "shared" if use_shared else "personal"
            for category, key, val in changed:
                _persistence_queue.enqueue({
                    "kind": "upsert", "scope": scope, "owner": subject,
                    "category": category, "key": key, "content": val,
                    "importance": importance, "entities": entities,
                    "event_date": event_date, "source": source,
                })
        else:
            legacy_file_store.save_memory(personal_snapshot)
        return self.merged()

    def forget(self, key: str, category: str = "notes") -> str:
        with self._lock:
            bucket = self.personal.get(category, {})
            existed = key in bucket
            if existed:
                del bucket[key]
                self.personal[category] = bucket
            personal_snapshot = self.personal
            backend = self.backend
            owner = self.owner

        if not existed:
            return f"Not found: {category}/{key}"

        if backend == "postgres":
            _persistence_queue.enqueue({
                "kind": "delete", "scope": "personal", "owner": owner,
                "category": category, "key": key,
            })
        else:
            legacy_file_store.save_memory(personal_snapshot)
        return f"Forgotten: {category}/{key}"


# ── background persistence queue ─────────────────────────────────────────

class _PersistenceQueue:
    """Bounded background write queue. enqueue() never blocks and never
    itself talks to Postgres — a single consumer task (start_worker(),
    run for the whole process lifetime — see main.py's run()) drains it.
    Mirrors the exact bounded-queue + drop-and-log-on-full idiom already
    used elsewhere in this codebase (dashboard/server.py's phone-audio
    queue) rather than inventing a new pattern."""

    _MAXSIZE = 500
    _MAX_ATTEMPTS = 5

    def __init__(self) -> None:
        self._queue: asyncio.Queue | None = None
        self._dropped = 0

    def enqueue(self, job: dict) -> None:
        if self._queue is None:
            # The worker hasn't started yet (main.py's run() starts it
            # right away, so this is only possible for a write that
            # somehow happens before that — vanishingly rare, e.g. a
            # direct free-function call outside the normal lifecycle).
            # Still must not silently lose the fact: a single one-off task
            # here, not the steady-state "one task per write" pattern the
            # "no uncontrolled task for every write" requirement warns
            # against — the steady state always goes through the one
            # shared queue+worker below.
            try:
                asyncio.get_event_loop().create_task(_apply_job_with_retry(job))
            except RuntimeError:
                print(f"[Memory][Postgres] No event loop available — write not persisted: {job}")
            return
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self._dropped += 1
            print(
                f"[Memory][Postgres] Persistence queue full — dropped a write "
                f"(total dropped this process: {self._dropped}). The fact is still "
                f"in this session's RAM cache but will NOT survive a restart "
                f"until it is saved again: {job}"
            )

    async def run_worker(self) -> None:
        self._queue = asyncio.Queue(maxsize=self._MAXSIZE)
        while True:
            job = await self._queue.get()
            try:
                await _apply_job_with_retry(job, self._MAX_ATTEMPTS)
            finally:
                self._queue.task_done()


async def _apply_job_with_retry(job: dict, max_attempts: int = 5) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            await asyncio.to_thread(_apply_job, job)
            return
        except Exception as e:
            print(f"[Memory][Postgres] Write failed (attempt {attempt}/{max_attempts}): {e} — job={job}")
            if attempt >= max_attempts:
                print(
                    f"[Memory][Postgres] Giving up on this write after {max_attempts} attempts. "
                    f"The fact remains in the current session's RAM cache but will NOT be "
                    f"durable until Postgres is reachable again and the fact is saved once more: {job}"
                )
                return
            await asyncio.sleep(min(2 ** attempt, 30))


def _apply_job(job: dict) -> None:
    kind = job.get("kind")
    if kind == "upsert":
        postgres_repo.upsert_memory(
            job["scope"], job["owner"], job["category"], job["key"], job["content"],
            importance=job.get("importance") or 3, entities=job.get("entities"),
            event_date=job.get("event_date"), source=job.get("source") or "",
        )
    elif kind == "delete":
        postgres_repo.delete_memory(job["scope"], job["owner"], job["category"], job["key"])
    else:
        print(f"[Memory][Postgres] Unknown persistence job kind: {kind!r}")


# ── module-level singletons ──────────────────────────────────────────────
# One active identity at a time, one cache, one persistence queue — see
# this module's docstring for why a singleton is the right shape here.
_persistence_queue = _PersistenceQueue()
_cache = SessionMemoryCache()


def start_worker() -> asyncio.Task:
    """Call once, from within a running event loop (main.py's run()) —
    starts the single background persistence consumer for the whole
    process lifetime."""
    return asyncio.create_task(_persistence_queue.run_worker())
