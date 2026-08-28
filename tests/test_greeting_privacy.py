"""
tests/test_greeting_privacy.py -- startup-greeting privacy regression
tests: the unsolicited login/reconnect greeting (_send_startup_briefing())
must never reference, summarize, or hint at previous-session content or
sensitive long-term memory facts -- it fires with no user request and may
be heard by anyone in the room, not just the logged-in user.

Root cause fixed: session_clause used to hand pop_last_session()'s raw
previous-session summary straight to Gemini with "you may briefly and
naturally mention that {when}: {summary}" -- live-reproduced as SARANA
saying things like "We were talking about your wife...". The fix keeps
calling pop_last_session() (same memory-retrieval/consume-on-read
behavior as before) but never builds its result into the prompt text.

Run with:
    .venv/Scripts/python.exe -m tests.test_greeting_privacy
"""
import asyncio
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
from main import JarvisLive

# A deliberately sensitive fixture -- the exact kind of content that must
# never surface in an unsolicited greeting.
_SENSITIVE_SUMMARY = {
    "date": "2026-08-27",
    "summary": "Saroj's wife is angry with him and they had an argument.",
}
_SENSITIVE_SUBSTRINGS = [
    "wife", "angry", "argument", "Saroj's wife",
]
_FORBIDDEN_PHRASES = [
    "we were talking about", "last time you mentioned", "you told me about",
    "i remember you were", "we didn't save that in memory",
    "i remember our previous conversation",
]


class _FakeSession:
    def __init__(self):
        self.sent = []

    async def send_client_content(self, turns, turn_complete=True):
        self.sent.append(turns["parts"][0]["text"])


def _assert_prompt_is_clean(prompt: str) -> None:
    lowered = prompt.lower()
    for phrase in _SENSITIVE_SUBSTRINGS:
        assert phrase.lower() not in lowered, f"greeting leaked sensitive content: {phrase!r}"
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in lowered, f"greeting used a forbidden reveal phrase: {phrase!r}"


# ── 1: no previous-conversation content ────────────────────────────────

def test_greeting_never_mentions_previous_session_summary() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        jarvis._user_profile = {"username": "saroj"}
        jarvis._session_owner = "saroj"
        with patch("main.pop_last_session", return_value=_SENSITIVE_SUMMARY), \
             patch("main.load_memory", return_value={"identity": {"name": {"value": "Saroj"}}}):
            await jarvis._send_startup_briefing()
        assert len(jarvis.session.sent) == 1
        _assert_prompt_is_clean(jarvis.session.sent[0])
    asyncio.run(_run())
    print("test_greeting_never_mentions_previous_session_summary: PASS")


def test_greeting_never_mentions_previous_session_regardless_of_recency() -> None:
    """Same-day, yesterday, and many-days-ago summaries must all be
    equally suppressed -- the old "earlier today"/"yesterday"/"N days
    ago" phrasing logic is gone entirely, not just made conditional."""
    async def _run():
        for stale_date in ("2026-08-28", "2026-08-27", "2026-08-01"):
            jarvis = JarvisLive(HeadlessSurface())
            jarvis.session = _FakeSession()
            summary = {"date": stale_date, "summary": "Discussed a private medical diagnosis."}
            with patch("main.pop_last_session", return_value=summary), \
                 patch("main.load_memory", return_value={}):
                await jarvis._send_startup_briefing()
            prompt = jarvis.session.sent[0].lower()
            assert "medical" not in prompt and "diagnosis" not in prompt
            assert "earlier today" not in prompt and "yesterday" not in prompt and "days ago" not in prompt
    asyncio.run(_run())
    print("test_greeting_never_mentions_previous_session_regardless_of_recency: PASS")


def test_greeting_still_pops_last_session_preserving_consume_on_read() -> None:
    """The privacy fix must not silently change memory-retrieval
    behavior -- pop_last_session() is still called (and therefore the
    stored record is still consumed/cleared exactly as before), only its
    CONTENT is withheld from the prompt."""
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        with patch("main.pop_last_session", return_value=_SENSITIVE_SUMMARY) as mock_pop, \
             patch("main.load_memory", return_value={}):
            await jarvis._send_startup_briefing()
        mock_pop.assert_called_once_with(jarvis._session_owner)
    asyncio.run(_run())
    print("test_greeting_still_pops_last_session_preserving_consume_on_read: PASS")


# ── 2: no sensitive long-term memory facts either ──────────────────────

def test_greeting_never_includes_sensitive_memory_facts() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        # Deterministic display name -- never read the real, unmocked
        # config/api_keys.json (see _current_user_name()'s own fallback
        # chain), which would make this assertion depend on whatever
        # happens to be configured on the machine running the test.
        jarvis._web_user_name = "Saroj"
        sensitive_memory = {
            "identity": {"name": {"value": "Saroj"}},
            "relationships": {"wife": {"value": "Priya, currently upset after an argument"}},
            "notes": {"health": {"value": "recently diagnosed with a private medical condition"}},
        }
        with patch("main.pop_last_session", return_value=None), \
             patch("main.load_memory", return_value=sensitive_memory):
            await jarvis._send_startup_briefing()
        prompt = jarvis.session.sent[0].lower()
        assert "priya" not in prompt
        assert "upset" not in prompt
        assert "diagnosed" not in prompt
        assert "medical condition" not in prompt
        assert "Saroj" in jarvis.session.sent[0]   # the safe display name is still fine
    asyncio.run(_run())
    print("test_greeting_never_includes_sensitive_memory_facts: PASS")


# ── 3/4: time-of-day and language still work ───────────────────────────

def test_greeting_still_uses_correct_local_time_of_day() -> None:
    from datetime import datetime
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        fixed = datetime(2026, 6, 15, 2, 0)  # 2 AM -> late_night, unambiguous
        with patch("main.pop_last_session", return_value=None), \
             patch("main.load_memory", return_value={}), \
             patch.object(JarvisLive, "_local_now", return_value=fixed):
            await jarvis._send_startup_briefing()
        prompt = jarvis.session.sent[0]
        assert "late_night" in prompt
    asyncio.run(_run())
    print("test_greeting_still_uses_correct_local_time_of_day: PASS")


def test_greeting_still_respects_effective_language() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.session = _FakeSession()
        with patch("main.pop_last_session", return_value=None), \
             patch("main.load_memory", return_value={}), \
             patch.object(JarvisLive, "_resolve_effective_language", return_value="Turkish"):
            await jarvis._send_startup_briefing()
        prompt = jarvis.session.sent[0]
        assert "Respond in Turkish." in prompt
    asyncio.run(_run())
    print("test_greeting_still_respects_effective_language: PASS")


# ── 5: explicit memory questions still work (different mechanism) ──────

def test_explicit_memory_still_available_in_build_config() -> None:
    """The privacy fix is scoped to the unsolicited greeting only --
    ordinary long-term memory facts must still be part of
    system_instruction for the whole connection, so an explicit question
    like 'what did I tell you about my wife?' still works exactly as
    before."""
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    sensitive_memory = {
        "relationships": {"wife": {"value": "Priya, they argued recently"}},
    }
    with patch("main.load_memory", return_value=sensitive_memory):
        config = jarvis._build_config()
    assert "Priya" in config.system_instruction
    print("test_explicit_memory_still_available_in_build_config: PASS")


# ── 6: a new session cannot inherit a previous session's content ──────

def test_new_session_never_inherits_previous_sessions_greeting_content() -> None:
    """Sequential sessions (e.g. Saroj's identity-switch reconnect, or a
    fresh login) each get their own pop_last_session() call for their OWN
    owner -- but since content is never surfaced at all, there is no
    mechanism left by which one session's summary could leak into
    another's greeting."""
    async def _run():
        # Session 1: Saroj, with a sensitive summary of his own.
        jarvis1 = JarvisLive(HeadlessSurface())
        jarvis1.session = _FakeSession()
        jarvis1._user_profile = {"username": "saroj"}
        jarvis1._session_owner = "saroj"
        with patch("main.pop_last_session", return_value=_SENSITIVE_SUMMARY), \
             patch("main.load_memory", return_value={}):
            await jarvis1._send_startup_briefing()
        _assert_prompt_is_clean(jarvis1.session.sent[0])

        # Session 2: a different identity (Sana) reconnecting on the SAME
        # JarvisLive instance -- her own (different) sensitive summary
        # must also never appear, and Saroj's must never leak across.
        jarvis1._user_profile = {"username": "sana"}
        jarvis1._session_owner = "sana"
        jarvis1.session.sent = []
        sana_summary = {"date": "2026-08-27", "summary": "Sana discussed a financial dispute with her landlord."}
        with patch("main.pop_last_session", return_value=sana_summary), \
             patch("main.load_memory", return_value={}):
            await jarvis1._send_startup_briefing(identity_switch=True)
        prompt2 = jarvis1.session.sent[0].lower()
        assert "wife" not in prompt2 and "argument" not in prompt2   # Saroj's content
        assert "landlord" not in prompt2 and "financial dispute" not in prompt2   # Sana's own content too
    asyncio.run(_run())
    print("test_new_session_never_inherits_previous_sessions_greeting_content: PASS")


if __name__ == "__main__":
    test_greeting_never_mentions_previous_session_summary()
    test_greeting_never_mentions_previous_session_regardless_of_recency()
    test_greeting_still_pops_last_session_preserving_consume_on_read()
    test_greeting_never_includes_sensitive_memory_facts()
    test_greeting_still_uses_correct_local_time_of_day()
    test_greeting_still_respects_effective_language()
    test_explicit_memory_still_available_in_build_config()
    test_new_session_never_inherits_previous_sessions_greeting_content()
    print("\nAll greeting-privacy tests passed.")
