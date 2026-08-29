"""
tests/test_task_cancellation.py — regression tests distinguishing ordinary
speech interruption (barge-in) from an EXPLICIT, user-requested task
cancellation.

These are deliberately different concepts (see the task's own requirement):
SARANA talking over itself while the user starts a new turn does NOT mean
"cancel whatever backend operation is running" — only an explicit
cancel_active_task tool call (Gemini's own judgment call, triggered by
words like "stop that"/"cancel it"/"never mind") does, and even then never
by falsely claiming a cancellation that can't be guaranteed once a
mutating tool has already reached an external service (see main.py's
_READ_ONLY_TOOLS / cancel_active_task branch in _execute_tool()).

See tests/test_barge_in.py's own test_barge_in_never_touches_active_tool_task
for the complementary "ordinary interruption never touches the task" proof
at the _receive_audio() level; this file focuses on cancel_active_task's
own decision logic.

Run with:
    python -m pytest tests/test_task_cancellation.py -q
"""
import asyncio

from core.headless_surface import HeadlessSurface
from main import JarvisLive


class _FakeFunctionCall:
    def __init__(self, name, args=None, call_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = call_id


def _jarvis() -> JarvisLive:
    return JarvisLive(HeadlessSurface(), auto_start=False)


def test_cancel_read_only_tool_actually_cancels_it() -> None:
    """A read-only tool (no external side effect to misreport) can be
    honestly cancelled outright, even mid-flight."""
    async def _run():
        jarvis = _jarvis()
        task = asyncio.ensure_future(asyncio.sleep(10))
        jarvis._active_tool_task = task
        jarvis._active_tool_name = "get_weather"

        fc = _FakeFunctionCall("cancel_active_task", call_id="cx1")
        resp = await jarvis._execute_tool(fc)

        await asyncio.sleep(0)   # let the requested cancellation propagate
        assert "[TASK_CANCELLED]" in resp.response["result"]
        assert task.cancelled(), "a read-only tool's task must actually be cancelled"

    asyncio.run(_run())
    print("test_cancel_read_only_tool_actually_cancels_it: PASS")


def test_cancel_mutating_tool_does_not_claim_cancellation() -> None:
    """A mutating tool (Calendar create/update/delete, save_memory,
    reminder, etc.) that has already started running must be left to
    finish — its network call may already have reached Google Calendar or
    another external system, and cannot be safely/forcibly stopped
    mid-flight. cancel_active_task must never claim it was cancelled here."""
    async def _run():
        jarvis = _jarvis()
        task = asyncio.ensure_future(asyncio.sleep(10))
        jarvis._active_tool_task = task
        jarvis._active_tool_name = "create_calendar_event"

        try:
            fc = _FakeFunctionCall("cancel_active_task", call_id="cx2")
            resp = await jarvis._execute_tool(fc)

            assert "[TASK_MAY_HAVE_COMPLETED]" in resp.response["result"]
            assert "[TASK_CANCELLED]" not in resp.response["result"]
            await asyncio.sleep(0)
            assert not task.cancelled(), "a mutating tool already in flight must be left running, not force-cancelled"
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_run())
    print("test_cancel_mutating_tool_does_not_claim_cancellation: PASS")


def test_cancel_when_nothing_is_running_reports_honestly() -> None:
    async def _run():
        jarvis = _jarvis()
        jarvis._active_tool_task = None
        jarvis._active_tool_name = None

        fc = _FakeFunctionCall("cancel_active_task", call_id="cx3")
        resp = await jarvis._execute_tool(fc)

        assert "[TASK_ALREADY_DONE]" in resp.response["result"]

    asyncio.run(_run())
    print("test_cancel_when_nothing_is_running_reports_honestly: PASS")


def test_cancel_after_task_already_completed_is_not_falsely_reported_cancelled() -> None:
    """The core requirement: if a request already reached (and finished
    at) an external service by the time the cancellation arrives, SARANA
    must not claim it cancelled it."""
    async def _run():
        jarvis = _jarvis()

        async def _quick():
            return "done"

        task = asyncio.ensure_future(_quick())
        await task   # already completed by the time cancellation is requested
        jarvis._active_tool_task = task
        jarvis._active_tool_name = "create_calendar_event"

        fc = _FakeFunctionCall("cancel_active_task", call_id="cx4")
        resp = await jarvis._execute_tool(fc)

        assert "[TASK_ALREADY_DONE]" in resp.response["result"]
        assert "[TASK_CANCELLED]" not in resp.response["result"], (
            "an already-completed external operation must never be reported as cancelled"
        )

    asyncio.run(_run())
    print("test_cancel_after_task_already_completed_is_not_falsely_reported_cancelled: PASS")


if __name__ == "__main__":
    test_cancel_read_only_tool_actually_cancels_it()
    test_cancel_mutating_tool_does_not_claim_cancellation()
    test_cancel_when_nothing_is_running_reports_honestly()
    test_cancel_after_task_already_completed_is_not_falsely_reported_cancelled()
    print("\nAll task cancellation tests passed.")
