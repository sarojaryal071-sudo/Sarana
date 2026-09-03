"""
actions/task_engine.py — JARVIS's execution authority.

Architecture (agreed before this module was written): Gemini interprets
and clarifies the user's request into a plain-language OBJECTIVE and
hands it to JARVIS via the jarvis_task tool; from that point on, JARVIS —
this module — owns the task until verified completion, failure, a
recovery-exhausted report, or a safety block. Gemini does not choose
which capability/tool is used; that decision is made HERE, by a small,
deterministic, explicit router — the same keyword-scored-registry
pattern already proven in actions/system_shortcuts.py's alias matcher —
never a second LLM call deciding what to do (see this module's own
_score_domain(), a direct sibling of system_shortcuts.py's `_score`).

This is orchestration logic OVER the existing execution infrastructure,
not a second one:
  - the real work is done by calling EXISTING action-module functions
    directly, as ordinary in-process Python calls (youtube_video(),
    browser_control()) — not a second tool-execution queue. main.py's
    _tool_call_queue/_handle_tool_batch still owns receiving the ONE
    jarvis_task call from Gemini; everything in this module happens
    inside that single call.
  - verification/status reuses result_envelope.py's existing vocabulary
    exclusively (VERIFIED_SUCCESS/VERIFIED_FAILURE/INCONCLUSIVE/
    UI_AMBIGUOUS/CONFIRMATION_REQUIRED/BLOCKED/CANCELLED) — no second
    status system.
  - recovery is bounded and logged as real Step records, never a blind
    same-method retry (this project's own hard-learned rule, from a real
    live bug: an auto-retry double-clicked Calculator and silently
    computed the wrong answer).

Phase 1+2 scope (see docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md and the
"Full Execution Architecture Implementation Mission" this module was
built under): the Task/Step skeleton, the deterministic router, and ONE
real pilot capability domain — media/browser, reusing
actions/youtube_video.py and actions/browser_control.py exactly as they
already exist. Later phases extend _DOMAINS; this module's own shape
does not need to change to add a new domain, only a new registry entry
and (if the domain's underlying capability isn't already Result-
Envelope-aware) a small classifier function alongside the existing ones
below.

Explicitly NOT this module's job, and never added here: a second AI/LLM
choosing what to do: a second tool-execution queue: a second
verification vocabulary; browser/UI-automation implementation itself
(that stays owned by browser_control.py/computer_control.py); permanent
personal memory (a Task's steps are runtime-only and are never written
to memory/* — see Task.__init__'s own note).
"""
import time
import uuid

from actions import result_envelope as _envelope
from actions.youtube_video import youtube_video
from actions.browser_control import browser_control

# Bounded per-task step budget. Deliberately a small, LOCAL constant for
# this pilot scope (one primary attempt + one recovery attempt) rather
# than importing main.py's _JARVIS_MAX_ACTIONS_PER_TURN — task_engine.py
# must not import main.py (main.py imports THIS module; the reverse
# would be circular). Unifying the two governors into one shared bound
# is real follow-up work once the Task Engine covers more than one
# domain, not done here — see docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md.
_MAX_STEPS_PER_TASK = 3

# ── Task state ───────────────────────────────────────────────────────

TASK_RECEIVED   = "RECEIVED"
TASK_EXECUTING  = "EXECUTING"
TASK_RECOVERING = "RECOVERING"
TASK_COMPLETED  = "COMPLETED"
TASK_FAILED     = "FAILED"
TASK_BLOCKED    = "BLOCKED"
TASK_CANCELLED  = "CANCELLED"


def status_of(envelope_str: str) -> str:
    """Extracts the bracketed [STATUS] tag from a Result Envelope string
    ('[VERIFIED_SUCCESS] ...' -> 'VERIFIED_SUCCESS'). Returns '' for a
    plain, untagged string — some existing capabilities don't return an
    enveloped status for every path yet; callers must never assume '' means
    success, only that it needs the domain-specific classifier below."""
    s = (envelope_str or "").strip()
    if s.startswith("[") and "]" in s:
        return s[1:s.index("]")]
    return ""


class Step:
    """One attempted action within a Task — deliberately small fields
    only (matches the approved architecture's own 'keep the model small'
    rule): which domain was tried, the raw result string, and timing."""
    __slots__ = ("domain", "result", "started_at", "elapsed_s")

    def __init__(self, domain: str, result: str, started_at: float):
        self.domain = domain
        self.result = result
        self.started_at = started_at
        self.elapsed_s = time.monotonic() - started_at

    @property
    def status(self) -> str:
        return status_of(self.result)


class Task:
    """Runtime-only. A Task and its Steps live for the lifetime of one
    jarvis_task call and are never persisted — this is deliberate:
    execution history is NOT personal memory (see memory/*'s own,
    separate role) and must not silently become it. If a completed
    task's outcome is ever worth remembering long-term, that is a
    conscious save_memory call elsewhere, not an automatic side effect
    of this class."""

    def __init__(self, objective: str, context: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.objective = (objective or "").strip()
        self.context = (context or "").strip()
        self.state = TASK_RECEIVED
        self.steps: list[Step] = []
        self.created_at = time.monotonic()

    def record(self, domain: str, result: str, started_at: float) -> Step:
        step = Step(domain, result, started_at)
        self.steps.append(step)
        return step


# ── Capability router (deterministic, no LLM) ───────────────────────────
# Same scoring shape as system_shortcuts.py's _score()/resolve() — a
# small, explicit, auditable keyword-overlap match, picking the
# NARROWEST confident domain first. Order matters: youtube is checked
# before the more general browser domain so "play X on youtube" doesn't
# get swallowed by generic "open a website" phrasing.

_DOMAINS = [
    {
        "name": "youtube",
        "keywords": ["youtube", "video", "song", "music", "watch", "play"],
    },
    {
        "name": "browser",
        "keywords": ["website", "site", "browser", "open", "search", "google",
                     "url", "webpage", "page", "navigate", "chrome", "firefox", "edge"],
    },
]


def _normalize(text: str) -> set:
    import re
    return set(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def _score_domain(objective_words: set, domain: dict) -> int:
    return len(objective_words & set(domain["keywords"]))


def route(objective: str) -> str | None:
    """Returns the best-scoring domain name, or None if nothing clears a
    minimal confidence bar — deliberately refuses to guess at a weak
    match, same 'don't guess, say so' principle already used throughout
    this codebase (system_shortcuts.resolve(), UI_AMBIGUOUS)."""
    words = _normalize(objective)
    if not words:
        return None
    best_name, best_score = None, 0
    for domain in _DOMAINS:
        score = _score_domain(words, domain)
        if score > best_score:
            best_name, best_score = domain["name"], score
    return best_name if best_score >= 1 else None


# ── Domain result classifiers ────────────────────────────────────────
# Only needed for capabilities that don't already return a Result-
# Envelope-tagged string for every path. youtube_video()'s play action
# now does (fixed as part of this same mission — see youtube_video.py's
# _handle_play), so it needs no classifier; browser_control()'s plain
# open/search actions still return bare strings for some paths, so this
# reads their REAL, evidence-based shapes (grepped from browser_control.py
# directly, not guessed) rather than assuming "no exception = success".

def _classify_browser_result(result: str) -> str:
    tag = status_of(result)
    if tag:
        return tag
    r = (result or "")
    low = r.lower()
    if r.startswith("Opened") or r.startswith("Clicked") or r.startswith("Typed") or "typed" in low:
        return _envelope.STATUS_VERIFIED_SUCCESS
    if "could not" in low or "error" in low or "timed out" in low or "not found" in low:
        return _envelope.STATUS_VERIFIED_FAILURE
    return _envelope.STATUS_INCONCLUSIVE


# ── Domain handlers — call the EXISTING capability, in-process ─────────

def _run_youtube(objective: str) -> str:
    return youtube_video(parameters={"action": "play", "query": objective}, player=None)


def _run_browser(objective: str) -> str:
    result = browser_control(parameters={"action": "search", "query": objective})
    tag = _classify_browser_result(result)
    if status_of(result):
        return result
    return _envelope.envelope(tag, result)


_HANDLERS = {
    "youtube": _run_youtube,
    "browser": _run_browser,
}

# Bounded, ordered recovery chain: if the PRIMARY domain's result is
# escalatable (INCONCLUSIVE/UI_AMBIGUOUS — never a known VERIFIED_FAILURE,
# which is a real, already-known outcome, not something retrying a
# DIFFERENT method would fix), try the next domain down — never the SAME
# domain again. Matches this project's own no-blind-retry rule.
_RECOVERY_CHAIN = {
    "youtube": "browser",
}


def execute_task(parameters: dict = None) -> str:
    """The jarvis_task entry point (see main.py's dispatch). parameters:
    objective (str, required), context (str, optional), confirmed (bool,
    optional — reserved for a future domain that needs it; the current
    pilot domains are both SAFE-tier and never require it)."""
    params = parameters or {}
    objective = (params.get("objective") or "").strip()
    context = (params.get("context") or "").strip()

    if not objective:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no objective was given")

    task = Task(objective, context)
    task.state = TASK_EXECUTING

    domain = route(objective)
    if domain is None:
        task.state = TASK_FAILED
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            f"no known JARVIS capability confidently matches '{objective}' yet",
        )

    tried = []
    while domain and len(task.steps) < _MAX_STEPS_PER_TASK:
        tried.append(domain)
        handler = _HANDLERS[domain]
        started_at = time.monotonic()
        result = handler(objective)
        step = task.record(domain, result, started_at)

        if step.status == _envelope.STATUS_VERIFIED_SUCCESS:
            task.state = TASK_COMPLETED
            return result

        if step.status == _envelope.STATUS_VERIFIED_FAILURE:
            # A known, real outcome — trying a DIFFERENT domain is still
            # allowed (see _RECOVERY_CHAIN), but never retry THIS domain.
            pass

        next_domain = _RECOVERY_CHAIN.get(domain)
        if next_domain and next_domain not in tried:
            task.state = TASK_RECOVERING
            domain = next_domain
            continue
        break

    # Every available domain in the chain was tried (or the step budget
    # was hit) without a VERIFIED_SUCCESS — report the LAST real result
    # honestly rather than inventing a generic failure message, so the
    # actual evidence (e.g. "Could not open: ...") reaches Gemini/the user.
    task.state = TASK_FAILED
    last = task.steps[-1]
    if last.status:
        return last.result
    return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"tried {', '.join(tried)}, neither confirmed success")
