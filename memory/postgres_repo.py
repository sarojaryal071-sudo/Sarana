"""
memory/postgres_repo.py -- clean data-access layer for SARANA's persistent
memory database (PostgreSQL). No business logic lives here: every public
function below is a thin, direct SQL operation. memory/memory_cache.py is
the ONLY caller of this module -- the rest of SARANA (main.py, actions/*,
memory/memory_manager.py's public API) never imports psycopg or writes a
SQL string itself.

Configuration
-------------
DATABASE_URL environment variable -- a standard Postgres connection string
(e.g. postgresql://user:pass@host:5432/dbname), exactly what Render's
managed Postgres add-on provides. Never hardcoded, never committed (same
convention as GEMINI_API_KEY in main.py's _get_api_key()). is_configured()
is False when it's unset, or when the optional `psycopg` dependency isn't
installed -- callers (memory/memory_cache.py) MUST check this and fall
back to local-file-only behavior; every other function here assumes both
are present and will raise on any real failure (callers catch and degrade
gracefully -- see memory_cache.py's try/except around every call into
this module).

Schema
------
sarana_memories
    id           BIGSERIAL PRIMARY KEY
    scope        TEXT   -- 'personal' | 'shared'
    owner        TEXT   -- canonical users/user_db.py username (e.g.
                            "saroj", "sana"). For scope='personal' this is
                            the exclusive owner (who it's visible to AND
                            who it's about -- the same person). For
                            scope='shared' this is the SUBJECT (who told
                            SARANA the fact / who it's about) -- visibility
                            is controlled entirely by scope='shared'
                            (loaded for everyone), NOT by this column; ''
                            means the subject is unknown (e.g. legacy data
                            migrated before attribution existed). See
                            fetch_memories()'s docstring for how this is
                            surfaced back out as each entry's "subject" key.
    category     TEXT   -- identity | preferences | projects |
                            relationships | wishes | notes | ... — free-form,
                            NOT a rigid enum, so a new category never needs
                            a migration (mirrors memory/memory_manager.py's
                            original JSON-file category shape exactly)
    key          TEXT   -- short slug, e.g. "favorite_food", "birthday"
    content      TEXT   -- the actual remembered value/sentence
    entities     JSONB  -- important people/entities mentioned, e.g.
                            ["Bimal"] -- optional, defaults to []
    event_date   DATE   -- optional: a specific calendar date this fact is
                            tied to (birthdays, anniversaries, planned
                            events) -- nullable, indexed for future
                            "what's coming up" queries (see
                            list_upcoming_dated_memories())
    importance   SMALLINT -- 1-5, defaults to 3 -- lets a future trimming/
                            recall pass prioritize without another table
    source       TEXT    -- provenance, e.g. "conversation", defaults to ''
    created_at   TIMESTAMPTZ
    updated_at   TIMESTAMPTZ
    UNIQUE (scope, owner, category, key) -- upsert target; one row per
        fact, exactly like the old JSON file's memory[category][key] shape

sarana_session_summaries
    id         BIGSERIAL PRIMARY KEY
    owner      TEXT     -- canonical username; session summaries are always
                            personal (a session belongs to whoever was
                            logged in)
    date       DATE
    summary    TEXT
    language   TEXT
    created_at TIMESTAMPTZ

Design notes
------------
- owner is the canonical SQLite `username` column (users/user_db.py), NOT
  the SQLite integer id -- memory rows stay meaningful if that database is
  ever rebuilt with different ids, and no cross-database join is ever
  needed to read this table back.
- A fresh short-lived connection per call (like users/user_db.py's own
  _connect()) rather than a persistent pool -- this module is only ever
  touched at login (cache load), on an explicit save_memory tool call, on
  logout persistence flush, and by the background retry worker -- never on
  every conversation turn (see memory/memory_cache.py's module docstring
  for the full "Postgres is not in the hot path" picture). A tiny
  connect_timeout keeps a genuinely unreachable database from hanging
  whatever called in.
"""
from __future__ import annotations

import os
from datetime import date, datetime

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:                     # pragma: no cover — optional dependency
    psycopg = None
    Jsonb = None

_CONNECT_TIMEOUT_SECONDS = 5
_MANAGED_CATEGORIES = (
    "identity", "preferences", "projects", "relationships", "wishes", "notes",
)


def is_configured() -> bool:
    """True only when both a DATABASE_URL is set AND the optional psycopg
    dependency is actually installed. Callers must treat False as "use the
    local-file fallback" rather than raising — see memory/memory_cache.py."""
    return bool(os.environ.get("DATABASE_URL")) and psycopg is not None


def _connect():
    dsn = os.environ["DATABASE_URL"]
    return psycopg.connect(dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS, autocommit=True)


def init_schema() -> None:
    """Idempotent: creates the tables/indexes if missing, leaves existing
    data untouched otherwise. Safe to call on every process startup (same
    pattern as users/user_db.py's init_db())."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sarana_memories (
                id           BIGSERIAL PRIMARY KEY,
                scope        TEXT NOT NULL CHECK (scope IN ('personal', 'shared')),
                owner        TEXT NOT NULL DEFAULT '',
                category     TEXT NOT NULL,
                key          TEXT NOT NULL,
                content      TEXT NOT NULL,
                entities     JSONB NOT NULL DEFAULT '[]'::jsonb,
                event_date   DATE,
                importance   SMALLINT NOT NULL DEFAULT 3,
                source       TEXT NOT NULL DEFAULT '',
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (scope, owner, category, key)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sarana_memories_owner "
            "ON sarana_memories (scope, owner)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sarana_memories_event_date "
            "ON sarana_memories (event_date) WHERE event_date IS NOT NULL"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sarana_session_summaries (
                id         BIGSERIAL PRIMARY KEY,
                owner      TEXT NOT NULL,
                date       DATE NOT NULL,
                summary    TEXT NOT NULL,
                language   TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sarana_session_summaries_owner "
            "ON sarana_session_summaries (owner, created_at DESC)"
        )


# ── memories: read ───────────────────────────────────────────────────────

def fetch_memories(scope: str, owner: str = "") -> dict:
    """Returns {category: {key: {"value": content, "updated": "YYYY-MM-DD"}}}
    -- the same shape memory/memory_manager.py's original file-backed
    load_memory() produced, so format_memory_for_prompt() and every other
    reader downstream needed no signature changes -- plus an additional
    "subject" key on each entry when it's known and meaningful (see below).

    scope='personal': filtered to exactly `owner`'s own rows, as before --
    every fact returned is unambiguously about that one person, so no
    "subject" key is added (there's nothing to disambiguate).

    scope='shared': returns EVERY shared row regardless of the `owner`
    column's value -- `owner` here means "who the fact is originally
    ABOUT / who told SARANA it" (the subject), not "who may see it"
    (that's what scope already controls). Each entry's "subject" key
    carries that value when non-empty, so a caller can tell "Bimal is a
    friend" (unattributed, e.g. migrated legacy data) apart from "Bimal is
    SAROJ's friend" (attributed) instead of collapsing every shared fact
    into an anonymous, ownerless blob the way scope='shared' used to
    (every shared row's owner column was always '' before this fix)."""
    out: dict = {cat: {} for cat in _MANAGED_CATEGORIES}
    with _connect() as conn:
        if scope == "shared":
            rows = conn.execute(
                "SELECT category, key, content, owner, updated_at FROM sarana_memories "
                "WHERE scope = %s",
                (scope,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT category, key, content, owner, updated_at FROM sarana_memories "
                "WHERE scope = %s AND owner = %s",
                (scope, owner),
            ).fetchall()
    for category, key, content, row_owner, updated_at in rows:
        entry = {
            "value": content,
            "updated": updated_at.strftime("%Y-%m-%d") if updated_at else "",
        }
        if scope == "shared" and row_owner:
            entry["subject"] = row_owner
        out.setdefault(category, {})[key] = entry
    return out


def list_upcoming_dated_memories(owners: list[str], within_days: int = 2) -> list[dict]:
    """Personal (for the given owners) + shared memories whose event_date
    falls within the next `within_days` days (inclusive of today) —
    supports future "mention Saroj's birthday tomorrow" style features
    (see memory/memory_manager.py's module docstring, section 8 of the
    original request) without implementing that feature here. Not called
    anywhere yet — exists so the schema/query path is proven to work."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT scope, owner, category, key, content, event_date
            FROM sarana_memories
            WHERE event_date IS NOT NULL
              AND event_date BETWEEN CURRENT_DATE AND (CURRENT_DATE + %s::int)
              AND (scope = 'shared' OR owner = ANY(%s))
            ORDER BY event_date ASC
            """,
            (within_days, list(owners or [])),
        ).fetchall()
    return [
        {
            "scope": r[0], "owner": r[1], "category": r[2], "key": r[3],
            "content": r[4], "event_date": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


# ── memories: write ──────────────────────────────────────────────────────

def upsert_memory(
    scope: str, owner: str, category: str, key: str, content: str, *,
    importance: int = 3, entities: list | None = None,
    event_date: str | None = None, source: str = "",
) -> None:
    ev = None
    if event_date:
        try:
            ev = datetime.strptime(event_date, "%Y-%m-%d").date()
        except ValueError:
            ev = None
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sarana_memories
                (scope, owner, category, key, content, entities, event_date, importance, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (scope, owner, category, key) DO UPDATE SET
                content    = EXCLUDED.content,
                entities   = EXCLUDED.entities,
                event_date = EXCLUDED.event_date,
                importance = EXCLUDED.importance,
                source     = EXCLUDED.source,
                updated_at = now()
            """,
            (
                scope, owner, category, key, content,
                Jsonb(entities or []), ev, importance, source,
            ),
        )


def delete_memory(scope: str, owner: str, category: str, key: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM sarana_memories WHERE scope=%s AND owner=%s AND category=%s AND key=%s",
            (scope, owner, category, key),
        )
        return cur.rowcount > 0


# ── session summaries ────────────────────────────────────────────────────

def save_session_summary(owner: str, summary_date: str, summary: str, language: str = "") -> None:
    d = datetime.strptime(summary_date, "%Y-%m-%d").date() if isinstance(summary_date, str) else summary_date
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sarana_session_summaries (owner, date, summary, language) "
            "VALUES (%s, %s, %s, %s)",
            (owner, d, summary, language),
        )


def pop_last_session(owner: str) -> dict | None:
    """Returns AND deletes the most recent session summary for `owner`.
    Same "consume on read" contract as the original file-backed
    pop_last_session() — never repeated in a future greeting."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, date, summary, language FROM sarana_session_summaries "
            "WHERE owner = %s ORDER BY created_at DESC LIMIT 1",
            (owner,),
        ).fetchone()
        if not row:
            return None
        row_id, d, summary, language = row
        conn.execute("DELETE FROM sarana_session_summaries WHERE id = %s", (row_id,))
    entry = {"date": d.strftime("%Y-%m-%d") if isinstance(d, date) else str(d), "summary": summary}
    if language:
        entry["language"] = language
    return entry
