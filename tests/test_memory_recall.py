"""
tests/test_memory_recall.py -- the recall_memory tool + the real gap it
closes: format_memory_for_prompt()'s own per-category caps (15
preferences, 8 projects, etc., plus a 2000-char safety net) mean a fact
just past the cap was previously invisible AND unreachable, not just
deprioritized -- Gemini had no way to know it existed. recall_memory
searches the FULL store locally (no network, no second model), and
format_memory_for_prompt() now surfaces an "N more, not shown" hint
naming what got left out so Gemini knows this tool is worth calling.

Run with:
    .venv/Scripts/python.exe -m tests.test_memory_recall
"""
from memory import memory_manager as mm
from main import TOOL_DECLARATIONS


# ── recall_memory itself ─────────────────────────────────────────────

def test_recall_memory_finds_a_match_by_key() -> None:
    mem = {"notes": {"favorite_color": {"value": "blue"}}}
    assert "blue" in mm.recall_memory("favorite_color", memory=mem)
    print("test_recall_memory_finds_a_match_by_key: PASS")


def test_recall_memory_finds_a_match_by_value() -> None:
    mem = {"notes": {"favorite_color": {"value": "blue"}}}
    assert "favorite color" in mm.recall_memory("blue", memory=mem).lower()
    print("test_recall_memory_finds_a_match_by_value: PASS")


def test_recall_memory_is_honest_about_no_match_never_invents_one() -> None:
    mem = {"notes": {"favorite_color": {"value": "blue"}}}
    result = mm.recall_memory("something totally unrelated xyz", memory=mem)
    assert "nothing stored matches" in result.lower()
    print("test_recall_memory_is_honest_about_no_match_never_invents_one: PASS")


def test_recall_memory_with_no_query_asks_instead_of_guessing() -> None:
    assert "no search term" in mm.recall_memory("", memory={}).lower()
    print("test_recall_memory_with_no_query_asks_instead_of_guessing: PASS")


def test_recall_memory_defaults_to_the_real_load_memory_when_none_given() -> None:
    """The `memory=` param exists for testability (mirrors
    format_memory_for_prompt()'s own parameterized signature) — the real
    dispatch path (main.py) never passes it, so this locks in the
    default actually reaches the real store."""
    from unittest.mock import patch
    with patch.object(mm, "load_memory", return_value={"notes": {"x": {"value": "y"}}}) as m:
        result = mm.recall_memory("x")
    m.assert_called_once()
    assert "y" in result
    print("test_recall_memory_defaults_to_the_real_load_memory_when_none_given: PASS")


# ── format_memory_for_prompt's omitted-keys hint ─────────────────────

def test_omitted_hint_appears_when_a_category_is_truncated() -> None:
    mem = {"preferences": {f"pref_{i}": {"value": f"v{i}"} for i in range(18)}}
    text = mm.format_memory_for_prompt(mem)
    assert "recall_memory" in text
    assert "pref 17" in text  # the 3 cut off (cap is 15, 18 given)
    print("test_omitted_hint_appears_when_a_category_is_truncated: PASS")


def test_omitted_hint_absent_when_nothing_was_cut() -> None:
    mem = {"preferences": {"pref_1": {"value": "v1"}}}
    text = mm.format_memory_for_prompt(mem)
    assert "recall_memory" not in text
    print("test_omitted_hint_absent_when_nothing_was_cut: PASS")


def test_every_shown_preference_is_a_real_one_not_a_placeholder() -> None:
    """The cap itself is unchanged behavior — still shows the first N,
    never fabricates a summary in place of the real values."""
    mem = {"preferences": {f"pref_{i}": {"value": f"v{i}"} for i in range(18)}}
    text = mm.format_memory_for_prompt(mem)
    assert "Pref 0: v0" in text
    assert "Pref 14: v14" in text
    print("test_every_shown_preference_is_a_real_one_not_a_placeholder: PASS")


# ── main.py wiring ────────────────────────────────────────────────────

def test_recall_memory_tool_is_declared_with_a_required_query_param() -> None:
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "recall_memory")
    assert decl["parameters"]["required"] == ["query"]
    assert "query" in decl["parameters"]["properties"]
    print("test_recall_memory_tool_is_declared_with_a_required_query_param: PASS")


def test_recall_memory_dispatches_through_execute_tool() -> None:
    import asyncio
    from unittest.mock import patch
    from types import SimpleNamespace
    from core.headless_surface import HeadlessSurface
    from main import JarvisLive

    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        fc = SimpleNamespace(id="fc1", name="recall_memory", args={"query": "blue"})
        with patch("main.recall_memory", return_value="notes/favorite color: blue") as m:
            resp = await jarvis._execute_tool(fc)
        m.assert_called_once_with("blue")
        assert resp.response["result"] == "notes/favorite color: blue"

    asyncio.run(_run())
    print("test_recall_memory_dispatches_through_execute_tool: PASS")


if __name__ == "__main__":
    test_recall_memory_finds_a_match_by_key()
    test_recall_memory_finds_a_match_by_value()
    test_recall_memory_is_honest_about_no_match_never_invents_one()
    test_recall_memory_with_no_query_asks_instead_of_guessing()
    test_recall_memory_defaults_to_the_real_load_memory_when_none_given()
    test_omitted_hint_appears_when_a_category_is_truncated()
    test_omitted_hint_absent_when_nothing_was_cut()
    test_every_shown_preference_is_a_real_one_not_a_placeholder()
    test_recall_memory_tool_is_declared_with_a_required_query_param()
    test_recall_memory_dispatches_through_execute_tool()
    print("\nAll memory-recall tests passed.")
