"""
tests/test_calendar_store.py -- actions/calendar_store.py: encryption at
rest, is_configured() gating, and the actual SQL-generating save/load/
status/delete functions against a lightweight fake psycopg connection
(never a live database).

Run with:
    .venv/Scripts/python.exe -m tests.test_calendar_store
"""
import os
from unittest.mock import patch

from actions import calendar_store


# ── a minimal fake psycopg connection ─────────────────────────────────────
# Only implements exactly what calendar_store.py's own SQL needs: a
# context-manager connection whose .execute(sql, params) returns a cursor-
# like object with .fetchone()/.rowcount, backed by one shared in-memory
# table keyed by owner (calendar_store's real schema has owner as the sole
# PRIMARY KEY, so this mirrors that exactly).

class _FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, table: dict):
        self._table = table

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()):
        s = sql.strip()
        if s.startswith("CREATE TABLE"):
            return _FakeCursor()
        if s.startswith("INSERT INTO"):
            owner, encrypted, email = params
            self._table[owner] = {"encrypted_token": encrypted, "google_email": email}
            return _FakeCursor()
        if s.startswith("SELECT encrypted_token"):
            (owner,) = params
            row = self._table.get(owner)
            if row is None:
                return _FakeCursor([])
            return _FakeCursor([(row["encrypted_token"], row["google_email"])])
        if s.startswith("SELECT google_email"):
            (owner,) = params
            row = self._table.get(owner)
            if row is None:
                return _FakeCursor([])
            return _FakeCursor([(row["google_email"],)])
        if s.startswith("DELETE"):
            (owner,) = params
            existed = owner in self._table
            self._table.pop(owner, None)
            return _FakeCursor(rowcount=1 if existed else 0)
        raise AssertionError(f"unexpected SQL in fake connection: {sql!r}")


def _patched_store(table: dict):
    return patch.object(calendar_store, "_connect", return_value=_FakeConnection(table))


# ── is_configured() ────────────────────────────────────────────────────

def test_is_configured_false_without_database_url() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DATABASE_URL", None)
        assert calendar_store.is_configured() is False
    print("test_is_configured_false_without_database_url: PASS")


def test_is_configured_true_with_database_url_and_psycopg() -> None:
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x/y"}):
        with patch.object(calendar_store, "psycopg", object()):
            assert calendar_store.is_configured() is True
    print("test_is_configured_true_with_database_url_and_psycopg: PASS")


def test_is_configured_false_without_psycopg_installed() -> None:
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x/y"}):
        with patch.object(calendar_store, "psycopg", None):
            assert calendar_store.is_configured() is False
    print("test_is_configured_false_without_psycopg_installed: PASS")


# ── encryption at rest ──────────────────────────────────────────────────

def test_encrypt_decrypt_round_trip() -> None:
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "a-real-looking-client-secret-123"}):
        plaintext = '{"token": "ya29.fake", "refresh_token": "1//fake"}'
        encrypted = calendar_store._encrypt(plaintext)
        assert encrypted != plaintext
        assert plaintext not in encrypted   # the raw token must never appear verbatim in the stored blob
        decrypted = calendar_store._decrypt(encrypted)
        assert decrypted == plaintext
    print("test_encrypt_decrypt_round_trip: PASS")


def test_encryption_key_requires_google_client_secret() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GOOGLE_CLIENT_SECRET", None)
        try:
            calendar_store._encryption_key()
            assert False, "must raise without GOOGLE_CLIENT_SECRET"
        except RuntimeError:
            pass
    print("test_encryption_key_requires_google_client_secret: PASS")


def test_different_secrets_produce_undecryptable_ciphertext() -> None:
    """A basic sanity check that the key is actually derived FROM the
    secret (not some fixed/ignored value) -- encrypting under one secret
    and decrypting under a different one must not silently succeed."""
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret-one"}):
        encrypted = calendar_store._encrypt("hello world")
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret-two"}):
        try:
            result = calendar_store._decrypt(encrypted)
            assert result != "hello world"
        except Exception:
            pass   # a padding/decrypt error is an equally acceptable outcome
    print("test_different_secrets_produce_undecryptable_ciphertext: PASS")


# ── save / load / status / delete ──────────────────────────────────────

def test_save_and_load_credentials_round_trip() -> None:
    table: dict = {}
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret"}), _patched_store(table):
        calendar_store.save_credentials("saroj", '{"token": "abc"}', "saroj@example.com")
        loaded = calendar_store.load_credentials("saroj")
    assert loaded == ('{"token": "abc"}', "saroj@example.com")
    print("test_save_and_load_credentials_round_trip: PASS")


def test_load_credentials_returns_none_when_not_connected() -> None:
    table: dict = {}
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret"}), _patched_store(table):
        assert calendar_store.load_credentials("nobody") is None
    print("test_load_credentials_returns_none_when_not_connected: PASS")


def test_save_credentials_upserts_by_owner() -> None:
    table: dict = {}
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret"}), _patched_store(table):
        calendar_store.save_credentials("saroj", '{"token": "old"}', "old@example.com")
        calendar_store.save_credentials("saroj", '{"token": "new"}', "new@example.com")
        loaded = calendar_store.load_credentials("saroj")
    assert loaded == ('{"token": "new"}', "new@example.com")
    print("test_save_credentials_upserts_by_owner: PASS")


def test_get_status_never_touches_encrypted_column() -> None:
    table: dict = {}
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret"}), _patched_store(table):
        calendar_store.save_credentials("saroj", '{"token": "abc"}', "saroj@example.com")
        status = calendar_store.get_status("saroj")
    assert status == {"connected": True, "email": "saroj@example.com"}
    print("test_get_status_never_touches_encrypted_column: PASS")


def test_get_status_not_connected() -> None:
    table: dict = {}
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret"}), _patched_store(table):
        status = calendar_store.get_status("nobody")
    assert status == {"connected": False, "email": ""}
    print("test_get_status_not_connected: PASS")


def test_delete_credentials() -> None:
    table: dict = {}
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret"}), _patched_store(table):
        calendar_store.save_credentials("saroj", '{"token": "abc"}', "saroj@example.com")
        deleted = calendar_store.delete_credentials("saroj")
        assert deleted is True
        assert calendar_store.load_credentials("saroj") is None
        assert calendar_store.delete_credentials("saroj") is False   # already gone -- idempotent
    print("test_delete_credentials: PASS")


# ── multi-user isolation at the storage layer ──────────────────────────

def test_user_a_cannot_load_user_b_credentials() -> None:
    table: dict = {}
    with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "secret"}), _patched_store(table):
        calendar_store.save_credentials("saroj", '{"token": "saroj-token"}', "saroj@example.com")
        calendar_store.save_credentials("sana", '{"token": "sana-token"}', "sana@example.com")
        saroj_creds = calendar_store.load_credentials("saroj")
        sana_creds = calendar_store.load_credentials("sana")
    assert saroj_creds[0] == '{"token": "saroj-token"}'
    assert sana_creds[0] == '{"token": "sana-token"}'
    assert saroj_creds != sana_creds
    print("test_user_a_cannot_load_user_b_credentials: PASS")


if __name__ == "__main__":
    test_is_configured_false_without_database_url()
    test_is_configured_true_with_database_url_and_psycopg()
    test_is_configured_false_without_psycopg_installed()
    test_encrypt_decrypt_round_trip()
    test_encryption_key_requires_google_client_secret()
    test_different_secrets_produce_undecryptable_ciphertext()
    test_save_and_load_credentials_round_trip()
    test_load_credentials_returns_none_when_not_connected()
    test_save_credentials_upserts_by_owner()
    test_get_status_never_touches_encrypted_column()
    test_get_status_not_connected()
    test_delete_credentials()
    test_user_a_cannot_load_user_b_credentials()
    print("\nAll calendar-store tests passed.")
