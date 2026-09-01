"""
tests/test_set_expression.py — the set_expression tool (SARANA Face UI):
a real user asked SARANA "show me a sad expression" and was told it
couldn't be done — the mood expressions existed in the face UI already,
fully built (see frontend/src/lib/faceExpressions.js's FACE_EXPRESSIONS /
ui.py's _SARANA_EXPRESSIONS), but nothing gave the model an actual tool
to reach them; only mechanical status (listening/thinking/speaking/muted)
could ever drive the face. This tool is that missing signal.

Same convention as tests/test_jarvis_mode.py (which this file's helpers
are directly modeled on): a real JarvisLive instance constructed with
HeadlessSurface, dispatched through the real _execute_tool() path, with
a recording fake dashboard standing in for the actual WebSocket
broadcast.

Run with:
    .venv/Scripts/python.exe -m tests.test_set_expression
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive, TOOL_DECLARATIONS, _VALID_FACE_EXPRESSIONS


class _RecordingDashboard:
    """Minimal fake — only what set_expression's dispatch branch touches."""

    def __init__(self):
        self.expression_calls = []  # list of (expression, duration_seconds)

    async def broadcast_expression_override(self, expression: str, duration_seconds: float) -> None:
        self.expression_calls.append((expression, duration_seconds))


def _jarvis(auto_start=True) -> JarvisLive:
    j = JarvisLive(HeadlessSurface(), auto_start=auto_start)
    j._dashboard = _RecordingDashboard()
    return j


def _fc(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(id="fc-1", name=name, args=args)


# ── tool declaration ─────────────────────────────────────────────────────

def test_set_expression_tool_is_declared_with_the_full_shared_vocabulary_enum() -> None:
    names = [t["name"] for t in TOOL_DECLARATIONS]
    assert "set_expression" in names
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "set_expression")
    assert decl["parameters"]["required"] == ["expression"]
    enum = set(decl["parameters"]["properties"]["expression"]["enum"])
    assert enum == _VALID_FACE_EXPRESSIONS, "declared enum must match the validation set exactly — no drift"
    assert "duration_seconds" in decl["parameters"]["properties"]
    print("test_set_expression_tool_is_declared_with_the_full_shared_vocabulary_enum: PASS")


def test_valid_face_expressions_matches_the_frontend_and_desktop_vocabulary_size() -> None:
    # Same fifteen-word set as frontend/src/lib/faceExpressions.js's
    # FACE_EXPRESSIONS and ui.py's _SARANA_EXPRESSIONS keys — not
    # asserted cross-language here (Python can't import JS), but the
    # count is the cheap invariant that catches an accidental drop.
    assert len(_VALID_FACE_EXPRESSIONS) == 15
    for word in ("sad", "happy", "curious", "excited", "surprised", "concerned", "neutral"):
        assert word in _VALID_FACE_EXPRESSIONS
    print("test_valid_face_expressions_matches_the_frontend_and_desktop_vocabulary_size: PASS")


# ── dispatch: valid expression ───────────────────────────────────────────

def test_set_expression_sad_calls_ui_and_broadcasts_and_returns_success() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(jarvis.ui, "set_expression") as m:
            fr = await jarvis._execute_tool(_fc("set_expression", expression="sad"))
            await asyncio.sleep(0.05)  # let the fire-and-forget broadcast task run
            m.assert_called_once_with("sad", 6.0)
        assert jarvis._dashboard.expression_calls == [("sad", 6.0)]
        assert "sad" in fr.response["result"]
        assert "Done" in fr.response["result"]
    asyncio.run(_run())
    print("test_set_expression_sad_calls_ui_and_broadcasts_and_returns_success: PASS")


def test_set_expression_is_case_insensitive() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(jarvis.ui, "set_expression") as m:
            await jarvis._execute_tool(_fc("set_expression", expression="  Excited  "))
            m.assert_called_once_with("excited", 6.0)
    asyncio.run(_run())
    print("test_set_expression_is_case_insensitive: PASS")


def test_set_expression_custom_duration_is_respected_and_clamped() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(jarvis.ui, "set_expression") as m:
            await jarvis._execute_tool(_fc("set_expression", expression="happy", duration_seconds=12))
            m.assert_called_once_with("happy", 12.0)
            m.reset_mock()
            # over the cap
            await jarvis._execute_tool(_fc("set_expression", expression="happy", duration_seconds=999))
            m.assert_called_once_with("happy", 20.0)
            m.reset_mock()
            # under the floor
            await jarvis._execute_tool(_fc("set_expression", expression="happy", duration_seconds=0))
            m.assert_called_once_with("happy", 1.0)
    asyncio.run(_run())
    print("test_set_expression_custom_duration_is_respected_and_clamped: PASS")


def test_set_expression_garbage_duration_falls_back_to_the_default() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(jarvis.ui, "set_expression") as m:
            await jarvis._execute_tool(_fc("set_expression", expression="calm", duration_seconds="not-a-number"))
            m.assert_called_once_with("calm", 6.0)
    asyncio.run(_run())
    print("test_set_expression_garbage_duration_falls_back_to_the_default: PASS")


# ── dispatch: invalid expression ─────────────────────────────────────────

def test_set_expression_invalid_value_never_calls_ui_or_broadcasts() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        with patch.object(jarvis.ui, "set_expression") as m:
            fr = await jarvis._execute_tool(_fc("set_expression", expression="ecstatic"))
            m.assert_not_called()
        assert jarvis._dashboard.expression_calls == []
        assert "[INVALID_EXPRESSION]" in fr.response["result"]
        assert "ecstatic" in fr.response["result"]
    asyncio.run(_run())
    print("test_set_expression_invalid_value_never_calls_ui_or_broadcasts: PASS")


def test_set_expression_missing_value_is_also_rejected_not_a_crash() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        fr = await jarvis._execute_tool(_fc("set_expression"))
        assert "[INVALID_EXPRESSION]" in fr.response["result"]
    asyncio.run(_run())
    print("test_set_expression_missing_value_is_also_rejected_not_a_crash: PASS")


# ── never gated behind a confirmation — purely presentational (see
#    result_envelope.py's is_consequential(), which this never calls) ────

def test_set_expression_never_requires_confirmation_even_for_concerned_or_sad() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        for expr in ("sad", "concerned"):
            with patch.object(jarvis.ui, "set_expression") as m:
                fr = await jarvis._execute_tool(_fc("set_expression", expression=expr))
                m.assert_called_once()
            assert "[CONFIRMATION_REQUIRED]" not in fr.response["result"]
    asyncio.run(_run())
    print("test_set_expression_never_requires_confirmation_even_for_concerned_or_sad: PASS")


# ── universal — works identically whether or not JARVIS mode / auto_start ─

def test_set_expression_works_on_both_desktop_and_web_sessions() -> None:
    async def _run():
        for auto_start in (True, False):
            jarvis = _jarvis(auto_start=auto_start)
            with patch.object(jarvis.ui, "set_expression") as m:
                fr = await jarvis._execute_tool(_fc("set_expression", expression="curious"))
                m.assert_called_once_with("curious", 6.0)
            assert "curious" in fr.response["result"]
    asyncio.run(_run())
    print("test_set_expression_works_on_both_desktop_and_web_sessions: PASS")


# ── HeadlessSurface: exists, never crashes ───────────────────────────────

def test_headless_surface_set_expression_does_not_raise() -> None:
    surface = HeadlessSurface()
    surface.set_expression("happy", 6.0)  # must not raise
    print("test_headless_surface_set_expression_does_not_raise: PASS")


if __name__ == "__main__":
    test_set_expression_tool_is_declared_with_the_full_shared_vocabulary_enum()
    test_valid_face_expressions_matches_the_frontend_and_desktop_vocabulary_size()
    test_set_expression_sad_calls_ui_and_broadcasts_and_returns_success()
    test_set_expression_is_case_insensitive()
    test_set_expression_custom_duration_is_respected_and_clamped()
    test_set_expression_garbage_duration_falls_back_to_the_default()
    test_set_expression_invalid_value_never_calls_ui_or_broadcasts()
    test_set_expression_missing_value_is_also_rejected_not_a_crash()
    test_set_expression_never_requires_confirmation_even_for_concerned_or_sad()
    test_set_expression_works_on_both_desktop_and_web_sessions()
    test_headless_surface_set_expression_does_not_raise()
    print("\nAll set_expression tests passed.")
