"""
tests/test_identity_transition.py — ui.py's IdentityTransitionOverlay,
the desktop counterpart to the web frontend's
components/IdentityTransition.jsx (see that file's own header for the
full design brief: "not a plain cinematic transition... like in an AI
technological movie where the current UI goes through a fast rebuild of
another UI with moving neons and a cinematic building process").

Same convention as tests/test_sarana_face_canvas.py (which this file's
own offscreen-render technique is directly modeled on): QT_QPA_PLATFORM
is forced to "offscreen" before PyQt6 is even imported, so no real window
is ever opened. The state machine is driven directly (rewinding
_phase_start, calling the real _step()) rather than waiting on the
actual QTimer/event loop, exactly like every other animated widget test
in this file already does.

Run with:
    .venv/Scripts/python.exe -m tests.test_identity_transition
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must be set before PyQt6 import

import ui  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

with open("ui.py", "r", encoding="utf-8") as _f:
    _UI_SRC = _f.read()


def _new_overlay():
    ov = ui.IdentityTransitionOverlay(None)
    ov.resize(420, 500)
    return ov


# ── phase lifecycle ──────────────────────────────────────────────────────

def test_start_enters_deconstruct_phase_and_shows_itself() -> None:
    ov = _new_overlay()
    assert ov._phase is None
    assert not ov.isVisible()
    ov.start(True, lambda: None)
    assert ov._phase == "deconstruct"
    assert ov.isVisible()
    assert ov._tmr.isActive()
    print("test_start_enters_deconstruct_phase_and_shows_itself: PASS")


def test_on_rebuild_start_fires_exactly_once_at_the_deconstruct_rebuild_boundary() -> None:
    ov = _new_overlay()
    calls = []
    ov.start(True, lambda: calls.append(1))
    t0 = ov._phase_start
    # Not yet at the boundary — must not have fired.
    ov._phase_start = t0 - (ui._IT_DECONSTRUCT_MS - 1) / 1000.0
    ov._step()
    assert ov._phase == "deconstruct"
    assert calls == []
    # Cross the boundary.
    ov._phase_start = t0 - (ui._IT_DECONSTRUCT_MS + 1) / 1000.0
    ov._step()
    assert ov._phase == "rebuild"
    assert calls == [1]
    # Further steps within rebuild must not fire it again.
    ov._step()
    assert calls == [1]
    print("test_on_rebuild_start_fires_exactly_once_at_the_deconstruct_rebuild_boundary: PASS")


def test_transition_ends_hides_and_stops_the_timer() -> None:
    ov = _new_overlay()
    ov.start(True, lambda: None)
    t0 = ov._phase_start
    ov._phase_start = t0 - (ui._IT_DECONSTRUCT_MS + 1) / 1000.0
    ov._step()  # -> rebuild
    t1 = ov._phase_start
    ov._phase_start = t1 - (ui._IT_REBUILD_MS + 1) / 1000.0
    ov._step()  # -> end
    assert ov._phase is None
    assert not ov.isVisible()
    assert not ov._tmr.isActive()
    print("test_transition_ends_hides_and_stops_the_timer: PASS")


# ── real offscreen render coverage — no real window, no display needed ──

def test_paints_without_crashing_across_the_full_timeline_both_directions() -> None:
    for target_jarvis in (True, False):
        ov = _new_overlay()
        ov.start(target_jarvis, lambda: None)
        t0 = ov._phase_start
        for ms in (0, 100, 250, 400, 499):
            ov._phase_start = t0 - ms / 1000.0
            pm = ov.grab()
            assert not pm.isNull(), f"deconstruct grab() null at {ms}ms target_jarvis={target_jarvis}"
        ov._phase_start = t0 - (ui._IT_DECONSTRUCT_MS + 1) / 1000.0
        ov._step()
        t1 = ov._phase_start
        for ms in (0, 150, 260, 410, 600, 750, 899):
            ov._phase_start = t1 - ms / 1000.0
            pm = ov.grab()
            assert not pm.isNull(), f"rebuild grab() null at {ms}ms target_jarvis={target_jarvis}"
    print("test_paints_without_crashing_across_the_full_timeline_both_directions: PASS")


def test_degenerate_size_does_not_crash_paintevent() -> None:
    ov = _new_overlay()
    ov.resize(1, 1)
    ov.start(True, lambda: None)
    ov.grab()  # must not raise
    print("test_degenerate_size_does_not_crash_paintevent: PASS")


def test_paintevent_is_a_noop_when_no_phase_is_active() -> None:
    ov = _new_overlay()
    assert ov._phase is None
    pm = ov.grab()  # must not raise even though nothing has started yet
    assert not pm.isNull()
    print("test_paintevent_is_a_noop_when_no_phase_is_active: PASS")


# ── source-level guarantees ───────────────────────────────────────────────

def test_never_touches_hudcanvas_or_saranafacecanvas_internals() -> None:
    import inspect
    src = inspect.getsource(ui.IdentityTransitionOverlay)
    assert "self.hud" not in src
    assert "sarana_face" not in src
    print("test_never_touches_hudcanvas_or_saranafacecanvas_internals: PASS")


def test_apply_jarvis_mode_triggers_the_overlay_instead_of_an_instant_swap() -> None:
    block = _UI_SRC[_UI_SRC.index("self._jarvis_active = active"):][:400]
    assert "self._identity_transition.start(" in block
    assert "self._hud_cam_stack.setCurrentIndex(0 if active else 2)" in block
    print("test_apply_jarvis_mode_triggers_the_overlay_instead_of_an_instant_swap: PASS")


def test_overlay_is_constructed_as_a_child_of_the_hud_cam_stack() -> None:
    assert "self._identity_transition = IdentityTransitionOverlay(self._hud_cam_stack)" in _UI_SRC
    print("test_overlay_is_constructed_as_a_child_of_the_hud_cam_stack: PASS")


def test_uses_a_native_arc_sweep_for_the_ring_draw_effect_not_a_static_circle() -> None:
    import inspect
    src = inspect.getsource(ui.IdentityTransitionOverlay._paint_rings)
    assert "drawArc(" in src
    assert "span = int(t * 360 * 16)" in src
    print("test_uses_a_native_arc_sweep_for_the_ring_draw_effect_not_a_static_circle: PASS")


def test_target_identity_picks_the_right_accent_color() -> None:
    import inspect
    src = inspect.getsource(ui.IdentityTransitionOverlay.paintEvent)
    assert "accent_hex = C.ACC if self._target_jarvis else _FACE_GLOW" in src
    print("test_target_identity_picks_the_right_accent_color: PASS")


if __name__ == "__main__":
    test_start_enters_deconstruct_phase_and_shows_itself()
    test_on_rebuild_start_fires_exactly_once_at_the_deconstruct_rebuild_boundary()
    test_transition_ends_hides_and_stops_the_timer()
    test_paints_without_crashing_across_the_full_timeline_both_directions()
    test_degenerate_size_does_not_crash_paintevent()
    test_paintevent_is_a_noop_when_no_phase_is_active()
    test_never_touches_hudcanvas_or_saranafacecanvas_internals()
    test_apply_jarvis_mode_triggers_the_overlay_instead_of_an_instant_swap()
    test_overlay_is_constructed_as_a_child_of_the_hud_cam_stack()
    test_uses_a_native_arc_sweep_for_the_ring_draw_effect_not_a_static_circle()
    test_target_identity_picks_the_right_accent_color()
    print("\nAll identity_transition tests passed.")
