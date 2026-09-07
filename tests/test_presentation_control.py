"""
tests/test_presentation_control.py -- the Universal Information Surface's
own control channel: main.py's presentation_control tool (a real voice/
text command -- "expand that", "keep this on screen", "hide it" --
reaches this through the existing Gemini -> JARVIS authority boundary)
broadcasts a "presentation_control" message through the SAME dashboard
choke point (broadcast_presentation_control() -> _send_to_clients())
that already reaches a real browser client AND (via set_local_sink())
an embedded desktop Presentation Engine view -- one shared surface,
controllable from either input, never a second command parser/state
model.

Run with:
    .venv/Scripts/python.exe -m tests.test_presentation_control
"""
import asyncio
from unittest.mock import MagicMock

from core.headless_surface import HeadlessSurface
from main import JarvisLive


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


def _jarvis_with_fake_dashboard():
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    fake_dashboard = MagicMock()
    fake_dashboard.broadcast_presentation_control = MagicMock(return_value=asyncio.sleep(0))
    jarvis._dashboard = fake_dashboard
    return jarvis, fake_dashboard


def _run_action(action: str):
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        fc = _FakeFunctionCall("presentation_control", {"action": action})
        resp = await jarvis._execute_tool(fc)
        await asyncio.sleep(0)
        return resp, dashboard
    return asyncio.run(_run())


def test_expand_broadcasts_expand_action() -> None:
    resp, dashboard = _run_action("expand")
    dashboard.broadcast_presentation_control.assert_called_once_with("expand")
    assert "[PRESENTATION_EXPAND]" in resp.response["result"]
    print("test_expand_broadcasts_expand_action: PASS")


def test_collapse_broadcasts_collapse_action() -> None:
    resp, dashboard = _run_action("collapse")
    dashboard.broadcast_presentation_control.assert_called_once_with("collapse")
    assert "[PRESENTATION_COLLAPSE]" in resp.response["result"]
    print("test_collapse_broadcasts_collapse_action: PASS")


def test_dismiss_broadcasts_dismiss_action() -> None:
    resp, dashboard = _run_action("dismiss")
    dashboard.broadcast_presentation_control.assert_called_once_with("dismiss")
    assert "[PRESENTATION_DISMISS]" in resp.response["result"]
    print("test_dismiss_broadcasts_dismiss_action: PASS")


def test_keep_visible_broadcasts_keep_visible_action() -> None:
    resp, dashboard = _run_action("keep_visible")
    dashboard.broadcast_presentation_control.assert_called_once_with("keep_visible")
    assert "[PRESENTATION_KEEP_VISIBLE]" in resp.response["result"]
    print("test_keep_visible_broadcasts_keep_visible_action: PASS")


def test_invalid_action_is_rejected_without_broadcasting_anything() -> None:
    resp, dashboard = _run_action("teleport")
    dashboard.broadcast_presentation_control.assert_not_called()
    assert "valid" in resp.response["result"].lower()
    print("test_invalid_action_is_rejected_without_broadcasting_anything: PASS")


def test_no_dashboard_reports_unavailable_honestly_never_claims_success() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        assert jarvis._dashboard is None
        fc = _FakeFunctionCall("presentation_control", {"action": "expand"})
        resp = await jarvis._execute_tool(fc)
        return resp
    resp = asyncio.run(_run())
    assert "[PRESENTATION_UNAVAILABLE]" in resp.response["result"]
    print("test_no_dashboard_reports_unavailable_honestly_never_claims_success: PASS")


def test_action_is_case_insensitive_and_trimmed() -> None:
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        fc = _FakeFunctionCall("presentation_control", {"action": "  EXPAND  "})
        await jarvis._execute_tool(fc)
        await asyncio.sleep(0)
        dashboard.broadcast_presentation_control.assert_called_once_with("expand")
    asyncio.run(_run())
    print("test_action_is_case_insensitive_and_trimmed: PASS")


def test_not_gated_as_desktop_only_works_identically_on_web() -> None:
    """The Universal Information Surface is genuinely universal — a web
    session must be able to control its own presentation exactly like
    desktop, never a surface-restricted tool."""
    from main import DESKTOP_ONLY_TOOLS
    assert "presentation_control" not in DESKTOP_ONLY_TOOLS
    print("test_not_gated_as_desktop_only_works_identically_on_web: PASS")


def test_registered_tool_declaration_has_the_four_documented_actions() -> None:
    from main import TOOL_DECLARATIONS
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "presentation_control")
    enum = decl["parameters"]["properties"]["action"]["enum"]
    assert set(enum) == {"expand", "collapse", "dismiss", "keep_visible"}
    assert decl["parameters"]["required"] == ["action"]
    print("test_registered_tool_declaration_has_the_four_documented_actions: PASS")


if __name__ == "__main__":
    test_expand_broadcasts_expand_action()
    test_collapse_broadcasts_collapse_action()
    test_dismiss_broadcasts_dismiss_action()
    test_keep_visible_broadcasts_keep_visible_action()
    test_invalid_action_is_rejected_without_broadcasting_anything()
    test_no_dashboard_reports_unavailable_honestly_never_claims_success()
    test_action_is_case_insensitive_and_trimmed()
    test_not_gated_as_desktop_only_works_identically_on_web()
    test_registered_tool_declaration_has_the_four_documented_actions()
    print("\nAll presentation-control tests passed.")
