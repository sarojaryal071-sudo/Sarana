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
key='long_term_json_v1') records that the import already happened; its
presence short-circuits the whole function before any real row is
touched.

Usage
-----
Automatic: main.py's run() calls migrate_if_needed() once at startup,
whenever PostgreSQL is configured — cheap no-op after the first real run.

Manual (e.g. to migrate against a database from a machine that isn't
running the app): `python -m memory.migrate_long_term`
"""
from __future__ import annotations

from memory import legacy_file_store, postgres_repo

_MARKER_CATEGORY = "_migration"
_MARKER_KEY = "long_term_json_v1"
_MANAGED_CATEGORIES = (
    "identity", "preferences", "projects", "relationships", "wishes", "notes",
)


def _already_migrated() -> bool:
    try:
        existing = postgres_repo.fetch_memories("shared", "")
        return _MARKER_KEY in existing.get(_MARKER_CATEGORY, {})
    except Exception:
        # A category outside the managed set (like "_migration") isn't
        # returned by fetch_memories()'s pre-seeded dict shape unless a
        # row for it actually exists — fetch_memories() only pre-seeds the
        # MANAGED categories, so an empty dict here just means "not
        # migrated yet", not an error. See fetch_memories()'s note below
        # for why this still works.
        return False


def migrate_if_needed() -> dict:
    """Returns a small summary dict: {"ran": bool, "imported": int}.
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

    # fetch_memories() only pre-seeds the SIX managed categories in its
    # returned dict (see postgres_repo.py) — a real "_migration" row, once
    # written, still comes back fine because the SQL query itself has no
    # category filter; it's only the *pre-seeded skeleton* that's limited
    # to the managed set. So checking the marker this way is reliable.
    try:
        if _already_migrated():
            return {"ran": False, "imported": 0, "reason": "already migrated"}
    except Exception as e:
        print(f"[Memory][Migration] Could not check migration marker, skipping for safety: {e}")
        return {"ran": False, "imported": 0, "reason": str(e)}

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
        # succeeded, so a mid-migration failure leaves no marker and a
        # later retry safely re-imports (upsert is idempotent per
        # category/key, so a partial-then-retried migration never
        # duplicates rows either).
        postgres_repo.upsert_memory(
            "shared", "", _MARKER_CATEGORY, _MARKER_KEY, "migrated",
            source="migration:long_term.json",
        )
    except Exception as e:
        print(f"[Memory][Migration] Failed after importing {imported} fact(s): {e} — "
              f"will retry on next startup (no marker written).")
        return {"ran": True, "imported": imported, "reason": str(e)}

    print(f"[Memory][Migration] Imported {imported} fact(s) from long_term.json into PostgreSQL (shared).")
    return {"ran": True, "imported": imported}


if __name__ == "__main__":
    result = migrate_if_needed()
    print(result)
