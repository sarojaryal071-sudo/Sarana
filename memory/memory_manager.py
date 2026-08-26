"""
memory/memory_manager.py -- SARANA's memory API surface. Same public
functions this module has always exposed (load_memory, update_memory,
format_memory_for_prompt, save_session_summary, pop_last_session, remember,
forget, forget_memory, MEMORY_PATH, _lock) so main.py and every action
module (actions/background_monitor.py, actions/proactive.py) keep working
completely unchanged — but personal/shared FACTS (identity/preferences/
projects/relationships/wishes/notes) are now backed by PostgreSQL (see
memory/postgres_repo.py) through an in-RAM per-login session cache (see
memory/memory_cache.py), instead of a single global local JSON file.

What's UNCHANGED
-----------------
- actions/background_monitor.py's "monitors" data and any other key
  outside the six managed categories above — a separate concern that
  shares memory/long_term.json by historical convenience only. Still
  read/written directly against MEMORY_PATH/_lock (re-exported below,
  unchanged) and passed through untouched by load_memory() (see
  _read_legacy_extras()).
- format_memory_for_prompt()'s overall structure/limits/header for facts
  with no "subject" entry (personal facts, and shared facts with no known
  subject e.g. legacy migrated data) — output for those is byte-for-byte
  identical to before. A shared fact WITH a known subject now additionally
  names who it's about (see that function) — the attribution-loss fix
  below.
- Local/desktop with no DATABASE_URL configured: the whole personal/shared
  system transparently falls back to memory/legacy_file_store.py, i.e.
  exactly this module's original behavior — zero setup required, no
  regression for anyone not using Postgres.

New entry points for main.py's login/logout lifecycle
--------------------------------------------------------
set_active_owner(owner)     -- call once per login (main.py's
                                _set_user_profile()), BEFORE the resulting
                                connection's _build_config() runs. Loads
                                that user's personal memories + the shared
                                set into RAM. owner="" is the "no profile
                                resolved" bucket (today's original
                                un-scoped behavior).
clear_active_session()      -- call on logout: discards the in-RAM cache.
start_persistence_worker()  -- call once, from within main.py's run(), to
                                start the background Postgres write worker
                                for the whole process lifetime.
"""
from datetime import datetime

from memory import legacy_file_store, memory_cache, postgres_repo

# Re-exported for backward compatibility (e.g. tests reading the real file
# path directly) ONLY — this is a VALUE SNAPSHOT taken at import time, not
# a live alias of memory.legacy_file_store.MEMORY_PATH. Any code that
# actually needs to read/write the local JSON file itself (like
# actions/background_monitor.py's "monitors" data) MUST import MEMORY_PATH/
# _lock/load_memory directly from memory.legacy_file_store instead of from
# here — going through this stale copy risks reading/writing the wrong
# path if legacy_file_store.MEMORY_PATH is ever redirected (e.g. by a
# test patching it) after this module was first imported.
MEMORY_PATH = legacy_file_store.MEMORY_PATH
_lock = legacy_file_store._lock

_MANAGED_CATEGORIES = {
    "identity", "preferences", "projects", "relationships", "wishes", "notes",
}
_VALID_CATEGORIES = _MANAGED_CATEGORIES  # remember()'s validation set


# ── login / logout lifecycle ─────────────────────────────────────────────

def set_active_owner(owner: str) -> None:
    """See module docstring. Cheap to call redundantly (e.g. the same
    account logging back in) — it just reloads the same data."""
    memory_cache._cache.load(owner)


def clear_active_session() -> None:
    memory_cache._cache.clear()


def start_persistence_worker():
    return memory_cache.start_worker()


def owner_language(owner: str) -> str:
    """Best-effort language-preference lookup for `owner`, independent of
    whichever owner the ACTIVE session cache currently holds. Needed by
    main.py's _save_session_summary() on an identity-switch reconnect: by
    the time that coroutine actually runs, set_active_owner() has already
    reloaded the cache for the INCOMING user, so load_memory() would
    silently return the wrong person's language preference for the
    OUTGOING session's summary. Falls back to "" (caller defaults to
    Nepali, same as before this migration) on any failure."""
    cache = memory_cache._cache
    if cache.owner == owner:
        data = cache.merged()
    elif postgres_repo.is_configured():
        try:
            data = postgres_repo.fetch_memories("personal", owner)
        except Exception as e:
            print(f"[Memory][Postgres] owner_language lookup failed ({e}).")
            data = {}
    else:
        data = legacy_file_store.load_memory()
    entry = data.get("identity", {}).get("language", {})
    return (entry.get("value", "") if isinstance(entry, dict) else str(entry)).strip()


def current_owner() -> str:
    """The owner the active session cache is currently loaded for — used
    by main.py to freeze `self._session_owner` at connect time (see
    main.py's _build_config()) so a mid-session identity switch can never
    misattribute the OUTGOING session's summary to the incoming user."""
    return memory_cache._cache.owner


# ── everyday reads/writes (hot path — RAM cache only, no DB call) ────────

def _read_legacy_extras() -> dict:
    """Keys that live in the local JSON file but are NOT one of the
    managed memory categories — e.g. actions/background_monitor.py's
    "monitors" list. Passed through untouched so load_memory() keeps
    returning everything callers outside this module have always been
    able to read from it."""
    raw = legacy_file_store.load_memory()
    return {k: v for k, v in raw.items() if k not in _MANAGED_CATEGORIES}


def load_memory() -> dict:
    """Merged view for the CURRENT ACTIVE SESSION: cache-managed personal+
    shared categories (Postgres-backed, or local-file-backed if Postgres
    isn't configured/reachable) overlaid on whatever else lives in the
    legacy local file (untouched, see _read_legacy_extras())."""
    extras = _read_legacy_extras()
    merged = memory_cache._cache.merged()
    return {**extras, **merged}


def update_memory(
    memory_update: dict, *, shared: bool = False, importance: int = 3,
    entities: list | None = None, event_date: str | None = None, source: str = "",
) -> dict:
    """memory_update: {category: {key: value_or_{"value": value}}} — same
    shape this function has always taken. shared=True marks the fact as
    visible to every user instead of only the current session's owner
    (see main.py's save_memory tool declaration's optional `shared` arg)."""
    memory_cache._cache.update(
        memory_update, shared=shared, importance=importance,
        entities=entities, event_date=event_date, source=source,
    )
    return load_memory()


def _entry_value_and_subject(entry) -> tuple[str | None, str | None]:
    """Pulls (value, subject) out of an entry that's either a plain string
    (legacy shape) or {"value": ..., "updated": ..., "subject": ...}
    (see postgres_repo.fetch_memories()'s docstring — "subject" is only
    ever present on a SHARED fact whose original teller is known)."""
    if isinstance(entry, dict):
        return entry.get("value"), entry.get("subject")
    return entry, None


def _subject_note(subject: str | None) -> str:
    """Attribution-loss fix: a shared fact's subject (who told SARANA it /
    who it's about) must survive into the prompt text itself — otherwise a
    fact like "Bimal is my friend" (told by Saroj) reads, once merged for
    ANY reader, as an anonymous "Bimal is a friend", which the ADDRESS
    clause then makes Gemini default to reading as being about whoever
    it's currently speaking to. Appending the subject by name lets Gemini
    do what it already does well — phrase it naturally as "your X" when
    the subject IS who it's addressing, or "SUBJECT's X" when it's someone
    else — without this module needing to know who's currently listening."""
    return f" [fact about {subject}]" if subject else ""


def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    identity  = memory.get("identity", {})
    id_fields = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]
    for field in id_fields:
        entry = identity.get(field)
        if entry:
            val, subject = _entry_value_and_subject(entry)
            if val:
                lines.append(f"{field.title()}: {val}{_subject_note(subject)}")
    for key, entry in identity.items():
        if key in id_fields:
            continue
        val, subject = _entry_value_and_subject(entry)
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}{_subject_note(subject)}")

    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("")
        lines.append("Preferences:")
        for key, entry in list(prefs.items())[:15]:
            val, subject = _entry_value_and_subject(entry)
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}{_subject_note(subject)}")

    projects = memory.get("projects", {})
    if projects:
        lines.append("")
        lines.append("Active Projects / Goals:")
        for key, entry in list(projects.items())[:8]:
            val, subject = _entry_value_and_subject(entry)
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}{_subject_note(subject)}")

    rels = memory.get("relationships", {})
    if rels:
        lines.append("")
        lines.append("People in their life:")
        for key, entry in list(rels.items())[:10]:
            val, subject = _entry_value_and_subject(entry)
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}{_subject_note(subject)}")

    wishes = memory.get("wishes", {})
    if wishes:
        lines.append("")
        lines.append("Wishes / Plans / Wants:")
        for key, entry in list(wishes.items())[:8]:
            val, subject = _entry_value_and_subject(entry)
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}{_subject_note(subject)}")

    notes = memory.get("notes", {})
    if notes:
        lines.append("")
        lines.append("Other notes:")
        for key, entry in list(notes.items())[:8]:
            val, subject = _entry_value_and_subject(entry)
            if val:
                lines.append(f"  - {key}: {val}{_subject_note(subject)}")

    if not lines:
        return ""

    # "ABOUT THIS PERSON" (unchanged wording) still governs personal facts
    # and unattributed shared facts — the added sentence exists ONLY to
    # stop a "[fact about X]" tag from being misread/ignored: a fact
    # tagged that way is about the NAMED person, not necessarily whoever
    # you're currently speaking with.
    header = (
        "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list. "
        "A line tagged \"[fact about X]\" belongs to X, not necessarily whoever you're "
        "addressing right now — attribute it to X by name unless X is exactly who "
        "you're speaking with.]\n"
    )
    result = header + "\n".join(lines)
    if len(result) > 2000:
        result = result[:1997] + "…"

    return result + "\n"


def remember(key: str, value: str, category: str = "notes", *, shared: bool = False) -> str:
    if category not in _VALID_CATEGORIES:
        category = "notes"
    update_memory({category: {key: {"value": value}}}, shared=shared)
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    return memory_cache._cache.forget(key, category)


forget_memory = forget


# ── session memory ────────────────────────────────────────────────────────

def save_session_summary(summary: str, language: str = "", owner: str | None = None) -> None:
    """Persist a 1-2 sentence end-of-session summary for `owner` (defaults
    to the cache's CURRENTLY active owner — pass it explicitly when the
    session that just ended is no longer the active one, e.g. main.py's
    run() after an identity-switch reconnect has already loaded the next
    user's profile)."""
    summary = (summary or "").strip()
    if not summary:
        return
    resolved_owner = memory_cache._cache.owner if owner is None else (owner or "")
    if postgres_repo.is_configured():
        try:
            postgres_repo.init_schema()
            postgres_repo.save_session_summary(
                resolved_owner, datetime.now().strftime("%Y-%m-%d"), summary[:280], language,
            )
            print(f"[Memory][Postgres] Session saved for '{resolved_owner or '(no profile)'}': {summary[:60]}…")
            return
        except Exception as e:
            print(f"[Memory][Postgres] save_session_summary failed ({e}) — falling back to local file.")
    legacy_file_store.save_session_summary(summary, language)


def pop_last_session(owner: str | None = None) -> dict | None:
    """Return AND remove the most recent session summary for `owner`
    (defaults to the cache's current owner). Consumed on read — never
    repeated in a future greeting."""
    resolved_owner = memory_cache._cache.owner if owner is None else (owner or "")
    if postgres_repo.is_configured():
        try:
            postgres_repo.init_schema()
            return postgres_repo.pop_last_session(resolved_owner)
        except Exception as e:
            print(f"[Memory][Postgres] pop_last_session failed ({e}) — falling back to local file.")
    return legacy_file_store.pop_last_session()
