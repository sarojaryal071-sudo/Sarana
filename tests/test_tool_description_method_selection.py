"""
tests/test_tool_description_method_selection.py — J3 (Control-Method
Selection) of the locked JARVIS roadmap.

J3's own definition (docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md §10):
"the gap is not a missing layer — it's that the CHOICE between them
today depends on which tool Gemini happens to reach for, not an
explicit stated preference order... Do NOT build a naming/dispatch
abstraction for this." J3 is pure tool-DESCRIPTION language — no
dispatch code, no router, no new module — so there is nothing here to
unit-test at the execution level (main.py's _execute_tool() dispatch is
completely unchanged; see test_jarvis_task_boundary.py/test_jarvis_mode.py
for that layer's own regression coverage).

What CAN be verified deterministically is that the intended cross-tool
preference language actually exists in TOOL_DECLARATIONS and hasn't
silently regressed, and that no existing safety/Result-Envelope
directive was removed while adding it. Whether Gemini's OWN reasoning
is actually phrasing-independent is a live-model question, deliberately
NOT tested here (see docs) — that is a separate, explicitly-deferred
quality check, not part of this deterministic suite.

Run with:
    .venv/Scripts/python.exe -m tests.test_tool_description_method_selection
"""
from main import TOOL_DECLARATIONS


def _desc(name: str) -> str:
    return next(t for t in TOOL_DECLARATIONS if t["name"] == name)["description"]


# ── The three concrete gaps J3 closes (see the approved plan) ──────────

def test_computer_control_points_away_from_itself_for_open_app() -> None:
    d = _desc("computer_control").lower()
    assert "open_app" in d
    print("test_computer_control_points_away_from_itself_for_open_app: PASS")


def test_computer_control_points_away_from_itself_for_send_message() -> None:
    d = _desc("computer_control").lower()
    assert "send_message" in d
    print("test_computer_control_points_away_from_itself_for_send_message: PASS")


def test_computer_control_points_away_from_itself_for_browser_control() -> None:
    d = _desc("computer_control").lower()
    assert "browser_control" in d
    print("test_computer_control_points_away_from_itself_for_browser_control: PASS")


def test_open_app_states_its_own_preference_over_accomplish() -> None:
    d = _desc("open_app").lower()
    assert "accomplish" in d
    print("test_open_app_states_its_own_preference_over_accomplish: PASS")


def test_send_message_states_its_own_preference_over_accomplish() -> None:
    d = _desc("send_message").lower()
    assert "accomplish" in d
    print("test_send_message_states_its_own_preference_over_accomplish: PASS")


def test_browser_control_states_its_own_preference_over_computer_control() -> None:
    d = _desc("browser_control").lower()
    assert "computer_control" in d
    assert "smart_click" in d
    print("test_browser_control_states_its_own_preference_over_computer_control: PASS")


# ── Pre-existing preference language (Phases 3/4) must still be intact ─

def test_computer_settings_preference_over_accomplish_is_still_intact() -> None:
    d = _desc("computer_settings").lower()
    assert "preferred over computer_control" in d
    print("test_computer_settings_preference_over_accomplish_is_still_intact: PASS")


def test_office_control_preference_over_accomplish_is_still_intact() -> None:
    d = _desc("office_control").lower()
    assert "prefer this tool over accomplish" in d
    print("test_office_control_preference_over_accomplish_is_still_intact: PASS")


# ── Safety: no new sentence weakens an existing directive ───────────────

def test_send_message_confirmation_requirement_is_still_intact() -> None:
    d = _desc("send_message")
    assert "CONFIRMATION_REQUIRED" in d
    assert "confirmed=true" in d
    assert "never claim confirmed delivery" in d
    print("test_send_message_confirmation_requirement_is_still_intact: PASS")


def test_computer_control_result_envelope_directives_are_still_intact() -> None:
    d = _desc("computer_control")
    for tag in ("[VERIFIED_SUCCESS]", "[VERIFIED_FAILURE]", "[INCONCLUSIVE]", "[UI_AMBIGUOUS]", "[CONFIRMATION_REQUIRED]"):
        assert tag in d
    assert "never infer confirmation from the original request or unrelated speech" in d
    assert "never assume it worked" in d
    print("test_computer_control_result_envelope_directives_are_still_intact: PASS")


def test_open_app_result_envelope_directives_are_still_intact() -> None:
    d = _desc("open_app")
    for tag in ("[VERIFIED_SUCCESS]", "[VERIFIED_FAILURE]", "[INCONCLUSIVE]"):
        assert tag in d
    print("test_open_app_result_envelope_directives_are_still_intact: PASS")


def test_no_description_tells_gemini_to_bypass_confirmation_or_ignore_status() -> None:
    # A crude but real guard: none of the touched descriptions should
    # ever contain phrasing that instructs skipping a status check or
    # inferring confirmation. Note the EXISTING, correct directives
    # already say the negation of these phrases ("never assume it
    # worked", "never infer confirmation") — this checks for the bare,
    # un-negated form only.
    banned = ("ignore the result", "skip confirmation")
    for name in ("computer_control", "browser_control", "send_message", "open_app"):
        d = _desc(name).lower()
        for phrase in banned:
            assert phrase not in d, f"{name} description contains banned phrase: {phrase!r}"
        assert "never assume it worked" in d or "assume it worked" not in d
        assert "never infer confirmation" in d or "infer confirmation" not in d
    print("test_no_description_tells_gemini_to_bypass_confirmation_or_ignore_status: PASS")


def _run() -> None:
    test_computer_control_points_away_from_itself_for_open_app()
    test_computer_control_points_away_from_itself_for_send_message()
    test_computer_control_points_away_from_itself_for_browser_control()
    test_open_app_states_its_own_preference_over_accomplish()
    test_send_message_states_its_own_preference_over_accomplish()
    test_browser_control_states_its_own_preference_over_computer_control()
    test_computer_settings_preference_over_accomplish_is_still_intact()
    test_office_control_preference_over_accomplish_is_still_intact()
    test_send_message_confirmation_requirement_is_still_intact()
    test_computer_control_result_envelope_directives_are_still_intact()
    test_open_app_result_envelope_directives_are_still_intact()
    test_no_description_tells_gemini_to_bypass_confirmation_or_ignore_status()
    print("\nAll tool_description_method_selection tests passed.")


if __name__ == "__main__":
    _run()
