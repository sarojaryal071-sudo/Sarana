"""
tests/test_browser_session_staleness.py — targeted correction for a
confirmed real-world bug (see the diagnostic report): _SessionRegistry
could reuse a cached _BrowserSession without ever checking whether the
underlying browser/session was still alive, surfacing as "a browser
error" on the next call instead of self-healing.

Two independent fixes, both reusing the EXISTING _BrowserSession/
_SessionRegistry classes (no new session manager):
  1. _SessionRegistry._get_or_create() now discards a cached session
     whose background thread has died BEFORE handing it back for reuse
     (_BrowserSession.is_alive()).
  2. open_media_url() now retries EXACTLY ONCE with a freshly created
     session if the cached one raises on actual use (the thread-alive-
     but-context/page-closed case, which can only be detected reactively).

Per this project's own established convention: no test here launches a
real browser — sessions are always faked/mocked.

Run with:
    .venv/Scripts/python.exe -m tests.test_browser_session_staleness
"""
from unittest.mock import MagicMock, patch

import actions.browser_control as bc


# ── _SessionRegistry._get_or_create(): proactive dead-thread discard ───

def test_get_or_create_discards_a_session_whose_thread_has_died() -> None:
    registry = bc._SessionRegistry()
    dead = MagicMock()
    dead.is_alive.return_value = False
    registry._sessions["chrome"] = dead

    with patch.object(bc._BrowserSession, "start", return_value=None):
        sess = registry._get_or_create("chrome")

    dead.is_alive.assert_called_once()
    assert sess is not dead
    assert registry._sessions["chrome"] is sess
    print("test_get_or_create_discards_a_session_whose_thread_has_died: PASS")


def test_get_or_create_reuses_a_healthy_session() -> None:
    registry = bc._SessionRegistry()
    healthy = MagicMock()
    healthy.is_alive.return_value = True
    registry._sessions["chrome"] = healthy

    sess = registry._get_or_create("chrome")

    assert sess is healthy
    print("test_get_or_create_reuses_a_healthy_session: PASS")


# ── _BrowserSession.is_alive() itself ───────────────────────────────────

def test_browser_session_is_alive_reflects_the_background_thread() -> None:
    sess = bc._BrowserSession("chrome")
    assert sess.is_alive() is False  # never started
    sess._thread = MagicMock()
    sess._thread.is_alive.return_value = True
    assert sess.is_alive() is True
    sess._thread.is_alive.return_value = False
    assert sess.is_alive() is False
    print("test_browser_session_is_alive_reflects_the_background_thread: PASS")


# ── open_media_url(): reactive retry-once on a stale (thread-alive) session ─

def test_open_media_url_retries_once_after_a_stale_session_fails() -> None:
    stale = MagicMock()
    stale.browser_name = "chrome"
    stale.go_to.return_value = "go_to_coro"
    stale.run.side_effect = RuntimeError("Target page, context or browser has been closed")

    fresh = MagicMock()
    fresh.go_to.return_value = "go_to_coro"
    fresh.run.return_value = "Opened: https://example.com/"

    with patch.object(bc._registry, "get", side_effect=[stale, fresh]) as m_get, \
         patch.object(bc._registry, "discard") as m_discard:
        result = bc.open_media_url("https://example.com/")

    m_discard.assert_called_once_with("chrome")
    assert m_get.call_count == 2
    assert result == "Opened: https://example.com/"
    print("test_open_media_url_retries_once_after_a_stale_session_fails: PASS")


def test_open_media_url_gives_up_honestly_if_the_retry_also_fails() -> None:
    # Bounded: never retried more than once, and the failure is reported
    # honestly, never upgraded to a fabricated success.
    always_broken = MagicMock()
    always_broken.browser_name = "chrome"
    always_broken.run.side_effect = RuntimeError("still closed")

    with patch.object(bc._registry, "get", return_value=always_broken) as m_get, \
         patch.object(bc._registry, "discard") as m_discard:
        result = bc.open_media_url("https://example.com/")

    assert m_get.call_count == 2   # original attempt + exactly one retry
    m_discard.assert_called_once_with("chrome")
    assert "Could not open" in result
    print("test_open_media_url_gives_up_honestly_if_the_retry_also_fails: PASS")


def test_open_media_url_never_calls_discard_on_a_healthy_first_attempt() -> None:
    healthy = MagicMock()
    healthy.run.return_value = "Opened: https://example.com/"
    with patch.object(bc._registry, "get", return_value=healthy) as m_get, \
         patch.object(bc._registry, "discard") as m_discard:
        result = bc.open_media_url("https://example.com/")
    m_get.assert_called_once()
    m_discard.assert_not_called()
    assert result == "Opened: https://example.com/"
    print("test_open_media_url_never_calls_discard_on_a_healthy_first_attempt: PASS")


# ── _SessionRegistry.discard() ───────────────────────────────────────────

def test_discard_removes_the_session_and_best_effort_closes_it() -> None:
    registry = bc._SessionRegistry()
    sess = MagicMock()
    registry._sessions["chrome"] = sess

    registry.discard("chrome")

    assert "chrome" not in registry._sessions
    sess.close.assert_called_once()
    print("test_discard_removes_the_session_and_best_effort_closes_it: PASS")


def test_discard_never_raises_even_if_close_itself_fails() -> None:
    registry = bc._SessionRegistry()
    sess = MagicMock()
    sess.close.side_effect = RuntimeError("already dead")
    registry._sessions["chrome"] = sess

    registry.discard("chrome")  # must not raise

    assert "chrome" not in registry._sessions
    print("test_discard_never_raises_even_if_close_itself_fails: PASS")


def _run() -> None:
    test_get_or_create_discards_a_session_whose_thread_has_died()
    test_get_or_create_reuses_a_healthy_session()
    test_browser_session_is_alive_reflects_the_background_thread()
    test_open_media_url_retries_once_after_a_stale_session_fails()
    test_open_media_url_gives_up_honestly_if_the_retry_also_fails()
    test_open_media_url_never_calls_discard_on_a_healthy_first_attempt()
    test_discard_removes_the_session_and_best_effort_closes_it()
    test_discard_never_raises_even_if_close_itself_fails()
    print("\nAll browser_session_staleness tests passed.")


if __name__ == "__main__":
    _run()
