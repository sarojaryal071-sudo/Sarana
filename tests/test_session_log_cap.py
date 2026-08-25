"""
tests/test_session_log_cap.py — resource-cleanup regression test (item 1):
self._session_log must never grow past SESSION_LOG_MAX_ENTRIES, and the
existing readers of its tail (_save_session_summary()'s log[-40:],
proactive mode's self._session_log[-8:]) must keep seeing exactly the
same *recent* entries as before the cap existed.

Run with:
    .venv/Scripts/python.exe -m tests.test_session_log_cap
"""
from core.headless_surface import HeadlessSurface
from main import JarvisLive, SESSION_LOG_MAX_ENTRIES


def test_cap_constant_is_at_least_40() -> None:
    """_save_session_summary() reads log[-40:] — the cap must never be
    smaller than that or it would silently start truncating what that
    function has always been able to see."""
    assert SESSION_LOG_MAX_ENTRIES >= 40, (
        "cap must stay >= 40 to preserve _save_session_summary()'s existing log[-40:] behavior"
    )
    print("test_cap_constant_is_at_least_40: PASS")


def test_session_log_never_exceeds_the_cap() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    for i in range(SESSION_LOG_MAX_ENTRIES * 4):
        jarvis._append_session_log(f"entry {i}")
    assert len(jarvis._session_log) == SESSION_LOG_MAX_ENTRIES
    print("test_session_log_never_exceeds_the_cap: PASS")


def test_session_log_keeps_the_most_recent_entries() -> None:
    """Trimming must drop the OLDEST entries, never the newest — anything
    reading a recent tail must see genuinely recent data."""
    jarvis = JarvisLive(HeadlessSurface())
    total = SESSION_LOG_MAX_ENTRIES + 25
    for i in range(total):
        jarvis._append_session_log(f"entry {i}")
    assert jarvis._session_log[-1] == f"entry {total - 1}"
    assert jarvis._session_log[0] == f"entry {total - SESSION_LOG_MAX_ENTRIES}"
    print("test_session_log_keeps_the_most_recent_entries: PASS")


def test_below_cap_behaves_exactly_as_before() -> None:
    """A normal short conversation (well under the cap) must be completely
    unaffected — no trimming, no reordering."""
    jarvis = JarvisLive(HeadlessSurface())
    jarvis._append_session_log("User: hello")
    jarvis._append_session_log("SARANA: hi there")
    jarvis._append_session_log("User: how are you")
    assert jarvis._session_log == ["User: hello", "SARANA: hi there", "User: how are you"]
    print("test_below_cap_behaves_exactly_as_before: PASS")


def test_proactive_mode_tail_read_unaffected_by_cap() -> None:
    """self._session_log[-8:] (proactive mode's own read, main.py) must
    still return exactly the last 8 entries once the log is at/over cap."""
    jarvis = JarvisLive(HeadlessSurface())
    for i in range(SESSION_LOG_MAX_ENTRIES + 100):
        jarvis._append_session_log(f"entry {i}")
    tail = jarvis._session_log[-8:]
    assert len(tail) == 8
    assert tail[-1] == f"entry {SESSION_LOG_MAX_ENTRIES + 99}"
    print("test_proactive_mode_tail_read_unaffected_by_cap: PASS")


def test_save_session_summary_tail_slice_unaffected_by_cap() -> None:
    """_save_session_summary()'s log[-40:] must still return exactly 40
    genuinely-recent entries once the log is well past the cap — proving
    the cap (>= 40) never clips what that function has always relied on."""
    jarvis = JarvisLive(HeadlessSurface())
    total = SESSION_LOG_MAX_ENTRIES + 200
    for i in range(total):
        jarvis._append_session_log(f"entry {i}")
    tail40 = jarvis._session_log[-40:]
    assert len(tail40) == 40
    assert tail40[-1] == f"entry {total - 1}"
    assert tail40[0] == f"entry {total - 40}"
    print("test_save_session_summary_tail_slice_unaffected_by_cap: PASS")


if __name__ == "__main__":
    test_cap_constant_is_at_least_40()
    test_session_log_never_exceeds_the_cap()
    test_session_log_keeps_the_most_recent_entries()
    test_below_cap_behaves_exactly_as_before()
    test_proactive_mode_tail_read_unaffected_by_cap()
    test_save_session_summary_tail_slice_unaffected_by_cap()
    print("\nAll session-log cap tests passed.")
