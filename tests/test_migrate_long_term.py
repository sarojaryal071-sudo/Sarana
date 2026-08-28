"""
tests/test_migrate_long_term.py -- idempotency and correctness of the
one-time long_term.json -> PostgreSQL import (memory/migrate_long_term.py).
Uses tests/_fake_postgres_repo.py instead of a live database.

Run with:
    .venv/Scripts/python.exe -m tests.test_migrate_long_term
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from memory import legacy_file_store, migrate_long_term
from tests._fake_postgres_repo import FakePostgresRepo


def _write_legacy_file(path: Path, content: dict) -> None:
    path.write_text(json.dumps(content), encoding="utf-8")


def test_migrate_imports_existing_facts_as_shared() -> None:
    fake = FakePostgresRepo()
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "long_term.json"
        _write_legacy_file(mem_path, {
            "identity": {"name": {"value": "Saroj", "updated": "2026-08-24"}},
            "relationships": {"wife_mood": {"value": "angry", "updated": "2026-08-24"}},
        })
        with patch.object(legacy_file_store, "MEMORY_PATH", mem_path), \
             patch("memory.migrate_long_term.postgres_repo", fake):
            result = migrate_long_term.migrate_if_needed()

        assert result["ran"] is True
        assert result["imported"] == 2
        assert fake.rows[("shared", "", "identity", "name")]["content"] == "Saroj"
        assert fake.rows[("shared", "", "relationships", "wife_mood")]["content"] == "angry"
    print("test_migrate_imports_existing_facts_as_shared: PASS")


def test_migrate_is_idempotent_across_multiple_runs() -> None:
    fake = FakePostgresRepo()
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "long_term.json"
        _write_legacy_file(mem_path, {"identity": {"name": {"value": "Saroj"}}})
        with patch.object(legacy_file_store, "MEMORY_PATH", mem_path), \
             patch("memory.migrate_long_term.postgres_repo", fake):
            first = migrate_long_term.migrate_if_needed()
            second = migrate_long_term.migrate_if_needed()
            third = migrate_long_term.migrate_if_needed()

        assert first["ran"] is True and first["imported"] == 1
        assert second["ran"] is False and second["imported"] == 0
        assert third["ran"] is False and third["imported"] == 0
        # Exactly one row for the fact — never duplicated across runs.
        assert len([r for r in fake.rows if r[2] == "identity" and r[3] == "name"]) == 1
    print("test_migrate_is_idempotent_across_multiple_runs: PASS")


def test_migrate_skips_cleanly_when_postgres_not_configured() -> None:
    fake = FakePostgresRepo()
    fake.configured = False
    with patch("memory.migrate_long_term.postgres_repo", fake):
        result = migrate_long_term.migrate_if_needed()
    assert result["ran"] is False
    assert not fake.rows
    print("test_migrate_skips_cleanly_when_postgres_not_configured: PASS")


def test_migrate_retries_when_source_file_was_missing_last_time() -> None:
    """Regression test for the exact bug found against the real Neon
    database: a run where memory/long_term.json didn't exist on that
    machine must NOT permanently mark the migration complete — a later
    run (from a machine that actually has the file) must still import
    the real facts."""
    fake = FakePostgresRepo()
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "long_term.json"   # deliberately never created

        with patch.object(legacy_file_store, "MEMORY_PATH", mem_path), \
             patch("memory.migrate_long_term.postgres_repo", fake):
            first = migrate_long_term.migrate_if_needed()
        assert first["ran"] is True
        assert first["imported"] == 0
        assert first["source_found"] is False

        # The marker exists now, but must not block a real import once
        # the file actually shows up.
        _write_legacy_file(mem_path, {"identity": {"name": {"value": "Saroj"}}})
        with patch.object(legacy_file_store, "MEMORY_PATH", mem_path), \
             patch("memory.migrate_long_term.postgres_repo", fake):
            second = migrate_long_term.migrate_if_needed()
        assert second["ran"] is True
        assert second["imported"] == 1
        assert second["source_found"] is True
        assert fake.rows[("shared", "", "identity", "name")]["content"] == "Saroj"

        # NOW it's genuinely complete — a third run must skip.
        with patch.object(legacy_file_store, "MEMORY_PATH", mem_path), \
             patch("memory.migrate_long_term.postgres_repo", fake):
            third = migrate_long_term.migrate_if_needed()
        assert third["ran"] is False
    print("test_migrate_retries_when_source_file_was_missing_last_time: PASS")


def test_migrate_heals_old_format_marker() -> None:
    """The pre-fix version of this module wrote a bare "migrated" string
    as the marker's content, with no record of whether the source file
    was ever found — exactly what the real Neon database had. That old
    marker must be treated as incomplete and trigger a real import, not
    block one forever."""
    fake = FakePostgresRepo()
    fake.rows[("shared", "", "_migration", "long_term_json_v1")] = {
        "content": "migrated", "importance": 3, "entities": [], "event_date": None,
        "source": "migration:long_term.json", "updated_at": "2026-08-24",
    }
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "long_term.json"
        _write_legacy_file(mem_path, {"identity": {"name": {"value": "Saroj"}}})
        with patch.object(legacy_file_store, "MEMORY_PATH", mem_path), \
             patch("memory.migrate_long_term.postgres_repo", fake):
            result = migrate_long_term.migrate_if_needed()
        assert result["ran"] is True
        assert result["imported"] == 1
        assert fake.rows[("shared", "", "identity", "name")]["content"] == "Saroj"
    print("test_migrate_heals_old_format_marker: PASS")


def test_migrate_never_raises_on_repo_failure() -> None:
    class _BrokenRepo(FakePostgresRepo):
        def upsert_memory(self, *a, **kw):
            raise RuntimeError("connection refused")

    fake = _BrokenRepo()
    with tempfile.TemporaryDirectory() as td:
        mem_path = Path(td) / "long_term.json"
        _write_legacy_file(mem_path, {"identity": {"name": {"value": "Saroj"}}})
        with patch.object(legacy_file_store, "MEMORY_PATH", mem_path), \
             patch("memory.migrate_long_term.postgres_repo", fake):
            result = migrate_long_term.migrate_if_needed()   # must not raise
        assert result["ran"] is True
        assert "reason" in result
    print("test_migrate_never_raises_on_repo_failure: PASS")


if __name__ == "__main__":
    test_migrate_imports_existing_facts_as_shared()
    test_migrate_is_idempotent_across_multiple_runs()
    test_migrate_retries_when_source_file_was_missing_last_time()
    test_migrate_heals_old_format_marker()
    test_migrate_skips_cleanly_when_postgres_not_configured()
    test_migrate_never_raises_on_repo_failure()
    print("\nAll migration tests passed.")
