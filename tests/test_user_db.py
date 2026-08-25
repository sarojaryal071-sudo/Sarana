"""
tests/test_user_db.py — direct unit tests for users/user_db.py: SQLite
schema creation, idempotent seeding, alias resolution, and PIN hashing/
verification. Every test patches user_db.DB_PATH to an isolated temp file
so nothing here ever touches the real data/sarana.db.

Run with:
    .venv/Scripts/python.exe -m tests.test_user_db
"""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from users import user_db


def _isolated_db():
    """Context-manager-free helper: returns (tempdir, patcher) — caller
    must use `with patch.object(user_db, "DB_PATH", ...)` directly; kept
    as a plain helper since every test needs the same temp path."""
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "sarana.db"
    return tmp, db_path


# ── database creation ──────────────────────────────────────────────────

def test_init_db_creates_file_and_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "nested" / "dir" / "sarana.db"
        with patch.object(user_db, "DB_PATH", db_path):
            assert not db_path.exists()
            user_db.init_db()
            assert db_path.exists()
            assert db_path.parent.is_dir()
    print("test_init_db_creates_file_and_directory: PASS")


def test_init_db_creates_expected_schema() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        conn = sqlite3.connect(db_path)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            assert {"users", "user_aliases"}.issubset(tables)

            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            required = {
                "id", "username", "pin_hash", "nickname", "gender",
                "assistant_name", "voice_preference", "language_preference",
                "created_at", "updated_at",
            }
            assert required.issubset(cols), cols
        finally:
            conn.close()
    print("test_init_db_creates_expected_schema: PASS")


# ── idempotent seeding ──────────────────────────────────────────────────

def test_seeding_is_idempotent_across_multiple_init_calls() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        user_db.init_db()
        user_db.init_db()
        conn = sqlite3.connect(db_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            assert count == 2, f"expected exactly 2 seeded users, got {count}"
            alias_count = conn.execute("SELECT COUNT(*) FROM user_aliases").fetchone()[0]
            assert alias_count == 4, f"expected 4 aliases total (3 + 1), got {alias_count}"
        finally:
            conn.close()
    print("test_seeding_is_idempotent_across_multiple_init_calls: PASS")


def test_reinit_preserves_existing_user_data() -> None:
    """A profile edited after seeding (simulating a future admin/manual
    edit) must survive a later init_db() call — seeding must never
    overwrite an existing row."""
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE users SET nickname = 'Changed' WHERE username = 'saroj'")
            conn.commit()
        finally:
            conn.close()

        user_db.init_db()   # must NOT reset the edited row

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT nickname FROM users WHERE username = 'saroj'").fetchone()
            assert row[0] == "Changed", "reseed must not clobber existing user data"
        finally:
            conn.close()
    print("test_reinit_preserves_existing_user_data: PASS")


# ── seeded user 1: Bandana / Sana / Radhe -> one profile ────────────────

def test_bandana_sana_radhe_all_resolve_to_same_profile() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        p1 = user_db.authenticate("Bandana", "2060")
        p2 = user_db.authenticate("Sana", "2060")
        p3 = user_db.authenticate("Radhe", "2060")
        assert p1 is not None and p2 is not None and p3 is not None
        assert p1 == p2 == p3
        assert p1["nickname"] == "Sana"
        assert p1["pronunciation"] == "Saanaa"
    print("test_bandana_sana_radhe_all_resolve_to_same_profile: PASS")


def test_case_insensitive_username_lookup() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        for name in ("SAROJ", "saroj", "SaRoJ", "  Saroj  "):
            assert user_db.authenticate(name, "2057") is not None, name
    print("test_case_insensitive_username_lookup: PASS")


def test_user1_correct_pin_accepted() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        assert user_db.authenticate("Sana", "2060") is not None
    print("test_user1_correct_pin_accepted: PASS")


def test_user1_incorrect_pin_rejected() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        assert user_db.authenticate("Sana", "0000") is None
        assert user_db.authenticate("Bandana", "9999") is None
        assert user_db.authenticate("Radhe", "206") is None
    print("test_user1_incorrect_pin_rejected: PASS")


# ── seeded user 2: Saroj ─────────────────────────────────────────────────

def test_saroj_correct_pin_accepted() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        profile = user_db.authenticate("Saroj", "2057")
        assert profile is not None
        assert profile["nickname"] == "Saroj"
        assert profile["gender"] == "male"
    print("test_saroj_correct_pin_accepted: PASS")


def test_saroj_incorrect_pin_rejected() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        assert user_db.authenticate("Saroj", "0000") is None
    print("test_saroj_incorrect_pin_rejected: PASS")


def test_unknown_username_rejected() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        assert user_db.authenticate("NobodyHere", "2057") is None
    print("test_unknown_username_rejected: PASS")


# ── profile field correctness ─────────────────────────────────────────────

def test_profile_values_loaded_correctly_for_both_users() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()

        sana = user_db.authenticate("Sana", "2060")
        assert sana["nickname"] == "Sana"
        assert sana["pronunciation"] == "Saanaa"
        assert sana["gender"] == "female"
        assert sana["assistant_name"] == "Kanha"
        assert sana["voice_preference"] == "Male"
        assert sana["language_preference"] == "Nepali"

        saroj = user_db.authenticate("Saroj", "2057")
        assert saroj["nickname"] == "Saroj"
        assert saroj["gender"] == "male"
        assert saroj["assistant_name"] == "Sara"
        assert saroj["voice_preference"] == "Female"
        assert saroj["language_preference"] == "Nepali"
    print("test_profile_values_loaded_correctly_for_both_users: PASS")


def test_pin_hash_never_returned_by_authenticate() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        profile = user_db.authenticate("Saroj", "2057")
        assert "pin_hash" not in profile
    print("test_pin_hash_never_returned_by_authenticate: PASS")


def test_plaintext_pin_never_stored_in_database() -> None:
    tmp, db_path = _isolated_db()
    with tmp, patch.object(user_db, "DB_PATH", db_path):
        user_db.init_db()
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT pin_hash FROM users").fetchall()
        finally:
            conn.close()
        stored = [r[0] for r in rows]
        assert "2060" not in stored
        assert "2057" not in stored
        for pin_hash in stored:
            assert "$" in pin_hash   # salt$digest format, not a raw value
    print("test_plaintext_pin_never_stored_in_database: PASS")


# ── hashing internals ──────────────────────────────────────────────────

def test_hash_pin_uses_distinct_salts() -> None:
    """Same PIN, two calls -> different stored hashes (random salt), but
    both still verify correctly."""
    h1 = user_db._hash_pin("1234")
    h2 = user_db._hash_pin("1234")
    assert h1 != h2
    assert user_db._verify_pin("1234", h1)
    assert user_db._verify_pin("1234", h2)
    assert not user_db._verify_pin("4321", h1)
    print("test_hash_pin_uses_distinct_salts: PASS")


def test_verify_pin_rejects_malformed_stored_value() -> None:
    assert user_db._verify_pin("1234", "not-a-valid-hash") is False
    assert user_db._verify_pin("1234", "") is False
    print("test_verify_pin_rejects_malformed_stored_value: PASS")


if __name__ == "__main__":
    test_init_db_creates_file_and_directory()
    test_init_db_creates_expected_schema()
    test_seeding_is_idempotent_across_multiple_init_calls()
    test_reinit_preserves_existing_user_data()
    test_bandana_sana_radhe_all_resolve_to_same_profile()
    test_case_insensitive_username_lookup()
    test_user1_correct_pin_accepted()
    test_user1_incorrect_pin_rejected()
    test_saroj_correct_pin_accepted()
    test_saroj_incorrect_pin_rejected()
    test_unknown_username_rejected()
    test_profile_values_loaded_correctly_for_both_users()
    test_pin_hash_never_returned_by_authenticate()
    test_plaintext_pin_never_stored_in_database()
    test_hash_pin_uses_distinct_salts()
    test_verify_pin_rejects_malformed_stored_value()
    print("\nAll user_db tests passed.")
