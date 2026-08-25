"""
users/user_db.py -- small local SQLite user/profile store for SARANA's
username+PIN login (dashboard/server.py's /login/username).

Deliberately NOT a general-purpose auth system: no registration endpoint,
no password reset, no external database (Postgres/Neon/Redis/Firebase are
explicitly out of scope) -- a single local file (see DB_PATH) holding a
small, hand-seeded set of known profiles, following the same "local file,
idempotent init" pattern memory/memory_manager.py already uses for
long_term.json.

Schema
------
users
    id                    INTEGER PRIMARY KEY
    username              TEXT UNIQUE   -- canonical/display username
    pin_hash              TEXT          -- "salt_hex$hash_hex", PBKDF2-HMAC-
                                         -- SHA256, 200k iterations. The
                                         -- plaintext PIN is NEVER stored.
    nickname              TEXT
    pronunciation         TEXT          -- optional phonetic spelling for
                                         -- addressing/TTS (e.g. "Saanaa"
                                         -- for nickname "Sana"); '' if unset
    gender                TEXT
    assistant_name        TEXT          -- personalized assistant identity;
                                         -- '' means "use the default (SARANA)"
    voice_preference      TEXT          -- explicit preference, NOT derived
                                         -- from gender (see authenticate())
    language_preference   TEXT
    created_at            TEXT          -- ISO 8601, set once
    updated_at            TEXT          -- ISO 8601, refreshed on reseed

user_aliases
    alias                 TEXT PRIMARY KEY  -- lowercase login name
    user_id               INTEGER           -- FK -> users.id

Every accepted login name (including a user's own canonical username) is
an alias row -- login always resolves through this table, so "one profile,
several login names" (Bandana/Sana/Radhe -> one profile) falls out
naturally instead of needing special-case lookup logic.

Public API
----------
init_db()            -- idempotent: creates the DB/tables/seed data if
                         missing, leaves existing rows untouched otherwise.
authenticate(username, pin) -> dict | None
                      -- case-insensitive alias lookup + constant-time PIN
                         verification. Returns a profile dict with
                         pin_hash EXCLUDED, or None on any failure (unknown
                         alias, wrong PIN) -- callers must not distinguish
                         the two in a user-facing error (see dashboard/
                         server.py's /login/username).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
DB_PATH  = BASE_DIR / "data" / "sarana.db"

_PBKDF2_ITERATIONS = 200_000
_lock = Lock()

# Seed data. PINs live ONLY here, in source, as plaintext for the seeding
# step itself -- hashed immediately on write, never stored or logged in
# plaintext anywhere, and never returned by any function below.
_SEED_USERS = [
    {
        "username": "sana",
        "aliases": ["bandana", "sana", "radhe"],
        "pin": "2060",
        "nickname": "Sana",
        "pronunciation": "Saanaa",
        "gender": "female",
        "assistant_name": "Kanha",
        "voice_preference": "Male",
        "language_preference": "Nepali",
    },
    {
        "username": "saroj",
        "aliases": ["saroj"],
        "pin": "2057",
        "nickname": "Saroj",
        "pronunciation": "",
        "gender": "male",
        "assistant_name": "Sara",
        "voice_preference": "Female",
        "language_preference": "Nepali",
    },
]

_PROFILE_COLUMNS = (
    "id", "username", "nickname", "pronunciation", "gender",
    "assistant_name", "voice_preference", "language_preference",
    "created_at", "updated_at",
)   # deliberately excludes pin_hash -- see _row_to_profile()


def _hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def _verify_pin(pin: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(candidate.hex(), digest_hex)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row_to_profile(row: sqlite3.Row) -> dict:
    """dict from a `users` row -- never includes pin_hash, by construction
    (see _PROFILE_COLUMNS)."""
    return {col: row[col] for col in _PROFILE_COLUMNS}


def init_db() -> None:
    """Create the database/tables if missing, and seed _SEED_USERS if their
    aliases aren't already present. Idempotent and safe to call on every
    process startup (desktop and web alike): a fresh install gets the
    database + seed users created once; a subsequent startup with an
    existing database makes no changes and never duplicates or overwrites
    an existing user's data.
    """
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    username             TEXT NOT NULL UNIQUE,
                    pin_hash             TEXT NOT NULL,
                    nickname             TEXT NOT NULL DEFAULT '',
                    pronunciation        TEXT NOT NULL DEFAULT '',
                    gender               TEXT NOT NULL DEFAULT '',
                    assistant_name       TEXT NOT NULL DEFAULT '',
                    voice_preference     TEXT NOT NULL DEFAULT '',
                    language_preference  TEXT NOT NULL DEFAULT '',
                    created_at           TEXT NOT NULL,
                    updated_at           TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_aliases (
                    alias   TEXT NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id)
                )
                """
            )
            conn.commit()

            for seed in _SEED_USERS:
                existing = conn.execute(
                    "SELECT user_id FROM user_aliases WHERE alias = ?",
                    (seed["username"],),
                ).fetchone()
                if existing:
                    continue   # already seeded -- never duplicate or overwrite

                now = datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    """
                    INSERT INTO users (
                        username, pin_hash, nickname, pronunciation, gender,
                        assistant_name, voice_preference, language_preference,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        seed["username"], _hash_pin(seed["pin"]), seed["nickname"],
                        seed["pronunciation"], seed["gender"], seed["assistant_name"],
                        seed["voice_preference"], seed["language_preference"], now, now,
                    ),
                )
                user_id = cur.lastrowid
                for alias in seed["aliases"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_aliases (alias, user_id) VALUES (?, ?)",
                        (alias.lower(), user_id),
                    )
            conn.commit()
        finally:
            conn.close()


def authenticate(username: str, pin: str) -> dict | None:
    """Case-insensitive alias lookup + constant-time PIN verification.
    Returns the profile dict (no pin_hash) on success, None on ANY failure
    (unknown alias or wrong PIN -- deliberately indistinguishable to the
    caller, so a generic "invalid username or PIN" is the only thing that
    can ever be shown to a client)."""
    if not username or not pin:
        return None
    alias = username.strip().lower()

    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT users.* FROM users
                JOIN user_aliases ON user_aliases.user_id = users.id
                WHERE user_aliases.alias = ?
                """,
                (alias,),
            ).fetchone()
        finally:
            conn.close()

    if row is None:
        return None
    if not _verify_pin(pin, row["pin_hash"]):
        return None
    return _row_to_profile(row)
