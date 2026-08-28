"""
memory/migrate_long_term.py -- one-time import of the existing local
memory/long_term.json file into PostgreSQL, so the PostgreSQL migration
never throws away memories that already existed.

Why "shared" and not per-user
------------------------------
The pre-migration store (memory/legacy_file_store.py, formerly the whole
of memory/memory_manager.py) was always a SINGLE GLOBAL bucket — it never
recorded which of Saroj/Sana/anyone else a given fact belonged to. There
is no reliable automatic way to split it back apart after the fact, and
guessing wrong would be worse than not guessing at all (it could hide a
real fact from the person it's actually about). The safe, honest choice
made here: every migrated fact becomes a SHARED memory — visible to every
user, exactly as available as it was before this migration (nothing
hidden, nothing lost), rather than silently or incorrectly assigned to
one user. Anyone can later be re-saved as personal by simply being told
again (save_memory naturally overwrites by category/key).

Idempotency
-----------
Run this as many times as you like — including automatically, once, on
every process startup (see main.py's run()) — it never imports twice.
A single marker row (scope='shared', owner='', category='_migration',
key='long_term_json_v1') records the outcome as JSON: {"imported": N,
"source_found": bool}. Its presence short-circuits the whole function
ONLY when source_found is true — see _migration_state()'s docstring for
why "the marker exists" alone is deliberately NOT enough to skip.

Usage
-----
Automatic: main.py's run() calls migrate_if_needed() once at startup,
whenever PostgreSQL is configured — cheap no-op after the first real run.

Manual (e.g. to migrate against a database from a machine that isn't
running the app): `python -m memory.migrate_long_term`
"""
from __future__ import annotations

import json

from memory import legacy_file_store, postgres_repo

_MARKER_CATEGORY = "_migration"
_MARKER_KEY = "long_term_json_v1"
_MANAGED_CATEGORIES = (
    "identity", "preferences", "projects", "relationships", "wishes", "notes",
)


def _migration_state(shared_memories: dict) -> dict | None:
    """Parses the marker row's content (see module docstring), or None if
    no marker exists yet, or if it can't be parsed as the expected JSON
    shape — including the OLD, buggy marker format this module used to
    write (a bare "migrated" string, with no record of whether the source
    file actually existed). Either case is treated identically by the
    caller: "not confirmed complete, safe to (re-)attempt", which is
    exactly what's needed to heal a database that was marked migrated
    from an environment where memory/long_term.json (gitignored, machine-
    local only) never existed in the first place — the original bug this
    function exists to fix."""
    entry = shared_memories.get(_MARKER_CATEGORY, {}).get(_MARKER_KEY)
    if not entry:
        return None
    raw = entry.get("value") if isinstance(entry, dict) else entry
    try:
        state = json.loads(raw)
        return state if isinstance(state, dict) else None
    except (TypeError, ValueError):
        return None


def migrate_if_needed() -> dict:
    """Returns a small summary dict: {"ran": bool, "imported": int, ...}.
    Never raises — a migration failure must not block SARANA from
    starting; it's logged and can simply be retried on the next startup
    (the marker is only written on full success)."""
    if not postgres_repo.is_configured():
        return {"ran": False, "imported": 0, "reason": "postgres not configured"}

    try:
        postgres_repo.init_schema()
    except Exception as e:
        print(f"[Memory][Migration] Schema init failed, skipping migration for now: {e}")
        return {"ran": False, "imported": 0, "reason": str(e)}

    try:
        existing = postgres_repo.fetch_memories("shared", "")
        state = _migration_state(existing)
    except Exception as e:
        print(f"[Memory][Migration] Could not check migration marker, skipping for safety: {e}")
        return {"ran": False, "imported": 0, "reason": str(e)}

    # Only a marker that confirms the LOCAL FILE ITSELF was actually found
    # counts as a completed migration. A marker recorded with
    # source_found=False (or the old, ambiguous pre-fix format) means a
    # previous run had nothing to read from wherever IT executed — that
    # must never permanently block a later run (e.g. this one, from a
    # machine that actually has the file) from importing the real data.
    if state is not None and state.get("source_found") is True:
        return {"ran": False, "imported": 0, "reason": "already migrated"}

    source_found = legacy_file_store.MEMORY_PATH.exists()
    legacy = legacy_file_store.load_memory()
    imported = 0
    try:
        for category in _MANAGED_CATEGORIES:
            entries = legacy.get(category, {})
            if not isinstance(entries, dict):
                continue
            for key, entry in entries.items():
                value = entry.get("value") if isinstance(entry, dict) else entry
                if not value:
                    continue
                postgres_repo.upsert_memory(
                    "shared", "", category, key, str(value), source="migration:long_term.json",
                )
                imported += 1
        # Marker LAST — only written once every real row above has
        # succeeded, so a mid-migration failure leaves no marker update
        # (upsert is idempotent per category/key, so a partial-then-
        # retried migration never duplicates rows either). Records
        # source_found so a future run can tell a genuine "the file
        # existed and had 0 facts" apart from "the file was never here".
        marker_payload = json.dumps({"imported": imported, "source_found": source_found})
        postgres_repo.upsert_memory(
            "shared", "", _MARKER_CATEGORY, _MARKER_KEY, marker_payload,
            source="migration:long_term.json",
        )
    except Exception as e:
        print(f"[Memory][Migration] Failed after importing {imported} fact(s): {e} — "
              f"will retry on next startup (no marker written).")
        return {"ran": True, "imported": imported, "reason": str(e)}

    if not source_found:
        print(
            "[Memory][Migration] memory/long_term.json was not found on this machine — "
            "nothing to import here. Will retry on the next startup rather than marking "
            "this complete, in case a real file is present elsewhere/later."
        )
    print(f"[Memory][Migration] Imported {imported} fact(s) from long_term.json into PostgreSQL (shared).")
    return {"ran": True, "imported": imported, "source_found": source_found}


if __name__ == "__main__":
    result = migrate_if_needed()
    print(result)
