"""
tests/test_memory_personality.py -- Phase 2 (human-like memory behavior)
regression tests: automatic remembering (no permission-asking baked into
the mechanism), natural-language personality instructions present in
core/prompt.txt, and upcoming_events_for_prompt()'s formatting + privacy
scoping (shared dates for everyone, personal dates only for their own
owner — never another user's).

What this file does NOT try to test: Gemini's actual generated wording
(that's a live-model behavior, not something a unit test can assert on).
Where the requirement is purely a prompt-instruction ("ask naturally when
ambiguous", "never say 'database'"), this file checks the INSTRUCTION
TEXT is present and correctly worded, not the model's eventual output.

Run with:
    .venv/Scripts/python.exe -m tests.test_memory_personality
"""
from pathlib import Path
from unittest.mock import patch

from main import TOOL_DECLARATIONS
from memory import memory_manager
from tests._fake_postgres_repo import FakePostgresRepo

PROMPT_PATH = Path(__file__).resolve().parent.parent / "core" / "prompt.txt"


def _prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


# ── automatic remembering, no permission-asking baked in ────────────────

def test_save_memory_tool_declaration_never_requires_confirmation() -> None:
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "save_memory")
    desc = decl["description"].lower()
    assert "silently" in desc
    assert "do not announce" in desc or "do not" in desc
    forbidden = ["ask the user first", "ask for permission", "confirm before saving"]
    for phrase in forbidden:
        assert phrase not in desc
    print("test_save_memory_tool_declaration_never_requires_confirmation: PASS")


def test_prompt_contains_memory_behavior_section() -> None:
    text = _prompt_text()
    assert "MEMORY BEHAVIOR" in text
    assert "shared=false" in text
    assert "shared=true" in text
    print("test_prompt_contains_memory_behavior_section: PASS")


def test_prompt_forbids_technical_memory_language() -> None:
    text = _prompt_text()
    # The forbidden phrases are allowed to appear ONLY as the quoted
    # examples inside the instruction telling Gemini not to say them.
    assert '"save this to the database"' in text
    assert '"memory entry"' in text
    # And the instruction telling it not to must actually be there.
    assert "Never say" in text
    print("test_prompt_forbids_technical_memory_language: PASS")


def test_prompt_instructs_automatic_remembering_without_asking() -> None:
    text = _prompt_text()
    assert 'Don\'t ask "should I save this?"' in text or "Don't ask" in text
    assert "silently" in text
    print("test_prompt_instructs_automatic_remembering_without_asking: PASS")


def test_prompt_instructs_ambiguous_confirmation_flow() -> None:
    text = _prompt_text()
    assert "Ambiguous or sensitive" in text
    assert "ask" in text.lower()
    print("test_prompt_instructs_ambiguous_confirmation_flow: PASS")


def test_prompt_instructs_third_person_phrasing_for_shared() -> None:
    text = _prompt_text()
    assert "third person" in text
    print("test_prompt_instructs_third_person_phrasing_for_shared: PASS")


# ── upcoming_events_for_prompt(): formatting + privacy scoping ──────────

def test_upcoming_events_empty_when_postgres_not_configured() -> None:
    fake = FakePostgresRepo()
    fake.configured = False
    with patch("memory.memory_manager.postgres_repo", fake):
        assert memory_manager.upcoming_events_for_prompt("saroj") == ""
    print("test_upcoming_events_empty_when_postgres_not_configured: PASS")


def test_upcoming_events_empty_when_nothing_upcoming() -> None:
    fake = FakePostgresRepo()
    with patch("memory.memory_manager.postgres_repo", fake):
        assert memory_manager.upcoming_events_for_prompt("saroj") == ""
    print("test_upcoming_events_empty_when_nothing_upcoming: PASS")


def test_upcoming_events_formats_shared_and_own_personal_dates() -> None:
    fake = FakePostgresRepo()
    fake.rows[("shared", "saroj", "identity", "birthday")] = {
        "content": "October 25", "importance": 3, "entities": [],
        "event_date": "2026-08-25", "source": "conversation", "updated_at": "2026-08-24",
    }
    with patch("memory.memory_manager.postgres_repo", fake), \
         patch("memory.memory_manager.datetime") as mock_dt:
        from datetime import datetime as real_datetime
        mock_dt.now.return_value = real_datetime(2026, 8, 24)
        mock_dt.strptime = real_datetime.strptime
        text = memory_manager.upcoming_events_for_prompt("sana", within_days=2)
    assert "[UPCOMING DATES" in text
    assert "October 25" in text
    assert "tomorrow" in text
    assert "saroj's" in text.lower()   # Sana reading it must see it's Saroj's, not her own
    print("test_upcoming_events_formats_shared_and_own_personal_dates: PASS")


def test_upcoming_events_never_leaks_another_users_personal_date() -> None:
    """A personal (non-shared) dated fact belonging to Saroj must never
    appear in Sana's upcoming-events context."""
    fake = FakePostgresRepo()
    fake.rows[("personal", "saroj", "identity", "birthday")] = {
        "content": "October 25", "importance": 3, "entities": [],
        "event_date": "2026-08-25", "source": "conversation", "updated_at": "2026-08-24",
    }
    with patch("memory.memory_manager.postgres_repo", fake):
        text = memory_manager.upcoming_events_for_prompt("sana", within_days=2)
    assert text == "", "Saroj's PERSONAL birthday must never appear in Sana's context"
    print("test_upcoming_events_never_leaks_another_users_personal_date: PASS")


def test_upcoming_events_includes_owners_own_personal_date() -> None:
    fake = FakePostgresRepo()
    fake.rows[("personal", "saroj", "identity", "birthday")] = {
        "content": "October 25", "importance": 3, "entities": [],
        "event_date": "2026-08-25", "source": "conversation", "updated_at": "2026-08-24",
    }
    with patch("memory.memory_manager.postgres_repo", fake), \
         patch("memory.memory_manager.datetime") as mock_dt:
        from datetime import datetime as real_datetime
        mock_dt.now.return_value = real_datetime(2026, 8, 24)
        mock_dt.strptime = real_datetime.strptime
        text = memory_manager.upcoming_events_for_prompt("saroj", within_days=2)
    assert "October 25" in text, "a user's OWN personal dated fact must still surface to them"
    print("test_upcoming_events_includes_owners_own_personal_date: PASS")


if __name__ == "__main__":
    test_save_memory_tool_declaration_never_requires_confirmation()
    test_prompt_contains_memory_behavior_section()
    test_prompt_forbids_technical_memory_language()
    test_prompt_instructs_automatic_remembering_without_asking()
    test_prompt_instructs_ambiguous_confirmation_flow()
    test_prompt_instructs_third_person_phrasing_for_shared()
    test_upcoming_events_empty_when_postgres_not_configured()
    test_upcoming_events_empty_when_nothing_upcoming()
    test_upcoming_events_formats_shared_and_own_personal_dates()
    test_upcoming_events_never_leaks_another_users_personal_date()
    test_upcoming_events_includes_owners_own_personal_date()
    print("\nAll memory-personality tests passed.")
