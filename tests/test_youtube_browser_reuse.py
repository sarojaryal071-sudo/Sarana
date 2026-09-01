"""
tests/test_youtube_browser_reuse.py — three real bugs reported together,
one shared root cause: actions/youtube_video.py's play action used to
shell out directly to the OS (subprocess.Popen(["...", "start", url])),
completely bypassing actions/browser_control.py's own tracked session
registry. That meant:
  1. Every "play/search another video" call opened a brand new,
     untracked browser window instead of reusing the same tab.
  2. browser_control(action="close") had truthfully nothing to close
     afterward — a plain OS-level launch leaves Python with no handle
     on it at all.
  3. (Compounding, separate root cause) the youtube_video tool's own
     description gave the model no instruction to keep using THIS tool
     across a clarifying question ("what song?") — nothing stopped it
     from handing the follow-up off to web_search instead.

Fix 1+2: actions/browser_control.py's new open_media_url() routes
through the SAME _registry/_BrowserSession machinery every other
browser_control action already uses, instead of a second, parallel,
untracked way of opening a URL.
Fix 3: the youtube_video tool's own description now explicitly says to
call itself again with the answer, never hand off elsewhere.

Per this project's own established safety practice for anything that
would touch real hardware/a real running application: NO test here ever
launches a real browser. _registry.get() and the resulting session are
mocked in every test — what's verified is that the CORRECT calls happen
against that mock, exactly the "implementation -> mocked call -> correct
dispatch logic" standard this project's other test suites already use.

Run with:
    .venv/Scripts/python.exe -m tests.test_youtube_browser_reuse
"""
from unittest.mock import patch, MagicMock

import actions.browser_control as bc
import actions.youtube_video as yv
from main import TOOL_DECLARATIONS


# ── browser_control.open_media_url() ────────────────────────────────────

def test_open_media_url_routes_through_the_shared_registry_not_a_new_native_open() -> None:
    fake_session = MagicMock()
    fake_session.go_to.return_value = "go_to_coro"
    fake_session.run.return_value = "Opened: https://example.com/"
    with patch.object(bc._registry, "get", return_value=fake_session) as m_get:
        result = bc.open_media_url("https://example.com/")
    m_get.assert_called_once_with(None)
    fake_session.go_to.assert_called_once_with("https://example.com/")
    fake_session.run.assert_called_once_with("go_to_coro")
    assert result == "Opened: https://example.com/"
    print("test_open_media_url_routes_through_the_shared_registry_not_a_new_native_open: PASS")


def test_open_media_url_reuses_the_same_session_object_across_two_calls() -> None:
    # The actual fix for "opens a new one every time" — _registry.get()
    # is what returns the SAME already-running session on a second call
    # (it only creates a new one if none exists yet for that browser
    # name) — this test locks in that open_media_url always goes through
    # get(), never bypasses it to construct a fresh session directly.
    fake_session = MagicMock()
    fake_session.run.return_value = "Opened: url"
    with patch.object(bc._registry, "get", return_value=fake_session) as m_get:
        bc.open_media_url("https://a.example/")
        bc.open_media_url("https://b.example/")
    assert m_get.call_count == 2
    assert fake_session.go_to.call_count == 2
    print("test_open_media_url_reuses_the_same_session_object_across_two_calls: PASS")


def test_open_media_url_respects_an_explicit_browser_choice() -> None:
    fake_session = MagicMock()
    fake_session.run.return_value = "Opened: url"
    with patch.object(bc._registry, "get", return_value=fake_session) as m_get:
        bc.open_media_url("https://example.com/", browser="firefox")
    m_get.assert_called_once_with("firefox")
    print("test_open_media_url_respects_an_explicit_browser_choice: PASS")


def test_open_media_url_never_crashes_on_a_session_error() -> None:
    with patch.object(bc._registry, "get", side_effect=RuntimeError("no display")):
        result = bc.open_media_url("https://example.com/")
    assert "Could not open" in result
    print("test_open_media_url_never_crashes_on_a_session_error: PASS")


def test_open_media_url_reports_an_honest_timeout_not_a_crash() -> None:
    import concurrent.futures
    fake_session = MagicMock()
    fake_session.run.side_effect = concurrent.futures.TimeoutError()
    with patch.object(bc._registry, "get", return_value=fake_session):
        result = bc.open_media_url("https://example.com/")
    assert "timed out" in result.lower()
    print("test_open_media_url_reports_an_honest_timeout_not_a_crash: PASS")


# ── youtube_video.py: _handle_play routes through open_media_url ───────

def test_handle_play_uses_open_media_url_for_a_successfully_scraped_video() -> None:
    with patch.object(yv, "_scrape_first_video_url", return_value="https://www.youtube.com/watch?v=abc12345678"), \
         patch.object(yv, "open_media_url") as m_open:
        result = yv._handle_play({"query": "some song"}, player=None)
    m_open.assert_called_once_with("https://www.youtube.com/watch?v=abc12345678")
    assert "Playing: some song" in result
    print("test_handle_play_uses_open_media_url_for_a_successfully_scraped_video: PASS")


def test_handle_play_uses_open_media_url_for_the_fallback_search_page_too() -> None:
    with patch.object(yv, "_scrape_first_video_url", return_value=None), \
         patch.object(yv, "open_media_url") as m_open:
        result = yv._handle_play({"query": "some song"}, player=None)
    m_open.assert_called_once()
    (fallback_url,), _ = m_open.call_args
    assert "youtube.com/results" in fallback_url
    assert "some+song" in fallback_url or "some%20song" in fallback_url
    assert "manual selection required" in result
    print("test_handle_play_uses_open_media_url_for_the_fallback_search_page_too: PASS")


def test_handle_play_never_shells_out_to_the_os_directly_anymore() -> None:
    # Regression guard for the actual root cause — if this ever silently
    # reverts to subprocess.Popen(["...", "start", url]), this test
    # catches it even if nobody notices the missing open_media_url call.
    with patch.object(yv, "_scrape_first_video_url", return_value="https://www.youtube.com/watch?v=abc12345678"), \
         patch("subprocess.Popen") as m_popen, \
         patch.object(bc._registry, "get", return_value=MagicMock()):
        yv._handle_play({"query": "some song"}, player=None)
    m_popen.assert_not_called()
    print("test_handle_play_never_shells_out_to_the_os_directly_anymore: PASS")


def test_open_url_function_no_longer_exists_dead_code_was_removed() -> None:
    assert not hasattr(yv, "_open_url"), "the old untracked native-open helper should be gone, not just unused"
    print("test_open_url_function_no_longer_exists_dead_code_was_removed: PASS")


def test_handle_play_still_asks_for_a_query_when_none_is_given() -> None:
    # Unrelated to the bug fix — confirms this pre-existing, correct
    # behavior (the actual trigger for the reported clarifying-question
    # flow) is untouched.
    with patch.object(yv, "open_media_url") as m_open:
        result = yv._handle_play({}, player=None)
    m_open.assert_not_called()
    assert "what you'd like to watch" in result.lower()
    print("test_handle_play_still_asks_for_a_query_when_none_is_given: PASS")


# ── main.py: the tool description now forbids handing off mid-task ─────

def test_youtube_video_tool_description_forbids_handoff_to_web_search() -> None:
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "youtube_video")
    desc = decl["description"].lower()
    assert "web_search" in desc
    assert "never" in desc
    assert "same tool" in desc or "this same tool" in desc
    print("test_youtube_video_tool_description_forbids_handoff_to_web_search: PASS")


def test_youtube_video_tool_description_mentions_reusing_the_same_tab() -> None:
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "youtube_video")
    desc = decl["description"].lower()
    assert "same browser tab" in desc or "reuses the same" in desc
    print("test_youtube_video_tool_description_mentions_reusing_the_same_tab: PASS")


if __name__ == "__main__":
    test_open_media_url_routes_through_the_shared_registry_not_a_new_native_open()
    test_open_media_url_reuses_the_same_session_object_across_two_calls()
    test_open_media_url_respects_an_explicit_browser_choice()
    test_open_media_url_never_crashes_on_a_session_error()
    test_open_media_url_reports_an_honest_timeout_not_a_crash()
    test_handle_play_uses_open_media_url_for_a_successfully_scraped_video()
    test_handle_play_uses_open_media_url_for_the_fallback_search_page_too()
    test_handle_play_never_shells_out_to_the_os_directly_anymore()
    test_open_url_function_no_longer_exists_dead_code_was_removed()
    test_handle_play_still_asks_for_a_query_when_none_is_given()
    test_youtube_video_tool_description_forbids_handoff_to_web_search()
    test_youtube_video_tool_description_mentions_reusing_the_same_tab()
    print("\nAll youtube_browser_reuse tests passed.")
