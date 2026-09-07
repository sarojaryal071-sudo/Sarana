"""
tests/test_presentation_load_retry.py -- regression lock for a real,
observed bug: the embedded Presentation Engine's (ui.py's QWebEngineView
loading frontend/dist/desktop.html) first navigation can fail transiently
(ERR_EMPTY_RESPONSE -- confirmed via a live launch with QtWebEngine
remote debugging attached) and, before this fix, nothing ever noticed or
retried -- the view just sat on Chromium's own native error page forever.
Every later weather/calendar/etc. broadcast still arrived (see
_push_to_presentation_webview()) but silently did nothing, since
window.__jarvisBridge never got installed on the broken page -- exactly
the "JARVIS replies by voice, no overlay ever appears" symptom a real
user hit.

Source-inspection style (same convention as this project's other ui.py-
adjacent tests, e.g. test_jarvis_mode.py's own note) rather than a full
QApplication/QWebEngineView instantiation -- PyQt6-WebEngine is heavy to
spin up in a headless test process and this project's own established
pattern for verifying this integration is real, live, CDP-attached
verification (see docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md), not a
mocked unit test. This test locks in that the RETRY MECHANISM itself
exists and is wired correctly, complementing that real-world check.

Run with:
    .venv/Scripts/python.exe -m tests.test_presentation_load_retry
"""
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "ui.py"
_UI_SRC = _SRC.read_text(encoding="utf-8")


def test_retry_constants_exist_and_are_bounded() -> None:
    assert "_PRESENTATION_LOAD_MAX_RETRIES = 4" in _UI_SRC
    assert "_PRESENTATION_LOAD_RETRY_MS = 500" in _UI_SRC
    print("test_retry_constants_exist_and_are_bounded: PASS")


def test_load_finished_is_actually_connected_after_setting_the_url() -> None:
    """The real bug: setUrl() was previously fire-and-forget, with no
    signal ever telling this code whether the navigation succeeded."""
    idx = _UI_SRC.index('self._content_webview.setUrl(QUrl(f"http://127.0.0.1:{port}/desktop.html"))')
    following = _UI_SRC[idx:idx + 1500]
    assert "self._presentation_load_attempts = 0" in following
    assert "self._content_webview.loadFinished.connect(self._on_presentation_load_finished)" in following
    print("test_load_finished_is_actually_connected_after_setting_the_url: PASS")


def test_on_presentation_load_finished_resets_the_counter_on_success() -> None:
    idx = _UI_SRC.index("def _on_presentation_load_finished(self, ok: bool) -> None:")
    body = _UI_SRC[idx:idx + 1400]
    assert "if ok or self._content_webview is None:" in body
    assert "self._presentation_load_attempts = 0" in body
    print("test_on_presentation_load_finished_resets_the_counter_on_success: PASS")


def test_on_presentation_load_finished_retries_with_a_bounded_backoff() -> None:
    idx = _UI_SRC.index("def _on_presentation_load_finished(self, ok: bool) -> None:")
    body = _UI_SRC[idx:idx + 1400]
    assert "self._presentation_load_attempts += 1" in body
    assert "if self._presentation_load_attempts > _PRESENTATION_LOAD_MAX_RETRIES:" in body
    assert "QTimer.singleShot(_PRESENTATION_LOAD_RETRY_MS, _retry)" in body
    print("test_on_presentation_load_finished_retries_with_a_bounded_backoff: PASS")


def test_exhausted_retries_produce_an_honest_degraded_message_never_a_silent_stuck_error_page() -> None:
    idx = _UI_SRC.index("def _on_presentation_load_finished(self, ok: bool) -> None:")
    body = _UI_SRC[idx:idx + 1400]
    assert "Presentation Engine failed to load after several" in body
    assert "restart JARVIS to try again" in body
    print("test_exhausted_retries_produce_an_honest_degraded_message_never_a_silent_stuck_error_page: PASS")


def test_retry_guards_against_the_webview_being_deleted_during_the_backoff_window() -> None:
    """A real, distinct crash risk: the app (or this panel) closing while
    a QTimer.singleShot() retry is still pending would call setUrl() on
    an already-deleted Qt C++ object underneath the still-live Python
    reference -- PyQt6 raises RuntimeError for exactly this, not a normal
    Python exception a bare try/except Exception would be needed for."""
    idx = _UI_SRC.index("def _retry():")
    body = _UI_SRC[idx:idx + 400]
    assert "try:" in body
    assert "webview.setUrl(url)" in body
    assert "except RuntimeError:" in body
    print("test_retry_guards_against_the_webview_being_deleted_during_the_backoff_window: PASS")


if __name__ == "__main__":
    test_retry_constants_exist_and_are_bounded()
    test_load_finished_is_actually_connected_after_setting_the_url()
    test_on_presentation_load_finished_resets_the_counter_on_success()
    test_on_presentation_load_finished_retries_with_a_bounded_backoff()
    test_exhausted_retries_produce_an_honest_degraded_message_never_a_silent_stuck_error_page()
    test_retry_guards_against_the_webview_being_deleted_during_the_backoff_window()
    print("\nAll presentation-load-retry tests passed.")
