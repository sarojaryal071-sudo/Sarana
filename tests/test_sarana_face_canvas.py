"""
tests/test_sarana_face_canvas.py — desktop counterpart to the web
frontend's faceMesh.test.mjs/SaranaFace.test.mjs (see those files' own
headers), covering ui.py's SaranaFaceCanvas: a simple, bold, filled-shape
face (two round glowing eyes + highlight, two thick brow strokes, one
mouth curve, two soft cheek glows) built directly from the user's own
reference image, matching the web build's own generation.

Two kinds of coverage, mirroring this project's own established
convention on the frontend side (real behavior tests for logic,
source-inspection for structural/wiring invariants that would otherwise
need a full running window to check):

1. REAL offscreen render tests — SaranaFaceCanvas and (unmodified)
   HudCanvas are constructed under Qt's "offscreen" QPA platform plugin
   (no real window is ever shown, no display is required) and painted
   via .grab() across every reachable state/expression, including several
   real audio_level values driving the speaking mouth. This is the
   "implementation -> offscreen call -> verified correct behavior"
   standard this project's safety practice asks for, in place of
   anything that could touch the real machine.

2. SOURCE-INSPECTION wiring tests — MainWindow itself is deliberately
   NOT constructed here (it reads config, checks Windows autostart
   registry state, etc. — real side effects this test suite has no
   business triggering just to verify UI wiring). Instead these tests
   read ui.py's own source text and assert the specific wiring lines
   exist, exactly the technique frontend/src/components/*.test.mjs
   already uses throughout this project for the same reason.

Run with:
    .venv/Scripts/python.exe -m tests.test_sarana_face_canvas
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must be set before PyQt6 import

import ui  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

with open("ui.py", "r", encoding="utf-8") as _f:
    _UI_SRC = _f.read()


# ── expression mapping (mirrors lib/faceExpressions.js's own precedence) ─

def test_expression_mapping_matches_the_web_build_precedence_exactly() -> None:
    face = ui.SaranaFaceCanvas("SARANA")
    cases = [
        (dict(muted=True, speaking=False, state="LISTENING"), "concerned"),
        (dict(muted=False, speaking=True, state="LISTENING"), "speaking"),
        (dict(muted=False, speaking=False, state="THINKING"), "thinking"),
        (dict(muted=False, speaking=False, state="SLEEPING"), "neutral"),
        (dict(muted=False, speaking=False, state="LISTENING"), "listening"),
    ]
    for attrs, expected in cases:
        face.muted, face.speaking, face.state = attrs["muted"], attrs["speaking"], attrs["state"]
        assert face._expression() == expected, f"{attrs} -> expected {expected}, got {face._expression()}"
        assert face._expression() in ui._SARANA_FACE_EXPRESSIONS
    print("test_expression_mapping_matches_the_web_build_precedence_exactly: PASS")


def test_only_five_expressions_are_reachable_from_mechanical_status_alone() -> None:
    # _SARANA_FACE_EXPRESSIONS is specifically the set _mechanical_expression()
    # can return from real app status with NO override involved — the
    # other ten (happy/curious/sad/etc.) are fully rendered (see
    # _SARANA_EXPRESSIONS below) but only reachable via an explicit
    # set_expression tool call (see test_expression_override_* below),
    # never guessed from status alone.
    assert ui._SARANA_FACE_EXPRESSIONS == {"neutral", "listening", "thinking", "speaking", "concerned"}
    print("test_only_five_expressions_are_reachable_from_mechanical_status_alone: PASS")


def test_all_fifteen_expressions_have_real_render_parameters() -> None:
    # SARANA Face UI (set_expression tool) — the gap a real user hit
    # directly: they asked for "sad" and were told it couldn't be shown.
    # Every word in the shared vocabulary must have an actual params
    # entry here, not just exist as a string somewhere.
    expected = {
        "neutral", "listening", "thinking", "speaking", "concerned", "happy",
        "sad", "curious", "confused", "reassuring", "empathetic", "surprised",
        "calm", "focused", "excited",
    }
    assert set(ui._SARANA_EXPRESSIONS.keys()) == expected
    print("test_all_fifteen_expressions_have_real_render_parameters: PASS")


# ── expression override (set_expression tool) — priority + expiry ────────

def test_expression_override_wins_only_while_mechanical_state_is_idle() -> None:
    face = ui.SaranaFaceCanvas("SARANA")
    face.set_expression_override("sad", 10.0)
    face.state, face.speaking, face.muted = "LISTENING", False, False
    assert face._expression() == "sad"
    face.speaking = True
    assert face._expression() == "speaking", "speaking must win over a requested mood"
    face.speaking = False
    face.muted = True
    assert face._expression() == "concerned", "muted must win — it's a real functional signal"
    face.muted = False
    face.state = "THINKING"
    assert face._expression() == "thinking", "thinking must win over a requested mood"
    face.state = "LISTENING"
    assert face._expression() == "sad", "override re-applies once mechanical state returns to idle"
    print("test_expression_override_wins_only_while_mechanical_state_is_idle: PASS")


def test_expression_override_expires_on_its_own_and_reverts_to_mechanical() -> None:
    import time
    face = ui.SaranaFaceCanvas("SARANA")
    face.state, face.speaking, face.muted = "LISTENING", False, False
    face.set_expression_override("excited", 0.01)
    assert face._expression() == "excited"
    time.sleep(0.05)
    assert face._expression() == "listening"
    print("test_expression_override_expires_on_its_own_and_reverts_to_mechanical: PASS")


def test_no_override_set_behaves_exactly_like_before() -> None:
    face = ui.SaranaFaceCanvas("SARANA")
    face.state, face.speaking, face.muted = "LISTENING", False, False
    assert face._expression() == "listening"
    print("test_no_override_set_behaves_exactly_like_before: PASS")


def test_paints_without_crashing_for_every_override_only_expression() -> None:
    for expr in sorted(ui._SARANA_EXPRESSIONS.keys() - ui._SARANA_FACE_EXPRESSIONS):
        face = ui.SaranaFaceCanvas("SARANA")
        face.resize(360, 420)
        face.set_expression_override(expr, 10.0)
        assert face._expression() == expr
        face._step()
        pm = face.grab()
        assert not pm.isNull(), f"grab() produced a null pixmap for override expression={expr}"
    print("test_paints_without_crashing_for_every_override_only_expression: PASS")


# ── real offscreen render coverage — no real window, no display needed ──

def test_paints_without_crashing_across_every_reachable_state_and_audio_level() -> None:
    cases = [
        ("LISTENING", False, False, 0.0),
        ("THINKING", False, False, 0.0),
        ("SPEAKING", True, False, 0.0),
        ("SPEAKING", True, False, 0.5),
        ("SPEAKING", True, False, 1.0),
        ("SLEEPING", False, False, 0.0),
        ("LISTENING", False, True, 0.0),  # muted
    ]
    for state, speaking, muted, lvl in cases:
        face = ui.SaranaFaceCanvas("SARANA")
        face.resize(360, 420)
        face.state, face.speaking, face.muted, face.audio_level = state, speaking, muted, lvl
        face._step()
        pm = face.grab()
        assert not pm.isNull(), f"grab() produced a null pixmap for state={state} lvl={lvl}"
        assert pm.width() > 0 and pm.height() > 0
    print("test_paints_without_crashing_across_every_reachable_state_and_audio_level: PASS")


def test_audio_level_is_clamped_defensively_even_if_a_caller_sends_an_out_of_range_value() -> None:
    face = ui.SaranaFaceCanvas("SARANA")
    face.resize(360, 420)
    face.state, face.speaking = "LISTENING", True
    for bad in (-5.0, 5.0, float("nan")):
        face.audio_level = bad
        try:
            pm = face.grab()
        except Exception as e:  # pragma: no cover - failure path
            raise AssertionError(f"paintEvent crashed on audio_level={bad}: {e}")
        assert not pm.isNull()
    print("test_audio_level_is_clamped_defensively_even_if_a_caller_sends_an_out_of_range_value: PASS")


def test_blink_and_degenerate_size_do_not_crash_paintevent() -> None:
    face = ui.SaranaFaceCanvas("SARANA")
    face.resize(360, 420)
    face._blink = True
    pm = face.grab()
    assert not pm.isNull()
    face.resize(1, 1)  # exercises paintEvent's own W<10/H<10 early return
    face.grab()  # must not raise
    print("test_blink_and_degenerate_size_do_not_crash_paintevent: PASS")


def test_step_skips_all_work_while_not_visible() -> None:
    # Performance requirement: no per-frame work while this widget isn't
    # the one showing in _hud_cam_stack. A never-shown widget's
    # isVisible() is False under the offscreen platform too, so this is
    # a real (not mocked) check of the guard in SaranaFaceCanvas._step().
    face = ui.SaranaFaceCanvas("SARANA")
    assert face.isVisible() is False
    tick_before = face._tick
    face._step()
    assert face._tick == tick_before, "_step() must no-op while the widget isn't visible"
    print("test_step_skips_all_work_while_not_visible: PASS")


def test_hudcanvas_itself_still_constructs_and_paints_unmodified() -> None:
    # Confirms the JARVIS orb widget is untouched and still fully
    # functional — constructed with an empty face_path (no image file
    # needed; _load_face's own except-branch already handles that).
    hud = ui.HudCanvas("", "JARVIS")
    hud.resize(360, 420)
    hud.speaking = True
    hud._step()
    pm = hud.grab()
    assert not pm.isNull()
    print("test_hudcanvas_itself_still_constructs_and_paints_unmodified: PASS")


# ── source-inspection wiring checks (MainWindow itself isn't constructed
#    here — see this file's own header for why) ─────────────────────────

def test_hudcanvas_class_body_contains_no_reference_to_the_new_face_system() -> None:
    start = _UI_SRC.index("class HudCanvas(QWidget):")
    end = _UI_SRC.index("\n\n\n# ── SaranaFaceCanvas geometry")
    body = _UI_SRC[start:end]
    assert "SaranaFace" not in body
    assert "_eye_path" not in body
    print("test_hudcanvas_class_body_contains_no_reference_to_the_new_face_system: PASS")


def test_mainwindow_stacks_sarana_face_as_a_third_index_defaulting_to_sarana() -> None:
    assert "self._hud_cam_stack.addWidget(self.sarana_face)" in _UI_SRC
    assert "self._hud_cam_stack.setCurrentIndex(2)" in _UI_SRC
    print("test_mainwindow_stacks_sarana_face_as_a_third_index_defaulting_to_sarana: PASS")


def test_apply_jarvis_mode_switches_the_stack_and_respects_an_active_camera() -> None:
    assert 'self._hud_cam_stack.setCurrentIndex(0 if active else 2)' in _UI_SRC
    assert "self._hud_cam_stack.currentIndex() != 1" in _UI_SRC
    print("test_apply_jarvis_mode_switches_the_stack_and_respects_an_active_camera: PASS")


def test_cam_stream_stop_restores_the_previously_active_identity_not_a_hardcoded_orb() -> None:
    assert "self._hud_cam_stack.setCurrentIndex(0 if self._jarvis_active else 2)" in _UI_SRC
    print("test_cam_stream_stop_restores_the_previously_active_identity_not_a_hardcoded_orb: PASS")


def test_apply_state_mirrors_onto_sarana_face_too() -> None:
    block = _UI_SRC[_UI_SRC.index("def _apply_state(self, state: str):"):][:700]
    assert "self.sarana_face.state" in block
    assert "self.sarana_face.speaking" in block
    print("test_apply_state_mirrors_onto_sarana_face_too: PASS")


def test_apply_audio_level_mirrors_onto_sarana_face_too() -> None:
    block = _UI_SRC[_UI_SRC.index("def _apply_audio_level(self, level: float):"):][:200]
    assert "self.sarana_face.audio_level" in block
    print("test_apply_audio_level_mirrors_onto_sarana_face_too: PASS")


def test_toggle_mute_mirrors_onto_sarana_face_too() -> None:
    block = _UI_SRC[_UI_SRC.index("def _toggle_mute(self):"):][:300]
    assert "self.sarana_face.muted" in block
    print("test_toggle_mute_mirrors_onto_sarana_face_too: PASS")


def test_apply_name_update_mirrors_onto_sarana_face_too() -> None:
    block = _UI_SRC[_UI_SRC.index("def _apply_name_update("):][:700]
    assert "self.sarana_face._assistant_name" in block
    print("test_apply_name_update_mirrors_onto_sarana_face_too: PASS")


def test_expression_override_signal_wiring_exists() -> None:
    assert "_expression_sig  = pyqtSignal(str, float)" in _UI_SRC
    assert "self._expression_sig.connect(self._apply_expression_override)" in _UI_SRC
    block = _UI_SRC[_UI_SRC.index("def _apply_expression_override("):][:900]
    assert "self.sarana_face.set_expression_override(expression, duration_seconds)" in block
    print("test_expression_override_signal_wiring_exists: PASS")


def test_jarvisui_set_expression_emits_the_signal() -> None:
    block = _UI_SRC[_UI_SRC.index("def set_expression(self, expression: str, duration_seconds: float):"):][:400]
    assert "self._win._expression_sig.emit(str(expression), float(duration_seconds))" in block
    print("test_jarvisui_set_expression_emits_the_signal: PASS")


if __name__ == "__main__":
    test_expression_mapping_matches_the_web_build_precedence_exactly()
    test_only_five_expressions_are_reachable_from_mechanical_status_alone()
    test_all_fifteen_expressions_have_real_render_parameters()
    test_expression_override_wins_only_while_mechanical_state_is_idle()
    test_expression_override_expires_on_its_own_and_reverts_to_mechanical()
    test_no_override_set_behaves_exactly_like_before()
    test_paints_without_crashing_for_every_override_only_expression()
    test_paints_without_crashing_across_every_reachable_state_and_audio_level()
    test_audio_level_is_clamped_defensively_even_if_a_caller_sends_an_out_of_range_value()
    test_blink_and_degenerate_size_do_not_crash_paintevent()
    test_step_skips_all_work_while_not_visible()
    test_hudcanvas_itself_still_constructs_and_paints_unmodified()
    test_hudcanvas_class_body_contains_no_reference_to_the_new_face_system()
    test_mainwindow_stacks_sarana_face_as_a_third_index_defaulting_to_sarana()
    test_apply_jarvis_mode_switches_the_stack_and_respects_an_active_camera()
    test_cam_stream_stop_restores_the_previously_active_identity_not_a_hardcoded_orb()
    test_apply_state_mirrors_onto_sarana_face_too()
    test_apply_audio_level_mirrors_onto_sarana_face_too()
    test_toggle_mute_mirrors_onto_sarana_face_too()
    test_apply_name_update_mirrors_onto_sarana_face_too()
    test_expression_override_signal_wiring_exists()
    test_jarvisui_set_expression_emits_the_signal()
    print("\nAll sarana_face_canvas tests passed.")
