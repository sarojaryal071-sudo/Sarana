"""
tests/test_task_engine.py — Phase 1 (Task Engine skeleton) + Phase 2
(media/browser pilot) of the JARVIS execution-architecture mission.

Verifies: the deterministic capability router (no LLM call — a plain
keyword-overlap match, same shape as system_shortcuts.py's own),
Task/Step record-keeping, JARVIS-owned recovery (try a DIFFERENT domain
on an escalatable result, never the same one again), and that a
verified-success/verified-failure result is never invented — every
returned status traces back to a real (here: mocked) capability result.

Per this project's own established convention: youtube_video()/
browser_control() are ALWAYS mocked — no test here opens a real browser
or plays real audio. What's verified is that execute_task() calls the
right capability, in the right order, and reports its REAL result
honestly.

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine
"""
from unittest.mock import patch, MagicMock

import actions.task_engine as te


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(youtube=None, browser=None):
    """_HANDLERS is a dict built at module-load time, capturing each
    handler FUNCTION OBJECT — the same "the dict holds the reference,
    patching the bare module attribute doesn't reach it" pitfall this
    project's own computer_settings.py (ACTION_MAP) and
    system_shortcuts.py (_PSUTIL_HANDLERS) tests already had to work
    around. patch.dict on the dict ENTRIES, not patch.object on the bare
    _run_youtube/_run_browser names, is the only way to actually
    intercept execute_task()'s real dispatch."""
    overrides = {}
    if youtube is not None:
        overrides["youtube"] = youtube
    if browser is not None:
        overrides["browser"] = browser
    return patch.dict(te._HANDLERS, overrides)


# ── capability families: classification + recovery boundary ────────────
# (post Phase 2 review, before Phase 3 — see
# docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md's capability-family note)

def test_youtube_belongs_to_application_family() -> None:
    assert te.family_of("youtube") == te.FAMILY_APPLICATION
    print("test_youtube_belongs_to_application_family: PASS")

def test_browser_belongs_to_application_family() -> None:
    assert te.family_of("browser") == te.FAMILY_APPLICATION
    print("test_browser_belongs_to_application_family: PASS")

def test_family_of_returns_none_for_an_unregistered_domain() -> None:
    assert te.family_of("not_a_real_domain") is None
    print("test_family_of_returns_none_for_an_unregistered_domain: PASS")

def test_family_metadata_does_not_change_route_return_behavior() -> None:
    # route() must still return a bare domain-name string (or None) —
    # the family addition is a data-model change, not a route() contract
    # change.
    result = te.route("play a Kafle song on YouTube")
    assert result == "youtube"
    assert isinstance(result, str)
    print("test_family_metadata_does_not_change_route_return_behavior: PASS")

def test_real_recovery_chain_entries_are_all_same_family() -> None:
    # Structural safety net over the REAL (non-faked) _RECOVERY_CHAIN —
    # catches a future accidental cross-family entry immediately, not
    # just when a task happens to exercise it.
    for src, dst in te._RECOVERY_CHAIN.items():
        assert te.family_of(src) == te.family_of(dst), f"{src} -> {dst} crosses a family boundary"
    print("test_real_recovery_chain_entries_are_all_same_family: PASS")

def test_recovery_cannot_cross_families() -> None:
    # Deliberately fabricates a cross-family recovery entry (youtube, a
    # real application-family domain, pointed at a fake SYSTEM-family
    # domain) to prove execute_task() refuses the hop — not just that
    # the real, current data happens to be same-family. This is the
    # actual enforcement test; the one above is the regression net over
    # real data.
    fake_domains = te._DOMAINS + [{
        "name": "fake_system_domain", "family": te.FAMILY_SYSTEM, "keywords": ["volume"],
    }]
    fake_chain = dict(te._RECOVERY_CHAIN)
    fake_chain["youtube"] = "fake_system_domain"
    m_system_handler = MagicMock(return_value="[VERIFIED_SUCCESS] should never be reached")
    m_youtube = MagicMock(return_value="[INCONCLUSIVE] unsure on youtube.")
    with patch.object(te, "_DOMAINS", fake_domains), \
         patch.object(te, "_RECOVERY_CHAIN", fake_chain), \
         patch.dict(te._HANDLERS, {"youtube": m_youtube, "fake_system_domain": m_system_handler}):
        result = _task(objective="play a Kafle song on YouTube")
    m_system_handler.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_recovery_cannot_cross_families: PASS")


# ── router: deterministic, no LLM ───────────────────────────────────────

def test_route_matches_youtube_domain() -> None:
    assert te.route("play a Kafle song on YouTube") == "youtube"
    print("test_route_matches_youtube_domain: PASS")

def test_route_matches_browser_domain() -> None:
    assert te.route("open google.com and search for restaurants") == "browser"
    print("test_route_matches_browser_domain: PASS")

def test_route_returns_none_for_an_unmatched_objective_rather_than_guessing() -> None:
    assert te.route("what is the capital of France") is None
    assert te.route("") is None
    print("test_route_returns_none_for_an_unmatched_objective_rather_than_guessing: PASS")


# ── status_of: bracket-tag extraction ───────────────────────────────────

def test_status_of_extracts_the_bracketed_tag() -> None:
    assert te.status_of("[VERIFIED_SUCCESS] it worked.") == "VERIFIED_SUCCESS"
    assert te.status_of("plain untagged string") == ""
    assert te.status_of("") == ""
    print("test_status_of_extracts_the_bracketed_tag: PASS")


# ── _classify_browser_result: evidence-based, not a guess ──────────────

def test_classify_browser_result_recognizes_real_success_shapes() -> None:
    assert te._classify_browser_result("Opened: https://example.com") == "VERIFIED_SUCCESS"
    assert te._classify_browser_result("Clicked: 'Sign in'") == "VERIFIED_SUCCESS"
    print("test_classify_browser_result_recognizes_real_success_shapes: PASS")

def test_classify_browser_result_recognizes_real_failure_shapes() -> None:
    assert te._classify_browser_result("Could not open: no display") == "VERIFIED_FAILURE"
    assert te._classify_browser_result("Element not found (timeout).") == "VERIFIED_FAILURE"
    print("test_classify_browser_result_recognizes_real_failure_shapes: PASS")

def test_classify_browser_result_defaults_to_inconclusive_for_unknown_shapes() -> None:
    assert te._classify_browser_result("some completely novel string") == "INCONCLUSIVE"
    print("test_classify_browser_result_defaults_to_inconclusive_for_unknown_shapes: PASS")


# ── execute_task: end-to-end, mocked capabilities ───────────────────────

def test_execute_task_with_no_objective_is_inconclusive_calls_nothing() -> None:
    m_yt, m_br = MagicMock(), MagicMock()
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="")
    m_yt.assert_not_called()
    m_br.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    print("test_execute_task_with_no_objective_is_inconclusive_calls_nothing: PASS")

def test_execute_task_with_no_matching_domain_calls_nothing() -> None:
    m_yt, m_br = MagicMock(), MagicMock()
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="what's 2 plus 2")
    m_yt.assert_not_called()
    m_br.assert_not_called()
    assert result.startswith("[INCONCLUSIVE]")
    assert "no known JARVIS capability" in result
    print("test_execute_task_with_no_matching_domain_calls_nothing: PASS")

def test_execute_task_routes_a_youtube_objective_and_reports_real_success() -> None:
    m_yt = MagicMock(return_value="[VERIFIED_SUCCESS] Playing: Kafle song.")
    m_br = MagicMock()
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play a Kafle song on YouTube")
    m_yt.assert_called_once()
    m_br.assert_not_called()  # no recovery needed on success
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "Kafle" in result
    print("test_execute_task_routes_a_youtube_objective_and_reports_real_success: PASS")

def test_execute_task_recovers_to_browser_when_youtube_is_inconclusive() -> None:
    m_yt = MagicMock(return_value="[INCONCLUSIVE] opened a search page, manual selection required.")
    m_br = MagicMock(return_value="[VERIFIED_SUCCESS] Opened: https://youtube.com/results?...")
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play some obscure Kafle remix on YouTube")
    m_br.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_execute_task_recovers_to_browser_when_youtube_is_inconclusive: PASS")

def test_execute_task_never_retries_the_same_domain_twice() -> None:
    call_count = {"youtube": 0}
    def fake_youtube(objective, confirmed=False, context=None):
        call_count["youtube"] += 1
        return "[INCONCLUSIVE] still not sure."
    m_br = MagicMock(return_value="[INCONCLUSIVE] browser also unsure.")
    with _handlers(youtube=fake_youtube, browser=m_br):
        _task(objective="play some Kafle video")
    assert call_count["youtube"] == 1, "youtube domain must only be attempted once, never retried blindly"
    print("test_execute_task_never_retries_the_same_domain_twice: PASS")

def test_execute_task_reports_the_real_last_result_when_recovery_is_exhausted() -> None:
    m_yt = MagicMock(return_value="[INCONCLUSIVE] unsure on youtube.")
    m_br = MagicMock(return_value="[VERIFIED_FAILURE] Could not open: no browser found.")
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play some Kafle video")
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "Could not open" in result
    print("test_execute_task_reports_the_real_last_result_when_recovery_is_exhausted: PASS")

def test_execute_task_recovery_chain_is_bounded_to_one_hop_even_after_a_known_failure() -> None:
    # VERIFIED_FAILURE is a real, known outcome — this project's own rule
    # says an already-known failure isn't what triggers a "try a
    # different method" escalation the same way INCONCLUSIVE/UI_AMBIGUOUS
    # does; confirm the recovery chain is still exactly one hop bounded,
    # not an unbounded bounce between domains.
    m_yt = MagicMock(return_value="[VERIFIED_FAILURE] no video found.")
    m_br = MagicMock(return_value="[VERIFIED_FAILURE] nothing found either.")
    with _handlers(youtube=m_yt, browser=m_br):
        result = _task(objective="play some Kafle video")
    assert m_br.call_count <= 1
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_execute_task_recovery_chain_is_bounded_to_one_hop_even_after_a_known_failure: PASS")

def test_execute_task_records_real_step_history() -> None:
    with _handlers(youtube=MagicMock(return_value="[VERIFIED_SUCCESS] Playing: X.")):
        te.execute_task(parameters={"objective": "play X on YouTube"})
    # Exercise Task/Step directly too, not only through execute_task's
    # return string — confirms the record-keeping itself works.
    task = te.Task("play X on YouTube")
    step = task.record("youtube", "[VERIFIED_SUCCESS] Playing: X.", __import__("time").monotonic())
    assert step.status == "VERIFIED_SUCCESS"
    assert task.steps == [step]
    print("test_execute_task_records_real_step_history: PASS")


if __name__ == "__main__":
    test_youtube_belongs_to_application_family()
    test_browser_belongs_to_application_family()
    test_family_of_returns_none_for_an_unregistered_domain()
    test_family_metadata_does_not_change_route_return_behavior()
    test_real_recovery_chain_entries_are_all_same_family()
    test_recovery_cannot_cross_families()
    test_route_matches_youtube_domain()
    test_route_matches_browser_domain()
    test_route_returns_none_for_an_unmatched_objective_rather_than_guessing()
    test_status_of_extracts_the_bracketed_tag()
    test_classify_browser_result_recognizes_real_success_shapes()
    test_classify_browser_result_recognizes_real_failure_shapes()
    test_classify_browser_result_defaults_to_inconclusive_for_unknown_shapes()
    test_execute_task_with_no_objective_is_inconclusive_calls_nothing()
    test_execute_task_with_no_matching_domain_calls_nothing()
    test_execute_task_routes_a_youtube_objective_and_reports_real_success()
    test_execute_task_recovers_to_browser_when_youtube_is_inconclusive()
    test_execute_task_never_retries_the_same_domain_twice()
    test_execute_task_reports_the_real_last_result_when_recovery_is_exhausted()
    test_execute_task_recovery_chain_is_bounded_to_one_hop_even_after_a_known_failure()
    test_execute_task_records_real_step_history()
    print("\nAll task_engine tests passed.")
