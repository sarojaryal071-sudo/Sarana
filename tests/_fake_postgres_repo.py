"""
tests/_fake_postgres_repo.py -- an in-memory stand-in for
memory/postgres_repo.py's public API, used by the memory-ownership test
suite instead of a live PostgreSQL database (per this feature's own
testing requirement: "Use mocks/fakes... rather than requiring a live
production database for the normal test suite").

Deliberately named with a leading underscore and NOT test_*.py — this is a
test helper/double, not a test module itself, so the test runner never
tries to execute it directly.

Implements exactly the same function signatures as memory/postgres_repo.py
(fetch_memories, upsert_memory, delete_memory, save_session_summary,
pop_last_session, init_schema, is_configured) so it can be swapped in via
`unittest.mock.patch("memory.memory_cache.postgres_repo", fake)` /
`patch("memory.memory_manager.postgres_repo", fake)` /
`patch("memory.migrate_long_term.postgres_repo", fake)` — those modules
each hold `postgres_repo` as a plain module-level name (`from memory
import ... postgres_repo`), so patching it swaps every call site at once.
"""
_MANAGED_CATEGORIES = (
    "identity", "preferences", "projects", "relationships", "wishes", "notes",
)


class FakePostgresRepo:
    def __init__(self):
        self.configured = True
        self.rows: dict[tuple, dict] = {}     # (scope, owner, category, key) -> row
        self.sessions: list[dict] = []        # [{owner, date, summary, language}]
        self.fail_next_n_writes = 0           # simulate transient outages
        self.schema_init_calls = 0

    # ── configuration ────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return self.configured

    def init_schema(self) -> None:
        self.schema_init_calls += 1

    def _maybe_fail(self) -> None:
        if self.fail_next_n_writes > 0:
            self.fail_next_n_writes -= 1
            raise RuntimeError("simulated PostgreSQL outage")

    # ── memories ─────────────────────────────────────────────────────────

    def fetch_memories(self, scope: str, owner: str = "") -> dict:
        out = {cat: {} for cat in _MANAGED_CATEGORIES}
        for (s, o, cat, key), row in self.rows.items():
            if s == scope and o == owner:
                out.setdefault(cat, {})[key] = {"value": row["content"], "updated": row["updated_at"]}
        return out

    def upsert_memory(self, scope, owner, category, key, content, *,
                       importance=3, entities=None, event_date=None, source=""):
        self._maybe_fail()
        self.rows[(scope, owner, category, key)] = {
            "content": content, "importance": importance,
            "entities": entities or [], "event_date": event_date,
            "source": source, "updated_at": "2026-08-26",
        }

    def delete_memory(self, scope, owner, category, key) -> bool:
        self._maybe_fail()
        return self.rows.pop((scope, owner, category, key), None) is not None

    def list_upcoming_dated_memories(self, owners, within_days=2) -> list:
        results = []
        for (scope, owner, category, key), row in self.rows.items():
            if row.get("event_date") and (scope == "shared" or owner in (owners or [])):
                results.append({
                    "scope": scope, "owner": owner, "category": category,
                    "key": key, "content": row["content"], "event_date": row["event_date"],
                })
        return results

    # ── session summaries ────────────────────────────────────────────────

    def save_session_summary(self, owner, summary_date, summary, language=""):
        self._maybe_fail()
        self.sessions.append({"owner": owner, "date": summary_date, "summary": summary, "language": language})

    def pop_last_session(self, owner):
        self._maybe_fail()
        for i in range(len(self.sessions) - 1, -1, -1):
            if self.sessions[i]["owner"] == owner:
                s = self.sessions.pop(i)
                entry = {"date": s["date"], "summary": s["summary"]}
                if s["language"]:
                    entry["language"] = s["language"]
                return entry
        return None
