"""
tests/test_jarvis_task_boundary.py — the real Gemini/JARVIS tool boundary
(docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md §7 / the "Full Execution
Architecture Implementation Mission").

The mission's own explicit requirement: "do not allow the architecture
to depend purely on 'Gemini will probably follow the system
instruction' ... enforce the boundary in the JARVIS dispatch/execution
layer so direct low-level calls cannot bypass the Task Engine in JARVIS
mode." This tests that the enforcement is REAL — a dispatch-layer check
in main.py's _execute_tool(), not just tool-description wording — using
the exact same JarvisLive/HeadlessSurface/_execute_tool harness already
established in tests/test_jarvis_mode.py.

Covers, for the two pilot-scope actions task_engine.py actually routes
(browser_control's go_to/search/new_tab, youtube_video's play):
  - JARVIS mode ON  -> the direct tool is intercepted, the REAL
    capability function is never called, Gemini is told to use
    jarvis_task instead.
  - JARVIS mode OFF -> completely unaffected, exactly as it already
    works in SARANA mode today (a real regression guard).
  - An action task_engine.py does NOT yet cover (e.g. browser_control's
    "click", youtube_video's "summarize") is NEVER redirected, even in
    JARVIS mode — redirecting it would break real, working functionality
    with nothing to replace it yet.
  - jarvis_task itself requires JARVIS mode, symmetric with the above.

Run with:
    .venv/Scripts/python.exe -m tests.test_jarvis_task_boundary
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive, TOOL_DECLARATIONS
import main as main_module


def _jarvis(jarvis_mode: bool) -> JarvisLive:
    j = JarvisLive(HeadlessSurface(), auto_start=True)
    j._jarvis_mode = jarvis_mode
    return j


def _fc(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(id="fc-1", name=name, args=args)


# ── jarvis_task: declared, JARVIS-mode gated ────────────────────────────

def test_jarvis_task_tool_is_declared() -> None:
    names = [t["name"] for t in TOOL_DECLARATIONS]
    assert "jarvis_task" in names
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "jarvis_task")
    assert decl["parameters"]["required"] == ["objective"]
    print("test_jarvis_task_tool_is_declared: PASS")

def test_jarvis_task_outside_jarvis_mode_is_rejected_and_calls_nothing() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=False)
        with patch.object(main_module.task_engine, "execute_task") as m_exec:
            fr = await jarvis._execute_tool(_fc("jarvis_task", objective="play a song on youtube"))
        m_exec.assert_not_called()
        assert "[JARVIS_MODE_REQUIRED]" in fr.response["result"]
    asyncio.run(_run())
    print("test_jarvis_task_outside_jarvis_mode_is_rejected_and_calls_nothing: PASS")

def test_jarvis_task_in_jarvis_mode_calls_the_task_engine() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module.task_engine, "execute_task", return_value="[VERIFIED_SUCCESS] Playing: X.") as m_exec:
            fr = await jarvis._execute_tool(_fc("jarvis_task", objective="play X on youtube"))
        m_exec.assert_called_once_with(parameters={"objective": "play X on youtube"})
        assert fr.response["result"] == "[VERIFIED_SUCCESS] Playing: X."
    asyncio.run(_run())
    print("test_jarvis_task_in_jarvis_mode_calls_the_task_engine: PASS")


# ── browser_control: redirected in JARVIS mode, only for routed actions ─

def test_browser_control_go_to_is_redirected_in_jarvis_mode() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module, "browser_control") as m_bc:
            fr = await jarvis._execute_tool(_fc("browser_control", action="go_to", url="https://example.com"))
        m_bc.assert_not_called()
        assert "[JARVIS_TASK_REQUIRED]" in fr.response["result"]
    asyncio.run(_run())
    print("test_browser_control_go_to_is_redirected_in_jarvis_mode: PASS")

def test_browser_control_search_is_redirected_in_jarvis_mode() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module, "browser_control") as m_bc:
            fr = await jarvis._execute_tool(_fc("browser_control", action="search", query="cats"))
        m_bc.assert_not_called()
        assert "[JARVIS_TASK_REQUIRED]" in fr.response["result"]
    asyncio.run(_run())
    print("test_browser_control_search_is_redirected_in_jarvis_mode: PASS")

def test_browser_control_go_to_is_unaffected_outside_jarvis_mode() -> None:
    # Regression guard: SARANA mode (the default) must behave EXACTLY as
    # it did before this mission — zero change to the direct path.
    async def _run():
        jarvis = _jarvis(jarvis_mode=False)
        with patch.object(main_module, "browser_control", return_value="Opened: https://example.com") as m_bc:
            fr = await jarvis._execute_tool(_fc("browser_control", action="go_to", url="https://example.com"))
        m_bc.assert_called_once()
        assert fr.response["result"] == "Opened: https://example.com"
    asyncio.run(_run())
    print("test_browser_control_go_to_is_unaffected_outside_jarvis_mode: PASS")

def test_browser_control_click_is_never_redirected_even_in_jarvis_mode() -> None:
    # task_engine.py doesn't route "click" yet — redirecting it would
    # break real functionality with no replacement. Must stay direct.
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module, "browser_control", return_value="Clicked: 'Sign in'") as m_bc:
            fr = await jarvis._execute_tool(_fc("browser_control", action="click", description="Sign in"))
        m_bc.assert_called_once()
        assert fr.response["result"] == "Clicked: 'Sign in'"
    asyncio.run(_run())
    print("test_browser_control_click_is_never_redirected_even_in_jarvis_mode: PASS")


# ── youtube_video: same pattern ─────────────────────────────────────────

def test_youtube_video_play_is_redirected_in_jarvis_mode() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module, "youtube_video") as m_yt:
            fr = await jarvis._execute_tool(_fc("youtube_video", action="play", query="Kafle"))
        m_yt.assert_not_called()
        assert "[JARVIS_TASK_REQUIRED]" in fr.response["result"]
    asyncio.run(_run())
    print("test_youtube_video_play_is_redirected_in_jarvis_mode: PASS")

def test_youtube_video_play_is_unaffected_outside_jarvis_mode() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=False)
        with patch.object(main_module, "youtube_video", return_value="[VERIFIED_SUCCESS] Playing: Kafle.") as m_yt:
            fr = await jarvis._execute_tool(_fc("youtube_video", action="play", query="Kafle"))
        m_yt.assert_called_once()
        assert fr.response["result"] == "[VERIFIED_SUCCESS] Playing: Kafle."
    asyncio.run(_run())
    print("test_youtube_video_play_is_unaffected_outside_jarvis_mode: PASS")

def test_youtube_video_summarize_is_never_redirected_even_in_jarvis_mode() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module, "youtube_video", return_value="Summary complete.") as m_yt:
            fr = await jarvis._execute_tool(_fc("youtube_video", action="summarize", url="https://youtube.com/watch?v=x"))
        m_yt.assert_called_once()
        assert fr.response["result"] == "Summary complete."
    asyncio.run(_run())
    print("test_youtube_video_summarize_is_never_redirected_even_in_jarvis_mode: PASS")


# ── computer_settings: Phase 3 (System capabilities) boundary + the ────
# real dispatch bug found and fixed alongside it (see main.py's own
# comment on the computer_settings branch): this branch was previously
# missing its own `result =` assignment entirely, so every call — success,
# failure, or CONFIRMATION_REQUIRED — silently fell through to this
# method's `result = "Done."` DEFAULT instead of the real return value.
# test_computer_settings_non_migrated_action_returns_its_real_result_not_done
# is the regression guard for that fix specifically.

def test_computer_settings_volume_set_is_redirected_in_jarvis_mode() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module, "computer_settings") as m_cs:
            fr = await jarvis._execute_tool(_fc("computer_settings", action="volume_set", value="40"))
        m_cs.assert_not_called()
        assert "[JARVIS_TASK_REQUIRED]" in fr.response["result"]
    asyncio.run(_run())
    print("test_computer_settings_volume_set_is_redirected_in_jarvis_mode: PASS")

def test_computer_settings_power_actions_are_redirected_in_jarvis_mode() -> None:
    async def _run():
        for action in ("sleep", "restart", "shutdown"):
            jarvis = _jarvis(jarvis_mode=True)
            with patch.object(main_module, "computer_settings") as m_cs:
                fr = await jarvis._execute_tool(_fc("computer_settings", action=action))
            m_cs.assert_not_called()
            assert "[JARVIS_TASK_REQUIRED]" in fr.response["result"], action
    asyncio.run(_run())
    print("test_computer_settings_power_actions_are_redirected_in_jarvis_mode: PASS")

def test_computer_settings_system_shortcut_is_redirected_in_jarvis_mode() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module, "computer_settings") as m_cs:
            fr = await jarvis._execute_tool(_fc("computer_settings", action="system_shortcut", value="check bluetooth devices"))
        m_cs.assert_not_called()
        assert "[JARVIS_TASK_REQUIRED]" in fr.response["result"]
    asyncio.run(_run())
    print("test_computer_settings_system_shortcut_is_redirected_in_jarvis_mode: PASS")

def test_computer_settings_non_migrated_action_is_never_redirected_even_in_jarvis_mode() -> None:
    # minimize/maximize/toggle_wifi/bluetooth_on/app_volume_set/
    # list_system_shortcuts/etc. have no task_engine path yet — must stay
    # directly callable exactly as before, even in JARVIS mode.
    async def _run():
        jarvis = _jarvis(jarvis_mode=True)
        with patch.object(main_module, "computer_settings", return_value="Done: minimize.") as m_cs:
            fr = await jarvis._execute_tool(_fc("computer_settings", action="minimize"))
        m_cs.assert_called_once()
        assert fr.response["result"] == "Done: minimize."
    asyncio.run(_run())
    print("test_computer_settings_non_migrated_action_is_never_redirected_even_in_jarvis_mode: PASS")

def test_computer_settings_volume_set_is_unaffected_outside_jarvis_mode() -> None:
    async def _run():
        jarvis = _jarvis(jarvis_mode=False)
        with patch.object(main_module, "computer_settings", return_value="[VERIFIED_SUCCESS] volume is now 40%.") as m_cs:
            fr = await jarvis._execute_tool(_fc("computer_settings", action="volume_set", value="40"))
        m_cs.assert_called_once()
        assert fr.response["result"] == "[VERIFIED_SUCCESS] volume is now 40%."
    asyncio.run(_run())
    print("test_computer_settings_volume_set_is_unaffected_outside_jarvis_mode: PASS")

def test_computer_settings_non_migrated_action_returns_its_real_result_not_done() -> None:
    # THE regression guard for the real dispatch bug found while adding
    # this boundary: before the fix, this exact scenario (mocked
    # computer_settings returning a distinctive, non-"Done." string)
    # silently came back as "Done." — no exception, no warning, just the
    # wrong result reaching Gemini. Also exercised in SARANA mode (off),
    # confirming the fix isn't somehow tied to the new JARVIS-mode branch.
    async def _run():
        jarvis = _jarvis(jarvis_mode=False)
        with patch.object(main_module, "computer_settings", return_value="UNIQUE_MARKER_XYZ123") as m_cs:
            fr = await jarvis._execute_tool(_fc("computer_settings", action="minimize"))
        m_cs.assert_called_once()
        assert fr.response["result"] == "UNIQUE_MARKER_XYZ123"
    asyncio.run(_run())
    print("test_computer_settings_non_migrated_action_returns_its_real_result_not_done: PASS")


if __name__ == "__main__":
    test_jarvis_task_tool_is_declared()
    test_jarvis_task_outside_jarvis_mode_is_rejected_and_calls_nothing()
    test_jarvis_task_in_jarvis_mode_calls_the_task_engine()
    test_browser_control_go_to_is_redirected_in_jarvis_mode()
    test_browser_control_search_is_redirected_in_jarvis_mode()
    test_browser_control_go_to_is_unaffected_outside_jarvis_mode()
    test_browser_control_click_is_never_redirected_even_in_jarvis_mode()
    test_youtube_video_play_is_redirected_in_jarvis_mode()
    test_youtube_video_play_is_unaffected_outside_jarvis_mode()
    test_youtube_video_summarize_is_never_redirected_even_in_jarvis_mode()
    test_computer_settings_volume_set_is_redirected_in_jarvis_mode()
    test_computer_settings_power_actions_are_redirected_in_jarvis_mode()
    test_computer_settings_system_shortcut_is_redirected_in_jarvis_mode()
    test_computer_settings_non_migrated_action_is_never_redirected_even_in_jarvis_mode()
    test_computer_settings_volume_set_is_unaffected_outside_jarvis_mode()
    test_computer_settings_non_migrated_action_returns_its_real_result_not_done()
    print("\nAll jarvis_task_boundary tests passed.")
