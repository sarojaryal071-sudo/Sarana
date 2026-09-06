"""
tests/test_task_engine_youtube_navigation.py — targeted correction for a
confirmed real-world bug (see the diagnostic report): task_engine.py's
_run_youtube() used to pass the WHOLE raw objective to youtube_video()
as the search query, so "open YouTube" literally searched YouTube for
the phrase "open YouTube" and played whatever ranked first — reproduced
live: a real video titled "YouTube TV: Nothing but Net".

Covers the corrected semantics: a pure NAVIGATION objective ("open
YouTube", "open YouTube TV", "open YouTube in a new tab") now reuses the
EXISTING browser_control() go_to action (native-first, already-open-
browser-respecting) instead of ever calling youtube_video()'s play
action — no search, no video lookup, no playback. A PLAYBACK objective
("play X", "open YouTube and play X", "play X on YouTube") still reuses
youtube_video() exactly as before, now with the actual requested content
extracted instead of the whole sentence.

Per this project's own established convention: office_control/
youtube_video/browser_control are ALWAYS mocked here — no test opens a
real browser or plays real audio.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_youtube_navigation
"""
from unittest.mock import patch, MagicMock

import actions.task_engine as te


# ── _extract_youtube_query(): navigation vs. playback, deterministically ─

def test_bare_open_youtube_has_no_query_pure_navigation() -> None:
    assert te._extract_youtube_query("open YouTube") is None
    assert te._extract_youtube_query("Open YouTube.") is None
    print("test_bare_open_youtube_has_no_query_pure_navigation: PASS")


def test_open_youtube_tv_has_no_query_pure_navigation() -> None:
    assert te._extract_youtube_query("open YouTube TV") is None
    print("test_open_youtube_tv_has_no_query_pure_navigation: PASS")


def test_open_youtube_in_a_new_tab_has_no_query_pure_navigation() -> None:
    assert te._extract_youtube_query("open YouTube in a new tab") is None
    print("test_open_youtube_in_a_new_tab_has_no_query_pure_navigation: PASS")


def test_compound_objective_extracts_only_the_actual_content() -> None:
    # THE confirmed bug: this must be "the national anthem of nepal",
    # never the whole sentence.
    q = te._extract_youtube_query("open youtube and play the national anthem of nepal")
    assert q == "the national anthem of nepal"
    print("test_compound_objective_extracts_only_the_actual_content: PASS")


def test_play_x_on_youtube_strips_the_trailing_on_youtube() -> None:
    assert te._extract_youtube_query("play the national anthem of Nepal on YouTube") == "the national anthem of Nepal"
    assert te._extract_youtube_query("play the national anthem of Nepal on YouTube.") == "the national anthem of Nepal"
    print("test_play_x_on_youtube_strips_the_trailing_on_youtube: PASS")


def test_plain_play_query_is_unaffected() -> None:
    assert te._extract_youtube_query("play a Kafle song on YouTube") == "a Kafle song"
    print("test_plain_play_query_is_unaffected: PASS")


def test_extract_browser_name_recognizes_an_explicit_browser() -> None:
    assert te._extract_browser_name("open YouTube in the currently open Google Chrome") == "chrome"
    assert te._extract_browser_name("open YouTube") is None
    print("test_extract_browser_name_recognizes_an_explicit_browser: PASS")


# ── route(): a second real bug found DURING live verification of 2D ────

def test_route_open_youtube_in_current_chrome_reaches_the_youtube_domain() -> None:
    # Confirmed live during real-machine testing (not a guess): this
    # exact phrase used to score "browser" 2 ("open" + "chrome") against
    # youtube's 1 ("youtube"), routing a YouTube request to a literal
    # Google search for the whole sentence — _run_youtube()'s fix above
    # was never even reached. Fixed by removing the generic "open" verb
    # from browser's keyword list (see _DOMAINS's own comment).
    assert te.route("Open YouTube in a new tab in the currently open Chrome.") == "youtube"
    print("test_route_open_youtube_in_current_chrome_reaches_the_youtube_domain: PASS")


def test_route_existing_browser_objectives_are_unaffected_by_the_keyword_removal() -> None:
    assert te.route("open google.com and search for restaurants") == "browser"
    assert te.route("open a webpage") == "browser"
    assert te.route("search for something in the browser") == "browser"
    print("test_route_existing_browser_objectives_are_unaffected_by_the_keyword_removal: PASS")


def test_route_office_collisions_are_unaffected_by_the_keyword_removal() -> None:
    assert te.route("open Word") == "office"
    assert te.route("open Excel") == "office"
    print("test_route_office_collisions_are_unaffected_by_the_keyword_removal: PASS")


# ── _run_youtube(): navigation reuses browser_control, never searches ──

def test_run_youtube_bare_open_navigates_via_browser_control_never_searches() -> None:
    with patch.object(te, "browser_control", return_value="Opened: https://www.youtube.com") as m_bc, \
         patch.object(te, "youtube_video") as m_yt:
        result = te._run_youtube("open YouTube")
    m_yt.assert_not_called()  # no search, no video lookup, no playback
    m_bc.assert_called_once_with(parameters={"action": "go_to", "url": "https://www.youtube.com"})
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_run_youtube_bare_open_navigates_via_browser_control_never_searches: PASS")


def test_run_youtube_explicit_youtube_tv_navigates_to_the_tv_destination() -> None:
    with patch.object(te, "browser_control", return_value="Opened: https://tv.youtube.com") as m_bc, \
         patch.object(te, "youtube_video") as m_yt:
        result = te._run_youtube("open YouTube TV")
    m_yt.assert_not_called()
    m_bc.assert_called_once_with(parameters={"action": "go_to", "url": "https://tv.youtube.com"})
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_run_youtube_explicit_youtube_tv_navigates_to_the_tv_destination: PASS")


def test_run_youtube_never_infers_youtube_tv_from_a_generic_open_request() -> None:
    with patch.object(te, "browser_control", return_value="Opened: https://www.youtube.com") as m_bc:
        te._run_youtube("open YouTube")
    assert m_bc.call_args.kwargs["parameters"]["url"] == "https://www.youtube.com"
    print("test_run_youtube_never_infers_youtube_tv_from_a_generic_open_request: PASS")


def test_run_youtube_passes_an_explicit_browser_through_to_browser_control() -> None:
    with patch.object(te, "browser_control", return_value="Opened in chrome: https://www.youtube.com") as m_bc:
        te._run_youtube("open YouTube in the currently open Google Chrome")
    m_bc.assert_called_once_with(parameters={"action": "go_to", "url": "https://www.youtube.com", "browser": "chrome"})
    print("test_run_youtube_passes_an_explicit_browser_through_to_browser_control: PASS")


def test_run_youtube_playback_still_calls_youtube_video_with_the_clean_query() -> None:
    with patch.object(te, "youtube_video", return_value="[VERIFIED_SUCCESS] Playing: the national anthem of nepal.") as m_yt, \
         patch.object(te, "browser_control") as m_bc:
        result = te._run_youtube("open youtube and play the national anthem of nepal")
    m_bc.assert_not_called()
    m_yt.assert_called_once_with(
        parameters={"action": "play", "query": "the national anthem of nepal"}, player=None
    )
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_run_youtube_playback_still_calls_youtube_video_with_the_clean_query: PASS")


def test_run_youtube_navigation_never_fabricates_success_on_a_real_failure() -> None:
    with patch.object(te, "browser_control", return_value="Could not open: no display") as m_bc, \
         patch.object(te, "youtube_video") as m_yt:
        result = te._run_youtube("open YouTube")
    m_yt.assert_not_called()
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_run_youtube_navigation_never_fabricates_success_on_a_real_failure: PASS")


# ── End-to-end through execute_task(), routing included ─────────────────

def test_execute_task_open_youtube_end_to_end_never_calls_youtube_video() -> None:
    with patch.object(te, "browser_control", return_value="Opened: https://www.youtube.com"), \
         patch.object(te, "youtube_video") as m_yt:
        result = te.execute_task(parameters={"objective": "open YouTube"})
    m_yt.assert_not_called()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_execute_task_open_youtube_end_to_end_never_calls_youtube_video: PASS")


def _run() -> None:
    test_bare_open_youtube_has_no_query_pure_navigation()
    test_open_youtube_tv_has_no_query_pure_navigation()
    test_open_youtube_in_a_new_tab_has_no_query_pure_navigation()
    test_compound_objective_extracts_only_the_actual_content()
    test_play_x_on_youtube_strips_the_trailing_on_youtube()
    test_plain_play_query_is_unaffected()
    test_extract_browser_name_recognizes_an_explicit_browser()
    test_route_open_youtube_in_current_chrome_reaches_the_youtube_domain()
    test_route_existing_browser_objectives_are_unaffected_by_the_keyword_removal()
    test_route_office_collisions_are_unaffected_by_the_keyword_removal()
    test_run_youtube_bare_open_navigates_via_browser_control_never_searches()
    test_run_youtube_explicit_youtube_tv_navigates_to_the_tv_destination()
    test_run_youtube_never_infers_youtube_tv_from_a_generic_open_request()
    test_run_youtube_passes_an_explicit_browser_through_to_browser_control()
    test_run_youtube_playback_still_calls_youtube_video_with_the_clean_query()
    test_run_youtube_navigation_never_fabricates_success_on_a_real_failure()
    test_execute_task_open_youtube_end_to_end_never_calls_youtube_video()
    print("\nAll task_engine_youtube_navigation tests passed.")


if __name__ == "__main__":
    _run()
