"""
tests/test_gesture_mode_tool.py — the gesture_mode tool's DISPATCH
wiring in main.py (the actual hand-tracking/mouse-control logic is
covered separately in tests/test_gesture_control.py, which this file's
tests mock out entirely — same separation of concerns as
test_set_expression.py / SaranaFaceCanvas's own render tests vs. this
project's other "tool wiring" test files).

Same convention as tests/test_jarvis_mode.py / test_set_expression.py: a
real JarvisLive instance constructed with HeadlessSurface, dispatched
through the real _execute_tool() path.

Run with:
    .venv/Scripts/python.exe -m tests.test_gesture_mode_tool
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive, TOOL_DECLARATIONS, DESKTOP_ONLY_TOOLS
import main as main_module


def _jarvis(auto_start=True) -> JarvisLive:
    return JarvisLive(HeadlessSurface(), auto_start=auto_start)


def _fc(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(id="fc-1", name=name, args=args)


# ── tool declaration / gating ─────────────────────────────────────────

def test_gesture_mode_tool_is_declared_and_desktop_only() -> None:
    names = [t["name"] for t in TOOL_DECLARATIONS]
    assert "gesture_mode" in names
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "gesture_mode")
    assert decl["parameters"]["required"] == ["action"]
    assert "gesture_mode" in DESKTOP_ONLY_TOOLS
    # The description must be explicit that this NEVER self-activates —
    # the same requirement that motivated this whole tool existing.
    assert "explicit" in decl["description"].lower()
    assert "never" in decl["description"].lower()
    print("test_gesture_mode_tool_is_declared_and_desktop_only: PASS")


def test_gesture_mode_is_blocked_on_a_web_session() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)  # web session
        with patch.object(main_module.gesture_control, "start") as m:
            fr = await jarvis._execute_tool(_fc("gesture_mode", action="on"))
            m.assert_not_called()
        assert "[CAPABILITY_UNAVAILABLE]" in fr.response["result"]
    asyncio.run(_run())
    print("test_gesture_mode_is_blocked_on_a_web_session: PASS")


# ── dispatch: on/off/invalid ────────────────────────────────────────────

def test_gesture_mode_on_calls_gesture_control_start_and_reports_its_message() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(main_module.gesture_control, "start", return_value="Gesture control is now active. No preview window is shown.") as m:
            fr = await jarvis._execute_tool(_fc("gesture_mode", action="on"))
            m.assert_called_once_with()
        result = fr.response["result"]
        assert "[GESTURE_MODE]" in result
        assert "now active" in result
        assert "no preview" in result.lower()
    asyncio.run(_run())
    print("test_gesture_mode_on_calls_gesture_control_start_and_reports_its_message: PASS")


def test_gesture_mode_off_calls_gesture_control_stop_and_reports_its_message() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(main_module.gesture_control, "stop", return_value="Gesture control is now off.") as m:
            fr = await jarvis._execute_tool(_fc("gesture_mode", action="off"))
            m.assert_called_once_with()
        assert "[GESTURE_MODE]" in fr.response["result"]
        assert "now off" in fr.response["result"]
    asyncio.run(_run())
    print("test_gesture_mode_off_calls_gesture_control_stop_and_reports_its_message: PASS")


def test_gesture_mode_surfaces_a_real_camera_failure_honestly() -> None:
    # The dispatch must relay gesture_control.start()'s own honest
    # failure string verbatim, never silently claim success.
    async def _run():
        jarvis = _jarvis(auto_start=True)
        failure = "Could not open the webcam — it may be in use by another application. Gesture control was NOT activated."
        with patch.object(main_module.gesture_control, "start", return_value=failure):
            fr = await jarvis._execute_tool(_fc("gesture_mode", action="on"))
        assert failure in fr.response["result"]
        assert "NOT activated" in fr.response["result"]
    asyncio.run(_run())
    print("test_gesture_mode_surfaces_a_real_camera_failure_honestly: PASS")


def test_gesture_mode_invalid_action_never_calls_start_or_stop() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(main_module.gesture_control, "start") as m_start, \
             patch.object(main_module.gesture_control, "stop") as m_stop:
            fr = await jarvis._execute_tool(_fc("gesture_mode", action="toggle"))
            m_start.assert_not_called()
            m_stop.assert_not_called()
        assert "on" in fr.response["result"] and "off" in fr.response["result"]
    asyncio.run(_run())
    print("test_gesture_mode_invalid_action_never_calls_start_or_stop: PASS")


def test_gesture_mode_missing_action_is_also_rejected() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(main_module.gesture_control, "start") as m_start:
            fr = await jarvis._execute_tool(_fc("gesture_mode"))
            m_start.assert_not_called()
        assert "action" in fr.response["result"].lower()
    asyncio.run(_run())
    print("test_gesture_mode_missing_action_is_also_rejected: PASS")


# ── never gated behind a confirmation — same reasoning as jarvis_mode's ─
#    own simple on/off toggle (result_envelope's is_consequential() is
#    never called for this tool either) ──────────────────────────────────

def test_gesture_mode_never_requires_confirmation() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(main_module.gesture_control, "start", return_value="Gesture control is now active. No preview window is shown."):
            fr = await jarvis._execute_tool(_fc("gesture_mode", action="on"))
        assert "[CONFIRMATION_REQUIRED]" not in fr.response["result"]
    asyncio.run(_run())
    print("test_gesture_mode_never_requires_confirmation: PASS")


if __name__ == "__main__":
    test_gesture_mode_tool_is_declared_and_desktop_only()
    test_gesture_mode_is_blocked_on_a_web_session()
    test_gesture_mode_on_calls_gesture_control_start_and_reports_its_message()
    test_gesture_mode_off_calls_gesture_control_stop_and_reports_its_message()
    test_gesture_mode_surfaces_a_real_camera_failure_honestly()
    test_gesture_mode_invalid_action_never_calls_start_or_stop()
    test_gesture_mode_missing_action_is_also_rejected()
    test_gesture_mode_never_requires_confirmation()
    print("\nAll gesture_mode tool tests passed.")
