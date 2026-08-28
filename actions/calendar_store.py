"""
actions/calendar_store.py -- persistent, encrypted storage for a SARANA
user's Google Calendar OAuth credentials (access token, refresh token,
scopes, expiry -- whatever google.oauth2.credentials.Credentials.to_json()
serializes).

Deliberately a SEPARATE module from memory/postgres_repo.py, not an
extension of it: that module's own docstring declares itself
single-purpose ("memory/memory_cache.py is the ONLY caller"). Calendar
credentials are not memory data -- they're a per-user secret, closer in
kind to users/user_db.py's pin_hash than to a remembered fact -- so this
gets its own table and its own thin connection helper, following the
exact SAME pattern (DATABASE_URL, psycopg, a fresh short-lived connection
per call, idempotent init_schema()) rather than sharing internals across
two unrelated concerns.

Security
--------
Tokens are encrypted at rest with AES-256-CBC (via the `cryptography`
package already used by dashboard/server.py for its own Remote Access
session-key encryption) before ever reaching Postgres -- see
_encryption_key()'s own docstring for the key-derivation choice. Nothing
in this module ever logs a token, and every function that returns
"status" information (get_status()) deliberately omits the encrypted
blob entirely.

Configuration
-------------
DATABASE_URL -- same Postgres connection string memory/postgres_repo.py
uses (see that module's docstring). is_configured() is False when unset
or when the optional psycopg dependency isn't installed; Google Calendar
integration is then simply unavailable (same graceful-degradation
convention as the memory system, but WITHOUT a local-file fallback --
Calendar credentials are inherently multi-user, per-account secrets that
a shared local JSON file could never safely hold, unlike a single-user
desktop's own memory).

Ownership
---------
Rows are keyed by `owner` -- the SAME canonical users/user_db.py username
concept sarana_memories/sarana_session_summaries already use (never a
SQLite integer id, never a display name). One Google Calendar connection
per SARANA account; connecting again simply replaces the stored
credentials for that owner.
"""
from __future__ import annotations

import base64
import hashlib
import os

try:
    import psycopg
except ImportError:                     # pragma: no cover — optional dependency
    psycopg = None

_CONNECT_TIMEOUT_SECONDS = 5
_AES_SALT = b"SARANA-GOOGLE-CALENDAR-v1"


def is_configured() -> bool:
    """True only when both DATABASE_URL is set AND psycopg is installed --
    mirrors memory/postgres_repo.py's is_configured() exactly. Callers
    (main.py's calendar tools, dashboard/server.py's OAuth routes) must
    treat False as "Calendar integration unavailable in this environment"
    rather than raising."""
    return bool(os.environ.get("DATABASE_URL")) and psycopg is not None


def _connect():
    dsn = os.environ["DATABASE_URL"]
    return psycopg.connect(dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS, autocommit=True)


def init_schema() -> None:
    """Idempotent — safe to call on every process startup/OAuth callback,
    same convention as memory/postgres_repo.py's init_schema()."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sarana_google_calendar_credentials (
                owner           TEXT PRIMARY KEY,
                encrypted_token TEXT NOT NULL,
                google_email    TEXT NOT NULL DEFAULT '',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


# ── encryption at rest ────────────────────────────────────────────────

def _encryption_key() -> bytes:
    """AES-256 key derived from GOOGLE_CLIENT_SECRET via SHA-256 plus a
    fixed, purpose-specific salt -- the exact key-derivation SHAPE
    dashboard/server.py's own _derive_key() already uses for Remote
    Access session-key encryption (SHA-256(secret‖salt), never the raw
    secret used directly as a key).

    Deliberately NOT a dedicated new TOKEN_ENCRYPTION_KEY environment
    variable: Render already has GOOGLE_CLIENT_SECRET configured for
    this exact feature, and requiring a second, separately-provisioned
    secret before Calendar integration can work at all would be a real
    deployment obstacle for zero functional gain today. GOOGLE_CLIENT_
    SECRET is itself a real secret issued by Google with reasonable
    entropy for this purpose. A dedicated encryption key would be a
    cleaner separation of concerns and is a reasonable future
    improvement (noted as a limitation in this feature's own report),
    but is not required for tokens to be genuinely encrypted at rest
    today."""
    secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_SECRET is not configured -- cannot encrypt/decrypt "
            "Calendar credentials."
        )
    return hashlib.sha256(secret.encode("utf-8") + _AES_SALT).digest()


def _encrypt(plaintext: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    key = _encryption_key()
    iv = os.urandom(16)
    padder = sym_pad.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(iv + ct).decode("ascii")


def _decrypt(enc_b64: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    key = _encryption_key()
    raw = base64.b64decode(enc_b64)
    iv, ct = raw[:16], raw[16:]
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


# ── credential storage ───────────────────────────────────────────────

def save_credentials(owner: str, credentials_json: str, email: str = "") -> None:
    """`credentials_json`: the exact string google.oauth2.credentials.
    Credentials.to_json() produces -- encrypted here, never stored or
    logged in plaintext. Upserts by owner: connecting again (or a token
    refresh persisting its new access token) simply replaces the row."""
    encrypted = _encrypt(credentials_json)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sarana_google_calendar_credentials (owner, encrypted_token, google_email)
            VALUES (%s, %s, %s)
            ON CONFLICT (owner) DO UPDATE SET
                encrypted_token = EXCLUDED.encrypted_token,
                google_email    = EXCLUDED.google_email,
                updated_at      = now()
            """,
            (owner, encrypted, email),
        )


def load_credentials(owner: str) -> tuple[str, str] | None:
    """Returns (credentials_json, google_email), decrypted, or None if
    `owner` has never connected Google Calendar. Callers must never log
    or otherwise surface the first element."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT encrypted_token, google_email FROM sarana_google_calendar_credentials "
            "WHERE owner = %s",
            (owner,),
        ).fetchone()
    if not row:
        return None
    encrypted, email = row
    return _decrypt(encrypted), (email or "")


def get_status(owner: str) -> dict:
    """Safe status info ONLY -- never touches or returns the encrypted
    token column at all. Used by GET /api/calendar/status."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT google_email FROM sarana_google_calendar_credentials WHERE owner = %s",
            (owner,),
        ).fetchone()
    if not row:
        return {"connected": False, "email": ""}
    return {"connected": True, "email": row[0] or ""}


def delete_credentials(owner: str) -> bool:
    """Used by disconnect. Returns True if a row actually existed."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM sarana_google_calendar_credentials WHERE owner = %s",
            (owner,),
        )
        return cur.rowcount > 0
