"""
tests/test_jarvis_mode.py — JARVIS Mode: the cross-platform, session-scoped
alternate persona/capability toggle (see main.py's self._jarvis_mode /
the jarvis_mode tool).

Covers: the jarvis_mode tool itself (universal, not desktop/web-only,
explicit on/off, dashboard broadcast, reconnect-reset), the static
[JARVIS_MODE] system_instruction block (_build_config()), and
computer_control's NEW JARVIS-only actions (observe/verify/ui_find/
ui_click/ui_type/get_active_window_title) — gating, the observe/verify
same-session _pending_vision injection (reusing screen_process's own
mechanism), the shared cooldown/busy guard (the "no unbounded observe
loop" safety net), and that computer_control's EXISTING raw actions stay
available regardless of JARVIS mode.

Run with:
    .venv/Scripts/python.exe -m tests.test_jarvis_mode
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive, DESKTOP_ONLY_TOOLS, WEB_ONLY_TOOLS, TOOL_DECLARATIONS
import main as main_module


class _RecordingDashboard:
    """Minimal fake — only what jarvis_mode's dispatch branch touches."""

    def __init__(self):
        self.jarvis_mode_calls = []   # list of bool

    async def broadcast_jarvis_mode(self, active: bool) -> None:
        self.jarvis_mode_calls.append(active)


def _jarvis(auto_start=True) -> JarvisLive:
    j = JarvisLive(HeadlessSurface(), auto_start=auto_start)
    j._dashboard = _RecordingDashboard()
    return j


def _fc(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(id="fc-1", name=name, args=args)


# ── tool declaration / surface gating ──────────────────────────────────────

def test_jarvis_mode_tool_is_declared() -> None:
    names = [t["name"] for t in TOOL_DECLARATIONS]
    assert "jarvis_mode" in names
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "jarvis_mode")
    assert decl["parameters"]["required"] == ["action"]
    assert "explicit" in decl["description"].lower()
    print("test_jarvis_mode_tool_is_declared: PASS")


def test_jarvis_mode_is_universal_not_desktop_only_or_web_only() -> None:
    # The MODE toggle itself works on both surfaces — only the DESKTOP-only
    # computer_control actions it unlocks stay gated by DESKTOP_ONLY_TOOLS.
    assert "jarvis_mode" not in DESKTOP_ONLY_TOOLS
    assert "jarvis_mode" not in WEB_ONLY_TOOLS
    print("test_jarvis_mode_is_universal_not_desktop_only_or_web_only: PASS")


def test_computer_control_is_still_desktop_only() -> None:
    # computer_control's new JARVIS actions ride on this EXISTING gate —
    # a web session never even reaches _execute_tool's computer_control
    # branch, regardless of JARVIS mode.
    assert "computer_control" in DESKTOP_ONLY_TOOLS
    print("test_computer_control_is_still_desktop_only: PASS")


# ── jarvis_mode tool: on/off ────────────────────────────────────────────────

def test_jarvis_mode_on_sets_flag_broadcasts_and_returns_directive() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        assert jarvis._jarvis_mode is False
        fr = await jarvis._execute_tool(_fc("jarvis_mode", action="on"))
        await asyncio.sleep(0.05)  # let the fire-and-forget broadcast task run
        assert jarvis._jarvis_mode is True
        assert "[JARVIS_MODE_ON]" in fr.response["result"]
        assert jarvis._dashboard.jarvis_mode_calls == [True]
    asyncio.run(_run())
    print("test_jarvis_mode_on_sets_flag_broadcasts_and_returns_directive: PASS")


def test_jarvis_mode_off_clears_flag_broadcasts_and_returns_directive() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        jarvis._jarvis_mode = True
        fr = await jarvis._execute_tool(_fc("jarvis_mode", action="off"))
        await asyncio.sleep(0.05)
        assert jarvis._jarvis_mode is False
        assert "[JARVIS_MODE_OFF]" in fr.response["result"]
        assert jarvis._dashboard.jarvis_mode_calls == [False]
    asyncio.run(_run())
    print("test_jarvis_mode_off_clears_flag_broadcasts_and_returns_directive: PASS")


def test_jarvis_mode_invalid_action_leaves_state_unchanged() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        fr = await jarvis._execute_tool(_fc("jarvis_mode", action="toggle"))
        assert jarvis._jarvis_mode is False
        assert "on" in fr.response["result"] and "off" in fr.response["result"]
        assert jarvis._dashboard.jarvis_mode_calls == []
    asyncio.run(_run())
    print("test_jarvis_mode_invalid_action_leaves_state_unchanged: PASS")


def test_jarvis_mode_on_message_differs_between_desktop_and_web() -> None:
    async def _run():
        desktop = _jarvis(auto_start=True)
        fr_d = await desktop._execute_tool(_fc("jarvis_mode", action="on"))
        web = _jarvis(auto_start=False)
        fr_w = await web._execute_tool(_fc("jarvis_mode", action="on"))
        result_d = fr_d.response["result"]
        result_w = fr_w.response["result"]
        assert "computer-control" in result_d.lower()
        assert "not gain" in result_w.lower() or "do not gain" in result_w.lower() or "does not gain" in result_w.lower()
    asyncio.run(_run())
    print("test_jarvis_mode_on_message_differs_between_desktop_and_web: PASS")


# ── [JARVIS_MODE] system_instruction block ─────────────────────────────────

def test_build_config_includes_jarvis_mode_block_off_by_default() -> None:
    jarvis = JarvisLive(HeadlessSurface())  # auto_start=True (desktop)
    config = jarvis._build_config()
    text = config.system_instruction
    assert "[JARVIS_MODE]" in text
    assert "OFF" in text
    assert "explicit" in text.lower() or "opt-in" in text.lower()
    print("test_build_config_includes_jarvis_mode_block_off_by_default: PASS")


def test_build_config_jarvis_mode_block_reflects_current_state() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._jarvis_mode = True
    config = jarvis._build_config()
    text = config.system_instruction
    assert "[JARVIS_MODE]" in text
    assert "currently ON" in text
    print("test_build_config_jarvis_mode_block_reflects_current_state: PASS")


def test_build_config_jarvis_mode_desktop_vs_web_capability_text_differs() -> None:
    desktop = JarvisLive(HeadlessSurface())          # auto_start=True
    web = JarvisLive(HeadlessSurface(), auto_start=False)
    desktop_text = desktop._build_config().system_instruction
    web_text = web._build_config().system_instruction
    # Isolate just the [JARVIS_MODE] section from each for a focused check.
    d_section = desktop_text.split("[JARVIS_MODE]")[1].split("[LOCATION]")[0]
    w_section = web_text.split("[JARVIS_MODE]")[1].split("[LOCATION]")[0]
    assert "observe" in d_section.lower()
    assert "observe" not in w_section.lower()
    assert "web session" in w_section.lower()
    print("test_build_config_jarvis_mode_desktop_vs_web_capability_text_differs: PASS")


# ── computer_control: new JARVIS-only actions require jarvis_mode ─────────

_NEW_ACTIONS = ["observe", "verify", "ui_find", "ui_click", "ui_type", "get_active_window_title"]


def test_computer_control_new_actions_blocked_without_jarvis_mode() -> None:
    async def _run():
        for action in _NEW_ACTIONS:
            jarvis = _jarvis(auto_start=True)
            assert jarvis._jarvis_mode is False
            fr = await jarvis._execute_tool(_fc("computer_control", action=action, description="Send"))
            result = fr.response["result"]
            assert "[JARVIS_MODE_REQUIRED]" in result, f"action={action} result={result!r}"
            assert jarvis._pending_vision is None
            assert jarvis._vision_busy is False
    asyncio.run(_run())
    print("test_computer_control_new_actions_blocked_without_jarvis_mode: PASS")


def test_computer_control_existing_actions_unaffected_by_jarvis_mode_off() -> None:
    # Backward compatibility: computer_control's EXISTING raw actions must
    # keep working exactly as before, regardless of JARVIS mode.
    async def _run():
        jarvis = _jarvis(auto_start=True)
        assert jarvis._jarvis_mode is False
        fr = await jarvis._execute_tool(_fc("computer_control", action="wait", seconds=0.01))
        result = fr.response["result"]
        assert "[JARVIS_MODE_REQUIRED]" not in result
        assert "Waited" in result
    asyncio.run(_run())
    print("test_computer_control_existing_actions_unaffected_by_jarvis_mode_off: PASS")


# ── observe / verify: same-session _pending_vision injection ──────────────

def _fake_capture_screen():
    return b"\xff\xd8\xff-fake-jpeg-bytes", "image/jpeg"


def test_computer_control_observe_opens_pending_vision_when_jarvis_mode_on() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        jarvis._jarvis_mode = True
        with patch.object(main_module, "_capture_screen", _fake_capture_screen), \
             patch.object(main_module, "get_active_window_title", lambda: "Notepad"):
            fr = await jarvis._execute_tool(
                _fc("computer_control", action="observe", description="is the file saved?")
            )
        result = fr.response["result"]
        assert "[VISION_ACTIVE]" in result
        assert jarvis._vision_busy is True
        assert jarvis._pending_vision is not None
        img_b, mime_t, question, angle = jarvis._pending_vision
        assert img_b == b"\xff\xd8\xff-fake-jpeg-bytes"
        assert mime_t == "image/jpeg"
        assert angle == "screen"
        assert "[JARVIS_OBSERVE]" in question
        assert "Notepad" in question
        assert "is the file saved?" in question
        # Never opens the camera — angle="screen" skips the
        # _vision_cam_active branch entirely (see main.py's own comment).
        assert jarvis._vision_cam_active is False
    asyncio.run(_run())
    print("test_computer_control_observe_opens_pending_vision_when_jarvis_mode_on: PASS")


def test_computer_control_verify_uses_verify_wording_not_observe_wording() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        jarvis._jarvis_mode = True
        with patch.object(main_module, "_capture_screen", _fake_capture_screen), \
             patch.object(main_module, "get_active_window_title", lambda: "WhatsApp"):
            fr = await jarvis._execute_tool(
                _fc("computer_control", action="verify", description="the message to John was sent")
            )
        assert "[VISION_ACTIVE]" in fr.response["result"]
        _, _, question, _ = jarvis._pending_vision
        assert "[JARVIS_VERIFY]" in question
        assert "[JARVIS_OBSERVE]" not in question
        assert "the message to John was sent" in question
        assert "WhatsApp" in question
    asyncio.run(_run())
    print("test_computer_control_verify_uses_verify_wording_not_observe_wording: PASS")


def test_computer_control_observe_cooldown_blocks_rapid_repeat() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)
        jarvis._jarvis_mode = True
        with patch.object(main_module, "_capture_screen", _fake_capture_screen), \
             patch.object(main_module, "get_active_window_title", lambda: "Chrome"):
            fr1 = await jarvis._execute_tool(_fc("computer_control", action="observe"))
            first_pending = jarvis._pending_vision
            fr2 = await jarvis._execute_tool(_fc("computer_control", action="observe"))
        assert "[VISION_ACTIVE]" in fr1.response["result"]
        assert "still in progress" in fr2.response["result"]
        # The second (blocked) call must not have clobbered the first
        # capture — this is the "no unbounded observe loop" bound.
        assert jarvis._pending_vision is first_pending
    asyncio.run(_run())
    print("test_computer_control_observe_cooldown_blocks_rapid_repeat: PASS")


def test_computer_control_observe_shares_busy_guard_with_screen_process() -> None:
    # Deliberate design choice (see main.py's comment on this): observe/
    # verify reuse screen_process's OWN self._vision_busy/_vision_last_time
    # guard rather than a second, parallel cooldown — one screen capture
    # in flight at a time, regardless of which tool asked for it.
    async def _run():
        jarvis = _jarvis(auto_start=True)
        jarvis._jarvis_mode = True
        jarvis._vision_busy = True   # simulate an in-flight screen_process capture
        with patch.object(main_module, "_capture_screen", _fake_capture_screen), \
             patch.object(main_module, "get_active_window_title", lambda: "Chrome"):
            fr = await jarvis._execute_tool(_fc("computer_control", action="observe"))
        assert "still in progress" in fr.response["result"]
        assert jarvis._pending_vision is None
    asyncio.run(_run())
    print("test_computer_control_observe_shares_busy_guard_with_screen_process: PASS")


# ── reconnect reset (source-level — a full reconnect isn't easily driven
# in a unit test; the reset block itself is a fixed, reviewable statement) ─

def test_reconnect_reset_block_resets_jarvis_mode() -> None:
    import inspect
    src = inspect.getsource(JarvisLive.run)
    assert "self._jarvis_mode           = False" in src
    print("test_reconnect_reset_block_resets_jarvis_mode: PASS")


if __name__ == "__main__":
    test_jarvis_mode_tool_is_declared()
    test_jarvis_mode_is_universal_not_desktop_only_or_web_only()
    test_computer_control_is_still_desktop_only()
    test_jarvis_mode_on_sets_flag_broadcasts_and_returns_directive()
    test_jarvis_mode_off_clears_flag_broadcasts_and_returns_directive()
    test_jarvis_mode_invalid_action_leaves_state_unchanged()
    test_jarvis_mode_on_message_differs_between_desktop_and_web()
    test_build_config_includes_jarvis_mode_block_off_by_default()
    test_build_config_jarvis_mode_block_reflects_current_state()
    test_build_config_jarvis_mode_desktop_vs_web_capability_text_differs()
    test_computer_control_new_actions_blocked_without_jarvis_mode()
    test_computer_control_existing_actions_unaffected_by_jarvis_mode_off()
    test_computer_control_observe_opens_pending_vision_when_jarvis_mode_on()
    test_computer_control_verify_uses_verify_wording_not_observe_wording()
    test_computer_control_observe_cooldown_blocks_rapid_repeat()
    test_computer_control_observe_shares_busy_guard_with_screen_process()
    test_reconnect_reset_block_resets_jarvis_mode()
    print("\nAll JARVIS mode tests passed.")
