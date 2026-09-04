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

Capability-family correction (post Phase 2 review, before Phase 3):
every _DOMAINS entry now also carries a `family` — a classification and
RECOVERY boundary, never a task-sequencing boundary (see family_of()'s
own docstring below for the distinction). This is a data-model addition
only; the router, the lifecycle, and Phase 0-2 behavior are unchanged.

Phase 5A (multi-objective sequencing, structural foundation): Gemini may
decompose a compound request into an ordered `objectives` list — still
plain language, still never a domain/tool name, exactly the same
contract as the single `objective` string it already sends. JARVIS does
NOT trust that list as an executable plan: build_plan() independently
routes every objective (the EXISTING route(), unchanged) before
executing any of them, and the resulting list of PlanStep records —
JARVIS's own artifact — is what actually executes. A single-objective
call continues to behave byte-for-byte as it always has (see
execute_task()'s own docstring); this is additive, not a rewrite of
Phase 0-4 behavior.

Explicitly NOT this module's job, and never added here: a second AI/LLM
choosing what to do: a second tool-execution queue: a second
verification vocabulary; browser/UI-automation implementation itself
(that stays owned by browser_control.py/computer_control.py); permanent
personal memory (a Task's steps are runtime-only and are never written
to memory/* — see Task.__init__'s own note).
"""
import re
import time
import uuid

from actions import result_envelope as _envelope
from actions.youtube_video import youtube_video
from actions.browser_control import browser_control
from actions.computer_settings import computer_settings
from actions import system_shortcuts
from actions.office_control import office_control

# Bounded per-task step budget. Deliberately a small, LOCAL constant for
# this pilot scope (one primary attempt + one recovery attempt) rather
# than importing main.py's _JARVIS_MAX_ACTIONS_PER_TURN — task_engine.py
# must not import main.py (main.py imports THIS module; the reverse
# would be circular). Unifying the two governors into one shared bound
# is real follow-up work once the Task Engine covers more than one
# domain, not done here — see docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md.
_MAX_STEPS_PER_TASK = 3

# Bounded PLAN length (Phase 5A) — a SEPARATE cap from _MAX_STEPS_PER_TASK
# above: that one bounds attempts (primary + recovery hops) WITHIN one
# objective; this one bounds how many objectives a single jarvis_task
# call may contain at all. Deliberately small — this is a guard against a
# malformed/runaway objectives list, not a workflow-engine capacity
# limit; today's real compound objectives (see Phase 5B) need 2-3.
_MAX_OBJECTIVES_PER_TASK = 4

# ── Task state ───────────────────────────────────────────────────────

TASK_RECEIVED              = "RECEIVED"
TASK_EXECUTING             = "EXECUTING"
TASK_RECOVERING            = "RECOVERING"
TASK_COMPLETED             = "COMPLETED"
TASK_FAILED                = "FAILED"
TASK_BLOCKED               = "BLOCKED"
TASK_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
TASK_CANCELLED             = "CANCELLED"


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
    rule): which domain was tried, the raw result string, timing, and
    (Phase 5A) which PlanStep this attempt belongs to. task.steps is
    shared across every PlanStep in a multi-objective Task — plan_index
    is what lets a Step be traced back to the specific objective it was
    trying to satisfy."""
    __slots__ = ("domain", "result", "started_at", "elapsed_s", "plan_index")

    def __init__(self, domain: str, result: str, started_at: float, plan_index: int = 0):
        self.domain = domain
        self.result = result
        self.started_at = started_at
        self.elapsed_s = time.monotonic() - started_at
        self.plan_index = plan_index

    @property
    def status(self) -> str:
        return status_of(self.result)


class PlanStep:
    """JARVIS's OWN executable-plan record for one incoming objective —
    built exclusively by build_plan() via the EXISTING route(); Gemini
    never supplies `domain` directly (see build_plan()'s own docstring).
    Deliberately tiny — not a generic workflow-step type, just enough to
    remember what was asked, what JARVIS decided to do about it, and how
    that attempt ultimately turned out."""
    __slots__ = ("objective", "domain", "status")

    def __init__(self, objective: str, domain: str):
        self.objective = objective
        self.domain = domain
        self.status = ""  # '' = not yet attempted; set by _execute_step()


class TaskContext:
    """Small, runtime-only, structured context carried between PlanSteps
    within ONE Task — explicitly NOT memory, NOT persisted, NOT a generic
    workflow data bus (see Task's own docstring on why execution state
    stays runtime-only).

    `values` — a SMALL set of deterministically-extracted scalars a
    LATER objective may consume (see _extract_context_values()). Flat,
    string-keyed, string-valued by convention; there is no nesting and no
    per-domain schema here on purpose — an extraction rule is added only
    when a concrete later step genuinely needs to consume it, never
    speculatively.
    `raw` — each completed PlanStep's own final evidence string, keyed by
    plan_index; an audit/fallback trail, not the primary consumption
    channel."""
    __slots__ = ("values", "raw")

    def __init__(self):
        self.values: dict[str, str] = {}
        self.raw: dict[int, str] = {}


class Task:
    """Runtime-only. A Task and its Steps live for the lifetime of one
    jarvis_task call and are never persisted — this is deliberate:
    execution history is NOT personal memory (see memory/*'s own,
    separate role) and must not silently become it. If a completed
    task's outcome is ever worth remembering long-term, that is a
    conscious save_memory call elsewhere, not an automatic side effect
    of this class.

    Phase 5A: `objective` (a single string) remains exactly what it
    always was — the legacy single-objective constructor argument, kept
    so existing direct callers (e.g. tests exercising Task/Step
    record-keeping directly) are unaffected. `objectives` is the new,
    OPTIONAL list form; when omitted, it's derived as the obvious
    one-item list from `objective`. `plan`/`task_context`/
    `current_step_index` are new, additive fields — nothing above them
    changes shape."""

    def __init__(self, objective: str = "", context: str = "", objectives: list[str] | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.objective = (objective or "").strip()
        if objectives:
            self.objectives: list[str] = [str(o).strip() for o in objectives if str(o or "").strip()]
        else:
            self.objectives = [self.objective] if self.objective else []
        self.context = (context or "").strip()
        self.task_context = TaskContext()
        self.plan: list[PlanStep] = []
        self.current_step_index = 0
        self.state = TASK_RECEIVED
        self.steps: list[Step] = []
        self.created_at = time.monotonic()

    def record(self, domain: str, result: str, started_at: float, plan_index: int = 0) -> Step:
        step = Step(domain, result, started_at, plan_index)
        self.steps.append(step)
        return step


# ── Capability families ──────────────────────────────────────────────
# A FAMILY is a classification and RECOVERY boundary — NOT a task-
# sequencing boundary. A single objective's PLAN/EXECUTE steps may
# legitimately cross families in order (e.g. system -> application ->
# files for "open Excel and save this to a specific folder") — that is
# ordinary multi-step sequencing (Phase 4+ work) and nothing here
# restricts it. What families DO bound is specifically RECOVERY (see
# execute_task()'s use of family_of() below): if one capability's
# attempt comes back escalatable, JARVIS may try a DIFFERENT METHOD
# within the SAME conceptual category, never jump to an unrelated
# category as a "recovery" — a failed volume-set falling back to a
# browser search would not be a sane recovery of anything.
#
# Only these two are actually populated by a real domain today. The
# rest of the taxonomy is a documented placeholder for where future
# capabilities go — deliberately not scaffolded into code until a real
# domain needs them:
#   SYSTEM       — universal OS-level infrastructure (audio, display,
#                   windows, shortcuts, OS settings). Not yet populated
#                   — Phase 3.
#   APPLICATION  — capabilities tied to one specific application/domain
#                   (browser, YouTube, Office, VS Code/dev, ...).
#                   youtube/browser live here today; Office joins this
#                   SAME family in Phase 4 — not System.
#   RESOURCE     — files / terminal / processes. Concept only, not built.
#   DEVELOPMENT  — repo agent / git. Concept only, not built.
#   DEPLOYMENT   — deploy + verify. Concept only, not built.

FAMILY_SYSTEM      = "system"
FAMILY_APPLICATION = "application"

# ── Capability router (deterministic, no LLM) ───────────────────────────
# Same scoring shape as system_shortcuts.py's _score()/resolve() — a
# small, explicit, auditable keyword-overlap match, picking the
# NARROWEST confident domain first. Order matters: youtube is checked
# before the more general browser domain so "play X on youtube" doesn't
# get swallowed by generic "open a website" phrasing.

_DOMAINS = [
    {
        "name": "youtube",
        "family": FAMILY_APPLICATION,
        "keywords": ["youtube", "video", "song", "music", "watch", "play"],
    },
    # office is declared BEFORE browser so that an objective like "open
    # Word" (which ties 1-1: "word" for office vs. "open" for browser)
    # resolves to office, not browser — same tie-break-by-declaration-
    # order technique already used for youtube-vs-browser above. Verified
    # empirically (see tests/test_task_engine_office.py's own collision
    # tests), not assumed.
    {
        "name": "office",
        "family": FAMILY_APPLICATION,
        # Evidence-based: office_control.py's own real app names
        # (word/excel) and its actual supported action/field vocabulary
        # (insert/replace/format/bold/italic/underline/save, cell,
        # spreadsheet/workbook/worksheet, document/paragraph).
        # Deliberately EXCLUDES generic verbs (open/search/page/file/
        # write/edit/create) that would collide with browser's keyword
        # set — "word"/"excel"/"cell" identify actual Office intent, a
        # bare "open"/"create" does not. Deliberately excludes
        # "powerpoint" too: office_control.py has no PowerPoint support
        # today (Word/Excel only) — see docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md's
        # Phase 4 note; adding the keyword would route confidently to a
        # capability that doesn't exist.
        "keywords": ["word", "excel", "spreadsheet", "workbook", "worksheet",
                     "cell", "document", "paragraph", "insert", "replace",
                     "bold", "italic", "underline", "formatting", "save"],
    },
    {
        "name": "browser",
        "family": FAMILY_APPLICATION,
        "keywords": ["website", "site", "browser", "open", "search", "google",
                     "url", "webpage", "page", "navigate", "chrome", "firefox", "edge"],
    },
    # ── SYSTEM family (Phase 3) ─────────────────────────────────────────
    # Three domains, not one giant "system" bucket and not one domain per
    # ACTION_MAP entry either — the smallest sensible split given what's
    # actually being integrated: a single verified numeric setting
    # (volume), a small set of consequential power actions (grouped
    # because they share ONE safety story — the existing
    # is_consequential() gate — not because they're keyword-similar), and
    # the entire system_shortcuts.py registry (40 Settings panes + 11
    # read-only queries) as ONE domain, since that module already owns
    # its own deterministic resolver — task_engine does not re-implement
    # per-shortcut keyword matching, it reuses system_shortcuts.resolve()
    # wholesale via system_shortcut()'s handler below.
    {
        "name": "system_volume",
        "family": FAMILY_SYSTEM,
        "keywords": ["volume", "loud", "louder", "quieter", "quiet"],
    },
    {
        "name": "system_power",
        "family": FAMILY_SYSTEM,
        "keywords": ["sleep", "suspend", "hibernate", "restart", "reboot", "shutdown", "shut"],
    },
    {
        "name": "system_shortcut",
        "family": FAMILY_SYSTEM,
        # Evidence-based: real words drawn from config/system_shortcuts.json's
        # own pane/query names and aliases — deliberately EXCLUDES "volume"
        # (owned by system_volume above) even though the registry's own
        # "Volume mixer" pane alias contains it, so "set my volume to 40%"
        # never ties with a Settings-page match.
        "keywords": ["bluetooth", "wifi", "network", "battery", "disk", "storage",
                     "printer", "printers", "firewall", "processes", "cpu",
                     "installed", "display", "sound", "brightness", "night",
                     "airplane", "update", "security", "startup", "settings",
                     "check", "status", "ip", "address"],
    },
]


def _normalize(text: str) -> set:
    return set(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def _score_domain(objective_words: set, domain: dict) -> int:
    return len(objective_words & set(domain["keywords"]))


def route(objective: str) -> str | None:
    """Returns the best-scoring domain name, or None if nothing clears a
    minimal confidence bar — deliberately refuses to guess at a weak
    match, same 'don't guess, say so' principle already used throughout
    this codebase (system_shortcuts.resolve(), UI_AMBIGUOUS). Return
    shape is unchanged by the family addition — still a bare domain-name
    string (or None), exactly as before."""
    words = _normalize(objective)
    if not words:
        return None
    best_name, best_score = None, 0
    for domain in _DOMAINS:
        score = _score_domain(words, domain)
        if score > best_score:
            best_name, best_score = domain["name"], score
    return best_name if best_score >= 1 else None


def family_of(domain_name: str) -> str | None:
    """The family a registered domain belongs to, or None if the domain
    isn't registered. Used ONLY to bound RECOVERY (see execute_task()) —
    never consulted by route() or by ordinary task sequencing, which may
    cross families freely."""
    for domain in _DOMAINS:
        if domain["name"] == domain_name:
            return domain.get("family")
    return None


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


def _classify_system_shortcut_result(result: str) -> str:
    """system_shortcuts.system_shortcut()'s query paths already return a
    Result-Envelope-tagged string (status_of() below catches those). Its
    pane-open paths are deliberately PLAIN strings, by that module's own
    design — there is no real ground truth to confirm a Settings page is
    now actually visible, so it never claimed VERIFIED_SUCCESS in the
    first place (see system_shortcuts.py's own open_pane() docstring).
    Task Engine preserves that honesty rather than upgrading a bare
    'Opened X settings.' into a fabricated success claim: INCONCLUSIVE is
    the accurate classification — attempted, not independently verified."""
    tag = status_of(result)
    if tag:
        return tag
    r = result or ""
    if r.startswith("Could not open"):
        return _envelope.STATUS_VERIFIED_FAILURE
    return _envelope.STATUS_INCONCLUSIVE  # "Opened ... settings." or "No known fast-path ..."


def _classify_office_result(result: str) -> str:
    """office_control.py's word_*/excel_* functions already return a
    Result-Envelope-tagged string for every real path (status_of() below
    catches those). Its top-level dispatcher's own 'Unknown Word
    action...'/'Unknown office app...' fallback strings are plain, by
    that module's own design — _run_office() below only ever emits
    (app, action) pairs office_control() already supports, so these
    should never actually fire, but are classified defensively rather
    than assumed unreachable (same discipline as the browser/system_shortcut
    classifiers above)."""
    tag = status_of(result)
    if tag:
        return tag
    r = result or ""
    if r.startswith("Unknown"):
        return _envelope.STATUS_VERIFIED_FAILURE
    return _envelope.STATUS_INCONCLUSIVE


# ── Domain handlers — call the EXISTING capability, in-process ─────────
# Uniform (objective, confirmed, context) signature across every handler
# — confirmed was added this way in Phase 3 (unused by SAFE-tier
# youtube/browser/system_volume/system_shortcut), and Phase 5A extends
# the SAME pattern for `context` (a TaskContext, or None for a
# single-objective task/a direct unit-test call) so execute_task()'s
# call site never needs to special-case which domains care about either
# parameter. Most handlers below simply ignore context — that's the
# expected, default case, not a gap; _run_office is the one Phase 5A
# opt-in consumer (see its own docstring).

def _run_youtube(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    return youtube_video(parameters={"action": "play", "query": objective}, player=None)


def _run_browser(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    result = browser_control(parameters={"action": "search", "query": objective})
    tag = _classify_browser_result(result)
    if status_of(result):
        return result
    return _envelope.envelope(tag, result)


def _run_system_volume(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    """Sets the MASTER volume to a percentage parsed out of the
    objective (JARVIS's own deterministic extraction — never a second
    LLM call). Defaults to 50% only if the objective genuinely contains
    no number, matching computer_settings.py's own existing default."""
    match = re.search(r"(\d{1,3})\s*%?", objective)
    value = match.group(1) if match else "50"
    return computer_settings(parameters={"action": "volume_set", "value": value})


def _run_system_power(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    """Routes to sleep/restart/shutdown based on the objective's own
    wording. confirmed is threaded straight through to
    computer_settings()'s EXISTING is_consequential()/is_confirmed()
    gate — restart/shutdown still require it; sleep still doesn't
    (unchanged, see result_envelope.py's own note on why sleep isn't
    classified as consequential). Task Engine does not reimplement or
    relax that gate — it reuses it exactly as-is."""
    low = objective.lower()
    if "restart" in low or "reboot" in low:
        action = "restart"
    elif "shutdown" in low or "shut" in low:
        action = "shutdown"
    else:
        action = "sleep"
    return computer_settings(parameters={"action": action, "confirmed": confirmed})


def _run_system_shortcut(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    """Deliberately does NOT re-implement pane/query matching — hands
    the objective straight to system_shortcuts.py's OWN deterministic
    resolver, reusing its entire 40-pane/11-query registry as one Task
    Engine domain instead of enumerating each shortcut as its own
    _DOMAINS entry (see the 'smallest sensible domains, don't
    over-fragment' note above _DOMAINS)."""
    result = system_shortcuts.system_shortcut(objective)
    tag = _classify_system_shortcut_result(result)
    if status_of(result):
        return result
    return _envelope.envelope(tag, result)


_OFFICE_EXCEL_WORDS = {"excel", "spreadsheet", "workbook", "worksheet", "cell"}
_OFFICE_WORD_WORDS  = {"word", "document", "paragraph"}
_CELL_REF_RE        = re.compile(r"\b([A-Za-z]{1,2}[0-9]{1,3})\b")


def _office_app(words: set) -> str | None:
    if words & _OFFICE_EXCEL_WORDS:
        return "excel"
    if words & _OFFICE_WORD_WORDS:
        return "word"
    return None


_REFERENTIAL_WORDS = {"that", "it"}


def _parse_office_action(objective: str, context: "TaskContext | None" = None) -> dict | None:
    """Deterministic (objective -> office_control() parameters) parsing —
    JARVIS's own extraction, never a second LLM call, same technique as
    _run_system_volume's numeric extraction. Word's three content actions
    (replace/format/insert) are Word-only in office_control.py, so the
    ACTION ITSELF determines the app for those; Excel's two actions need
    a cell reference to determine both the app and the target. Returns
    None when nothing can be confidently determined — most commonly a
    bare 'open Word'/'open Excel' with no content instruction, since
    office_control.py has no generic 'just open the app' action; a
    caller must never guess one into existence.

    Phase 5A: an Excel set_cell objective that has a cell reference but
    NO explicit value in its own text (e.g. "put THAT in cell A1", the
    prior PlanStep having verified the actual number) falls back to
    TaskContext.values — but ONLY when the objective itself contains a
    referential word ("that"/"it"). Never silently substitutes a context
    value the objective's own text didn't actually ask for; a step with
    a literal number in it always uses that literal number, context or
    not."""
    low = objective.lower()
    words = _normalize(objective)

    m = re.search(r"\breplace\b\s+(.+?)\s+\bwith\b\s+(.+)", low)
    if m:
        return {
            "app": "word", "action": "replace_text",
            "find": m.group(1).strip(" '\""), "replace": m.group(2).strip(" '\""),
        }

    if any(w in words for w in ("bold", "italic", "underline")):
        params = {"app": "word", "action": "format_selection"}
        if "bold" in words:
            params["bold"] = True
        if "italic" in words:
            params["italic"] = True
        if "underline" in words:
            params["underline"] = True
        return params

    m = re.search(r"\b(?:insert|write|type)\b\s+(.+)", low)
    if m:
        return {"app": "word", "action": "insert_text", "text": m.group(1).strip(" '\"")}

    cell_match = _CELL_REF_RE.search(objective)
    if cell_match:
        cell = cell_match.group(1).upper()
        if any(w in low for w in ("get", "read", "what", "show")):
            return {"app": "excel", "action": "get_cell", "cell": cell}
        vm = re.search(r"\b(?:to|as)\b\s+(.+)$", low) or re.search(r"=\s*(.+)$", low)
        if vm:
            value_str = vm.group(1).strip(" '\"")
            try:
                value = float(value_str) if "." in value_str else int(value_str)
            except ValueError:
                value = value_str
            return {"app": "excel", "action": "set_cell", "cell": cell, "value": value}
        if context is not None and context.values and (words & _REFERENTIAL_WORDS):
            value_str = next(reversed(context.values.values()))
            try:
                value = float(value_str) if "." in value_str else int(value_str)
            except ValueError:
                value = value_str
            return {"app": "excel", "action": "set_cell", "cell": cell, "value": value}

    if "save" in words:
        app = _office_app(words)
        if app:
            return {"app": app, "action": "save"}

    return None


def _run_office(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    """Parses the objective into office_control.py's own (app, action,
    ...) parameter shape, then calls it in-process exactly as it already
    exists — no second Office controller, no reimplemented Word/Excel
    logic. If no supported action can be confidently determined, reports
    that honestly instead of guessing or fabricating success (see
    _parse_office_action's own docstring). The one Phase 5A opt-in
    consumer of TaskContext among today's handlers — see
    _parse_office_action's own docstring for exactly when/how."""
    params = _parse_office_action(objective, context)
    if params is None:
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            "no specific Office action (insert/replace/format/save/a cell "
            "reference) could be determined from this objective — "
            "office_control.py has no generic 'just open the app' action; "
            "ask the user what they want done inside Word/Excel",
        )
    result = office_control(parameters=params)
    tag = _classify_office_result(result)
    if status_of(result):
        return result
    return _envelope.envelope(tag, result)


_HANDLERS = {
    "youtube": _run_youtube,
    "browser": _run_browser,
    "office": _run_office,
    "system_volume": _run_system_volume,
    "system_power": _run_system_power,
    "system_shortcut": _run_system_shortcut,
}

# Bounded, ordered recovery chain: if the PRIMARY domain's result is
# escalatable (INCONCLUSIVE/UI_AMBIGUOUS — never a known VERIFIED_FAILURE,
# which is a real, already-known outcome, not something retrying a
# DIFFERENT method would fix), try the next domain down — never the SAME
# domain again. Matches this project's own no-blind-retry rule.
_RECOVERY_CHAIN = {
    "youtube": "browser",
}
# Phase 4 (Office) considered and rejected inventing an office->* entry:
# office_control.py exposes no genuine alternative-method relationship
# to word/excel (unlike youtube->browser's real "try the general web"
# fallback) — a failed insert_text/set_cell falling back to browser or
# system_shortcut would not be a sane recovery of anything. Same
# no-artificial-recovery discipline as Phase 3's system domains.


# ── Context extraction (Phase 5A) ───────────────────────────────────────
# Best-effort, deterministic extraction of a SMALL, well-known scalar out
# of a step's VERIFIED_SUCCESS evidence into TaskContext.values — the
# same local-regex discipline used everywhere else in this module
# (_run_system_volume's numeric extraction, _parse_office_action's
# parsing), just pointed at output instead of input. Not a generic
# extraction framework: a new domain gets a rule added here ONLY when a
# concrete later objective actually needs to consume it — nothing is
# extracted speculatively. Today's one real rule exists specifically to
# support Phase 5B's proof workflow (battery percent -> Office cell).

_BATTERY_PERCENT_RE = re.compile(r"Percent:\s*(\d{1,3})")


def _extract_context_values(domain: str, result: str, context: TaskContext) -> None:
    if domain == "system_shortcut":
        m = _BATTERY_PERCENT_RE.search(result)
        if m:
            context.values["percent"] = m.group(1)


def build_plan(objectives: list[str]) -> tuple[list[PlanStep] | None, str | None]:
    """JARVIS's OWN plan-construction step — the concrete meaning of
    'JARVIS creates the executable plan, Gemini only decomposes intent':
    the EXISTING route() (unchanged) is run for EVERY incoming objective
    up front, before any of them execute, so a compound task is never
    partially executed when JARVIS already knows part of it can't be
    routed. Gemini's objectives list is raw input text; the returned
    list of PlanStep records — each carrying JARVIS's own routing
    decision — is the actual executable plan.

    This validates two of the four distinguishable layers a plan can
    fail at:
      1. structural validity   — objectives non-empty, count bounded.
      2. domain resolution     — route() clears its confidence bar.
    It deliberately does NOT attempt to pre-validate:
      3. executability of the requested operation (route() resolving to
         a domain doesn't guarantee a handler can turn the objective's
         exact wording into a real capability call — e.g.
         _parse_office_action() may still return None) — this is
         discovered honestly at execution time, exactly as it already
         is today, and reported through the same INCONCLUSIVE path.
      4. availability of a required context value a later step's
         wording implies but which no prior step actually produced —
         same treatment: discovered honestly when that step actually
         runs, not pre-checked.
    Building separate up-front checks for 3/4 would mean giving every
    handler a dry-run/preview mode — real added complexity Phase 5A
    doesn't need; the existing per-handler honesty already covers both,
    just later than a hypothetical full pre-flight check would.

    Returns (plan, None) on success, or (None, evidence) if rejected —
    the caller reports that honestly and executes nothing."""
    if not objectives:
        return None, "no objective was given"
    if len(objectives) > _MAX_OBJECTIVES_PER_TASK:
        return None, (
            f"{len(objectives)} objectives were given, more than the "
            f"bounded maximum of {_MAX_OBJECTIVES_PER_TASK}"
        )
    plan: list[PlanStep] = []
    for objective in objectives:
        if not objective.strip():
            return None, "one of the given objectives was empty"
        domain = route(objective)
        if domain is None:
            return None, f"no known JARVIS capability confidently matches '{objective}' yet"
        plan.append(PlanStep(objective, domain))
    return plan, None


def _execute_step(task: Task, plan_index: int, plan_step: PlanStep, confirmed: bool) -> str:
    """Executes ONE PlanStep to a terminal Result Envelope status —
    Phase 0-4's entire former execute_task() body, extracted essentially
    unchanged: routing is already decided (by build_plan(), not here),
    family-scoped recovery and terminal-status handling are byte-for-byte
    what they always were. The one real, necessary change: the per-step
    attempt budget is now a LOCAL `attempts` counter rather than
    `len(task.steps)` — task.steps is shared across every PlanStep in a
    multi-objective Task now, so the old length-based check would have
    silently shrunk each later PlanStep's own recovery budget."""
    objective = plan_step.objective
    domain = plan_step.domain
    tried: list[str] = []
    attempts = 0
    result = ""
    while domain and attempts < _MAX_STEPS_PER_TASK:
        attempts += 1
        tried.append(domain)
        handler = _HANDLERS[domain]
        started_at = time.monotonic()
        result = handler(objective, confirmed, task.task_context)
        step = task.record(domain, result, started_at, plan_index)
        plan_step.status = step.status

        if step.status == _envelope.STATUS_VERIFIED_SUCCESS:
            task.task_context.raw[plan_index] = result
            _extract_context_values(domain, result, task.task_context)
            return result

        if step.status == _envelope.STATUS_BLOCKED:
            # Permanently refused by policy — never a "try a different
            # method" situation (that's what RECOVER is for); a
            # different domain can't turn "blocked" into "allowed".
            task.task_context.raw[plan_index] = result
            return result

        if step.status == _envelope.STATUS_CONFIRMATION_REQUIRED:
            # Needs an explicit human yes — a different domain/method
            # can't supply that on the user's behalf. Terminal, exactly
            # like VERIFIED_SUCCESS/BLOCKED above, just not yet allowed
            # rather than refused outright.
            task.task_context.raw[plan_index] = result
            return result

        if step.status == _envelope.STATUS_VERIFIED_FAILURE:
            # A known, real outcome — trying a DIFFERENT domain is still
            # allowed (see _RECOVERY_CHAIN), but never retry THIS domain.
            pass

        next_domain = _RECOVERY_CHAIN.get(domain)
        # Family-scoped recovery: a hop is only taken if the candidate is
        # untried AND in the SAME family as the domain that just ran —
        # recovery tries a different METHOD within one conceptual
        # category, never jumps categories. RECOVERY rule only — has no
        # bearing on task SEQUENCING (see execute_task()), which may
        # cross families deliberately.
        if next_domain and next_domain not in tried and family_of(next_domain) == family_of(domain):
            task.state = TASK_RECOVERING
            domain = next_domain
            continue
        break

    # Every available domain in the chain was tried (or the step budget
    # was hit) without a VERIFIED_SUCCESS — report the LAST real result
    # honestly rather than inventing a generic failure message, so the
    # actual evidence (e.g. "Could not open: ...") reaches Gemini/the user.
    task.task_context.raw[plan_index] = result
    if not status_of(result):
        result = _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE, f"tried {', '.join(tried)}, neither confirmed success"
        )
        plan_step.status = _envelope.STATUS_INCONCLUSIVE
    return result


def execute_task(parameters: dict = None) -> str:
    """The jarvis_task entry point (see main.py's dispatch). parameters:
    objective (str) — the legacy single-objective interface; continues
    to work completely unchanged, a one-item plan behaves identically to
    Phase 0-4's execute_task(). objectives (list[str]) — Phase 5A: an
    ORDERED list of Gemini's own atomic, plain-language sub-objectives
    for a genuinely compound request — still domain-agnostic, still not
    an executable plan (see build_plan()). Give exactly one of the two;
    if both are given, objectives wins. context (str, optional).
    confirmed (bool, optional) — threaded through to every PlanStep's
    handler exactly as before.

    Task-level VERIFIED_SUCCESS requires EVERY PlanStep to have reached
    VERIFIED_SUCCESS — dispatching every step is not the same as the
    task succeeding. The first PlanStep that does not reach
    VERIFIED_SUCCESS stops the WHOLE task there: later PlanSteps are
    never attempted after a permanently-failed/blocked/
    confirmation-required one, and JARVIS never modifies or replans the
    remaining PlanSteps — the only adaptivity is the existing,
    family-scoped, same-step recovery mechanism inside _execute_step()."""
    params = parameters or {}
    raw_objectives = params.get("objectives")
    if raw_objectives:
        objectives = [str(o).strip() for o in raw_objectives if str(o or "").strip()]
    else:
        single = (params.get("objective") or "").strip()
        objectives = [single] if single else []
    context = (params.get("context") or "").strip()
    confirmed = bool(params.get("confirmed", False))

    plan, error = build_plan(objectives)
    if plan is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, error)

    task = Task(objective=objectives[0], context=context, objectives=objectives)
    task.plan = plan
    task.state = TASK_EXECUTING

    result = ""
    for plan_index, plan_step in enumerate(plan):
        task.current_step_index = plan_index
        result = _execute_step(task, plan_index, plan_step, confirmed)

        if plan_step.status == _envelope.STATUS_VERIFIED_SUCCESS:
            continue  # sequencing: advance to the next PlanStep (may cross families)

        # Anything else is terminal for the WHOLE task, not just this
        # PlanStep — no autonomous replanning, no skipping ahead.
        if plan_step.status == _envelope.STATUS_BLOCKED:
            task.state = TASK_BLOCKED
        elif plan_step.status == _envelope.STATUS_CONFIRMATION_REQUIRED:
            task.state = TASK_AWAITING_CONFIRMATION
        else:
            task.state = TASK_FAILED
        return result

    # Every PlanStep independently reached VERIFIED_SUCCESS.
    task.state = TASK_COMPLETED
    return result
