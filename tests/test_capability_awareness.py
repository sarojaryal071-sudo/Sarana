"""
tests/test_capability_awareness.py -- Phase 3 (capability awareness &
honest responses) regression tests: a web/headless session must never
attempt (or claim success for) a tool that needs local desktop access,
desktop itself must be completely unaffected, and the system prompt must
tell Gemini which surface it's on.

Run with:
    .venv/Scripts/python.exe -m tests.test_capability_awareness
"""
import asyncio
from unittest.mock import patch

import main
from core.headless_surface import HeadlessSurface
from main import DESKTOP_ONLY_TOOLS, TOOL_DECLARATIONS, JarvisLive


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


_DECLARED_NAMES = {t["name"] for t in TOOL_DECLARATIONS}


# ── DESKTOP_ONLY_TOOLS sanity ─────────────────────────────────────────────

def test_every_desktop_only_tool_is_a_real_declared_tool() -> None:
    unknown = DESKTOP_ONLY_TOOLS - _DECLARED_NAMES
    assert not unknown, f"DESKTOP_ONLY_TOOLS references undeclared tool(s): {unknown}"
    print("test_every_desktop_only_tool_is_a_real_declared_tool: PASS")


def test_universal_tools_are_not_marked_desktop_only() -> None:
    for name in ("save_memory", "web_search", "manage_monitor"):
        assert name not in DESKTOP_ONLY_TOOLS, f"{name} must remain available on web"
    print("test_universal_tools_are_not_marked_desktop_only: PASS")


# ── the actual gate in _execute_tool() ───────────────────────────────────

def test_web_session_blocks_desktop_only_tool_without_calling_it() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        with patch.object(main, "open_app") as mock_open_app:
            fc = _FakeFunctionCall("open_app", {"app_name": "notepad"})
            resp = await jarvis._execute_tool(fc)
            mock_open_app.assert_not_called()
        result_text = resp.response["result"]
        assert "[CAPABILITY_UNAVAILABLE]" in result_text
        assert "open_app" in result_text
    asyncio.run(_run())
    print("test_web_session_blocks_desktop_only_tool_without_calling_it: PASS")


def test_capability_unavailable_response_never_claims_success() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        with patch.object(main, "computer_settings"):
            fc = _FakeFunctionCall("computer_settings", {"action": "volume_up"})
            resp = await jarvis._execute_tool(fc)
        result_text = resp.response["result"].lower()
        assert "done" != result_text.strip()
        assert "capability_unavailable" in result_text
    asyncio.run(_run())
    print("test_capability_unavailable_response_never_claims_success: PASS")


def test_desktop_session_still_calls_the_real_tool_unaffected() -> None:
    """The exact same tool, on desktop (auto_start=True), must be
    completely unaffected by the web gate — desktop capabilities must
    not regress."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())   # auto_start defaults True
        with patch.object(main, "open_app", return_value="Opened notepad.") as mock_open_app:
            fc = _FakeFunctionCall("open_app", {"app_name": "notepad"})
            resp = await jarvis._execute_tool(fc)
            mock_open_app.assert_called_once()
        assert resp.response["result"] == "Opened notepad."
    asyncio.run(_run())
    print("test_desktop_session_still_calls_the_real_tool_unaffected: PASS")


def test_universal_tool_still_works_on_web() -> None:
    """web_search (a network API call — genuinely available either way)
    must NOT be blocked by the capability gate."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        with patch.object(main, "web_search_action", return_value="3 results found.") as mock_search:
            fc = _FakeFunctionCall("web_search", {"query": "nepal weather", "mode": "search"})
            resp = await jarvis._execute_tool(fc)
            mock_search.assert_called_once()
        assert resp.response["result"] == "3 results found."
    asyncio.run(_run())
    print("test_universal_tool_still_works_on_web: PASS")


def test_genuine_tool_failure_still_propagates_honestly() -> None:
    """Phase 3 doesn't touch the EXISTING honest-failure path — a tool
    that genuinely raises must still surface a real failure message, on
    either surface, exactly as before."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())   # desktop — not gated at all
        with patch.object(main, "web_search_action", side_effect=RuntimeError("network down")):
            fc = _FakeFunctionCall("web_search", {"query": "x", "mode": "search"})
            resp = await jarvis._execute_tool(fc)
        assert "failed" in resp.response["result"].lower()
        assert "network down" in resp.response["result"]
    asyncio.run(_run())
    print("test_genuine_tool_failure_still_propagates_honestly: PASS")


# ── system prompt capability context ─────────────────────────────────────

def test_build_config_tells_gemini_its_a_web_session() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    config = jarvis._build_config()
    text = config.system_instruction
    assert "[CAPABILITIES]" in text
    assert "web session" in text.lower() or "web" in text.lower()
    assert "open_app" not in text   # examples are described in plain language, not tool names
    print("test_build_config_tells_gemini_its_a_web_session: PASS")


def test_build_config_tells_gemini_its_desktop() -> None:
    jarvis = JarvisLive(HeadlessSurface())   # auto_start=True
    config = jarvis._build_config()
    text = config.system_instruction
    assert "[CAPABILITIES]" in text
    assert "desktop application" in text.lower()
    print("test_build_config_tells_gemini_its_desktop: PASS")


if __name__ == "__main__":
    test_every_desktop_only_tool_is_a_real_declared_tool()
    test_universal_tools_are_not_marked_desktop_only()
    test_web_session_blocks_desktop_only_tool_without_calling_it()
    test_capability_unavailable_response_never_claims_success()
    test_desktop_session_still_calls_the_real_tool_unaffected()
    test_universal_tool_still_works_on_web()
    test_genuine_tool_failure_still_propagates_honestly()
    test_build_config_tells_gemini_its_a_web_session()
    test_build_config_tells_gemini_its_desktop()
    print("\nAll capability-awareness tests passed.")
