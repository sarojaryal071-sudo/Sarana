"""
tests/test_action_governor.py — the bounded autonomous-execution governor
(main.py's self._jarvis_action_count / _JARVIS_MAX_ACTIONS_PER_TURN).

Exists because a goal-directed observe -> act -> verify -> reason loop has
no other natural stopping point: Gemini decides for itself when to keep
calling computer_control/browser_control, and a genuinely stuck strategy
could otherwise call tools indefinitely. Covers:

  - the hard cap itself (exactly N calls allowed, the (N+1)th refused)
  - the cap applies regardless of JARVIS mode (both tools can already act
    on the real computer/browser either way)
  - computer_control and browser_control share ONE budget, not two
  - the counter never grows past the cap once tripped (pinned, not
    incremented further on refused calls)
  - every reset trigger: a fresh typed command (_on_text_command), a
    barge-in interrupt, and a fresh Gemini connection (reconnect)
  - tools that never touch the real computer/browser (save_memory) never
    consume the budget

Run with:
    .venv/Scripts/python.exe -m tests.test_action_governor
"""
import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive, _JARVIS_MAX_ACTIONS_PER_TURN
import main as main_module


class _FakeSession:
    async def send_client_content(self, *, turns, turn_complete=True):
        pass


def _jarvis(auto_start=True) -> JarvisLive:
    return JarvisLive(HeadlessSurface(), auto_start=auto_start)


def _fc(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(id="fc-1", name=name, args=args)


def test_exactly_the_limit_succeeds_then_the_next_call_is_refused() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(main_module, "computer_control", lambda **kw: "Waited 0.01s"):
            for i in range(_JARVIS_MAX_ACTIONS_PER_TURN):
                fr = await jarvis._execute_tool(_fc("computer_control", action="wait", seconds=0.01))
                assert "[JARVIS_ACTION_LIMIT_REACHED]" not in fr.response["result"], (
                    f"call {i + 1}/{_JARVIS_MAX_ACTIONS_PER_TURN} was refused too early"
                )
            # The (N+1)th call must be refused.
            fr = await jarvis._execute_tool(_fc("computer_control", action="wait", seconds=0.01))
            assert "[JARVIS_ACTION_LIMIT_REACHED]" in fr.response["result"]
    asyncio.run(_run())
    print("test_exactly_the_limit_succeeds_then_the_next_call_is_refused: PASS")


def test_governor_applies_regardless_of_jarvis_mode() -> None:
    # computer_control's ORIGINAL raw actions (click/type/wait/etc.) work
    # with JARVIS mode off, exactly as before this feature — the governor
    # must still bound them; it is not a JARVIS-mode-only safety net.
    async def _run():
        jarvis = _jarvis(auto_start=True)
        assert jarvis._jarvis_mode is False
        with patch.object(main_module, "computer_control", lambda **kw: "Waited 0.01s"):
            for _ in range(_JARVIS_MAX_ACTIONS_PER_TURN):
                await jarvis._execute_tool(_fc("computer_control", action="wait", seconds=0.01))
            fr = await jarvis._execute_tool(_fc("computer_control", action="wait", seconds=0.01))
        assert "[JARVIS_ACTION_LIMIT_REACHED]" in fr.response["result"]
    asyncio.run(_run())
    print("test_governor_applies_regardless_of_jarvis_mode: PASS")


def test_computer_control_and_browser_control_share_one_budget() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(main_module, "computer_control", lambda **kw: "Waited 0.01s"), \
             patch.object(main_module, "browser_control", lambda **kw: "ok"):
            half = _JARVIS_MAX_ACTIONS_PER_TURN // 2
            for _ in range(half):
                await jarvis._execute_tool(_fc("computer_control", action="wait", seconds=0.01))
            for _ in range(_JARVIS_MAX_ACTIONS_PER_TURN - half):
                await jarvis._execute_tool(_fc("browser_control", action="list_browsers"))
            # Budget now exhausted across BOTH tools combined.
            fr = await jarvis._execute_tool(_fc("computer_control", action="wait", seconds=0.01))
            assert "[JARVIS_ACTION_LIMIT_REACHED]" in fr.response["result"]
            fr2 = await jarvis._execute_tool(_fc("browser_control", action="list_browsers"))
            assert "[JARVIS_ACTION_LIMIT_REACHED]" in fr2.response["result"]
    asyncio.run(_run())
    print("test_computer_control_and_browser_control_share_one_budget: PASS")


def test_counter_stays_pinned_once_tripped_never_grows_unbounded() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        jarvis._jarvis_action_count = _JARVIS_MAX_ACTIONS_PER_TURN
        with patch.object(main_module, "computer_control", lambda **kw: "Waited 0.01s"):
            for _ in range(50):
                await jarvis._execute_tool(_fc("computer_control", action="wait", seconds=0.01))
        assert jarvis._jarvis_action_count == _JARVIS_MAX_ACTIONS_PER_TURN
    asyncio.run(_run())
    print("test_counter_stays_pinned_once_tripped_never_grows_unbounded: PASS")


def test_tools_that_never_touch_the_computer_dont_consume_the_budget() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        for _ in range(_JARVIS_MAX_ACTIONS_PER_TURN + 10):
            await jarvis._execute_tool(_fc("save_memory", category="notes", key="x", value="y"))
        assert jarvis._jarvis_action_count == 0
    asyncio.run(_run())
    print("test_tools_that_never_touch_the_computer_dont_consume_the_budget: PASS")


# ── reset triggers ──────────────────────────────────────────────────────

def test_on_text_command_resets_the_governor() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        jarvis._loop = asyncio.get_event_loop()
        jarvis.session = _FakeSession()
        jarvis._jarvis_action_count = _JARVIS_MAX_ACTIONS_PER_TURN
        jarvis._on_text_command("open Calculator")
        assert jarvis._jarvis_action_count == 0
    asyncio.run(_run())
    print("test_on_text_command_resets_the_governor: PASS")


def test_on_text_command_without_a_live_session_does_not_crash_or_reset() -> None:
    # No self._loop/self.session set (mirrors a not-yet-connected session)
    # — must return harmlessly, never raise, and never falsely reset a
    # budget for a command that was never actually sent anywhere.
    jarvis = _jarvis(auto_start=True)
    jarvis._jarvis_action_count = 5
    jarvis._on_text_command("hello")
    assert jarvis._jarvis_action_count == 5
    print("test_on_text_command_without_a_live_session_does_not_crash_or_reset: PASS")


def test_reconnect_reset_block_resets_action_governor() -> None:
    src = inspect.getsource(JarvisLive.run)
    assert "self._jarvis_action_count   = 0" in src
    print("test_reconnect_reset_block_resets_action_governor: PASS")


def test_barge_in_and_new_utterance_reset_the_governor_source_check() -> None:
    # A full live barge-in / streamed transcription isn't easily driven in
    # a unit test (needs a real Gemini receive loop) — both reset
    # statements are fixed, reviewable lines inside _receive_audio, same
    # convention as the reconnect-reset check above. Expect exactly two:
    # one in the sc.interrupted (barge-in) branch, one in the
    # sc.input_transcription (first chunk of a new utterance) branch.
    src = inspect.getsource(JarvisLive._receive_audio)
    assert src.count("self._jarvis_action_count = 0") == 2
    print("test_barge_in_and_new_utterance_reset_the_governor_source_check: PASS")


if __name__ == "__main__":
    test_exactly_the_limit_succeeds_then_the_next_call_is_refused()
    test_governor_applies_regardless_of_jarvis_mode()
    test_computer_control_and_browser_control_share_one_budget()
    test_counter_stays_pinned_once_tripped_never_grows_unbounded()
    test_tools_that_never_touch_the_computer_dont_consume_the_budget()
    test_on_text_command_resets_the_governor()
    test_on_text_command_without_a_live_session_does_not_crash_or_reset()
    test_reconnect_reset_block_resets_action_governor()
    test_barge_in_and_new_utterance_reset_the_governor_source_check()
    print("\nAll action-governor tests passed.")
