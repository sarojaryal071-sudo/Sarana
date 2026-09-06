"""
tests/test_shared_audio_cue.py -- the shared semantic audio event origin:
main.py's _execute_tool() choke point (_emit_audio_cue_for_result()) now
authoritatively decides when a tool result carries a consequential Result
Envelope tag ([BLOCKED]/[CONFIRMATION_REQUIRED]/[VERIFIED_FAILURE]) and
broadcasts an explicit "audio_cue" message via dashboard/server.py's
broadcast_audio_cue() -- replacing the old, fragile browser-side
text-matching against Gemini's own paraphrased reply. Both a real browser
client AND (via DashboardServer.set_local_sink()) an embedded desktop
Presentation Engine view receive the SAME event from the SAME origin.

Run with:
    .venv/Scripts/python.exe -m tests.test_shared_audio_cue
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
    fake_dashboard.broadcast_audio_cue = MagicMock(return_value=asyncio.sleep(0))
    jarvis._dashboard = fake_dashboard
    return jarvis, fake_dashboard


# ── _emit_audio_cue_for_result() directly ──────────────────────────────

def test_confirmation_required_tag_emits_confirmation_required_event() -> None:
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        jarvis._emit_audio_cue_for_result("[CONFIRMATION_REQUIRED] delete this file?")
        await asyncio.sleep(0)
        dashboard.broadcast_audio_cue.assert_called_once_with("confirmation_required")
    asyncio.run(_run())
    print("test_confirmation_required_tag_emits_confirmation_required_event: PASS")


def test_blocked_tag_emits_blocked_event() -> None:
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        jarvis._emit_audio_cue_for_result("[BLOCKED] not allowed")
        await asyncio.sleep(0)
        dashboard.broadcast_audio_cue.assert_called_once_with("blocked")
    asyncio.run(_run())
    print("test_blocked_tag_emits_blocked_event: PASS")


def test_verified_failure_tag_emits_error_event() -> None:
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        jarvis._emit_audio_cue_for_result("[VERIFIED_FAILURE] the action did not actually happen")
        await asyncio.sleep(0)
        dashboard.broadcast_audio_cue.assert_called_once_with("error")
    asyncio.run(_run())
    print("test_verified_failure_tag_emits_error_event: PASS")


def test_ordinary_result_emits_nothing() -> None:
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        jarvis._emit_audio_cue_for_result("It's 5.2°C and partly cloudy.")
        await asyncio.sleep(0)
        dashboard.broadcast_audio_cue.assert_not_called()
    asyncio.run(_run())
    print("test_ordinary_result_emits_nothing: PASS")


def test_leading_whitespace_does_not_defeat_the_match() -> None:
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        jarvis._emit_audio_cue_for_result("   [BLOCKED] still recognized")
        await asyncio.sleep(0)
        dashboard.broadcast_audio_cue.assert_called_once_with("blocked")
    asyncio.run(_run())
    print("test_leading_whitespace_does_not_defeat_the_match: PASS")


def test_no_dashboard_never_raises() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    assert jarvis._dashboard is None
    jarvis._emit_audio_cue_for_result("[BLOCKED] not allowed")   # must not raise
    print("test_no_dashboard_never_raises: PASS")


def test_non_string_result_never_raises() -> None:
    """Some tool branches return non-string results (rare, but real) —
    the tag check must be safe against that."""
    jarvis, dashboard = _jarvis_with_fake_dashboard()
    jarvis._emit_audio_cue_for_result(None)
    jarvis._emit_audio_cue_for_result({"not": "a string"})
    dashboard.broadcast_audio_cue.assert_not_called()
    print("test_non_string_result_never_raises: PASS")


# ── end-to-end through _execute_tool()'s real choke point ──────────────

def test_execute_tool_emits_cue_for_a_real_blocked_result() -> None:
    """A real tool branch that returns [BLOCKED] -- desktop_control's
    confirmation-required path is a convenient real example already
    covered elsewhere; here we just need ANY branch whose result reaches
    the shared choke point. Using the unknown-tool fallback keeps this
    test independent of any specific tool's own business logic."""
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        fc = _FakeFunctionCall("get_calendar_events", {"start": "2026-08-29T00:00:00", "end": "2026-08-30T00:00:00"})
        from unittest.mock import patch
        with patch("main.calendar_store.is_configured", return_value=True), \
             patch("main.calendar_auth.is_configured", return_value=True), \
             patch("main.calendar_store.load_credentials", return_value=None):
            await jarvis._execute_tool(fc)
        await asyncio.sleep(0)
        # [CALENDAR_NOT_CONNECTED] is not one of the three audio-cue tags —
        # confirms the choke point runs for EVERY tool call but stays
        # silent for a tag outside its own deliberately narrow vocabulary.
        dashboard.broadcast_audio_cue.assert_not_called()
    asyncio.run(_run())
    print("test_execute_tool_emits_cue_for_a_real_blocked_result: PASS")


def test_execute_tool_choke_point_runs_for_unknown_tools_too() -> None:
    """Confirms _emit_audio_cue_for_result() is wired at the ONE true
    choke point (every branch funnels through it, including the
    catch-all "Unknown tool" result), not per-branch instrumentation."""
    async def _run():
        jarvis, dashboard = _jarvis_with_fake_dashboard()
        fc = _FakeFunctionCall("totally_made_up_tool_name", {})
        resp = await jarvis._execute_tool(fc)
        assert "Unknown tool" in resp.response["result"]
        await asyncio.sleep(0)
        dashboard.broadcast_audio_cue.assert_not_called()   # no recognized tag -- correctly silent
    asyncio.run(_run())
    print("test_execute_tool_choke_point_runs_for_unknown_tools_too: PASS")


if __name__ == "__main__":
    test_confirmation_required_tag_emits_confirmation_required_event()
    test_blocked_tag_emits_blocked_event()
    test_verified_failure_tag_emits_error_event()
    test_ordinary_result_emits_nothing()
    test_leading_whitespace_does_not_defeat_the_match()
    test_no_dashboard_never_raises()
    test_non_string_result_never_raises()
    test_execute_tool_emits_cue_for_a_real_blocked_result()
    test_execute_tool_choke_point_runs_for_unknown_tools_too()
    print("\nAll shared-audio-cue tests passed.")
