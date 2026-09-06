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

J4 (Plan -> Act -> Verify, formalizing what Phase 5A/5B already built):
PLAN is build_plan(); ACT is a handler call inside _execute_step();
VERIFY is that same call's classifier/Result-Envelope status — these
were already real, distinct steps, just not named that way. What J4
actually added: (1) the task-level FINAL REPORT for a multi-objective
Task now covers every objective actually attempted, not just the last
one (see _build_final_report()/_finalize_result()) — closing the exact
gap docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md § 16's own J4 exit
criteria name ("the FINAL report matches the ORIGINAL objective, not
just the last step"); (2) TASK_INCONCLUSIVE as its own observable task
state, distinct from TASK_FAILED (a verified failure and "couldn't tell"
are different outcomes and were being conflated); (3) CANCELLED is now
explicitly terminal in _execute_step(), matching BLOCKED/
CONFIRMATION_REQUIRED, instead of silently falling into the same
recovery-chain path INCONCLUSIVE/UI_AMBIGUOUS correctly use. A
single-objective Task is untouched by any of this — see
execute_task()'s and _finalize_result()'s own docstrings.

J6 (Computer/Application Perception — INSPECT): a composed, deterministic
pre-action state query (inspect(), see its own docstring) reusing the
EXISTING perception primitives (computer_control.py's
get_active_window_title()/list_ui_elements(), screen_processor.py's
_capture_screen()) exactly as they already exist — no new controller, no
new screenshot/UI engine, nothing sent to Gemini for interpretation (that
stays main.py's own, separate, async observe/verify-vision mechanism —
genuinely a different thing, see inspect()'s own docstring for why it
isn't reused here). Runs inside _execute_step(), right before a handler
call, for whichever domains _INSPECT_CONFIG actually declares applicable
(JARVIS's own deterministic config — Gemini never chooses this); its
result is recorded on the Step it preceded (Step.observation), never fed
into plan_step.status/task.state, so a perception failure/partial
observation can never itself become a recovery trigger or a terminal
outcome — VERIFY (the classifier) remains the only thing that decides
success/failure, exactly as J4 established.

Explicitly NOT this module's job, and never added here: a second AI/LLM
choosing what to do: a second tool-execution queue: a second
verification vocabulary; browser/UI-automation implementation itself
(that stays owned by browser_control.py/computer_control.py); permanent
personal memory (a Task's steps are runtime-only and are never written
to memory/* — see Task.__init__'s own note); expanded tiered recovery
across method hierarchies (J5); a new perception/vision subsystem or
continuous/background screen monitoring (J6 composes what already
exists, once, on demand, before an action — never a loop).
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
from actions.file_controller import file_controller
from actions.repo_agent import repo_agent
from actions.git_control import git_control
# J6: the EXISTING perception primitives, reused as-is — see inspect()'s
# own docstring. Never a new controller/screenshot engine.
from actions.computer_control import get_active_window_title, list_ui_elements
from actions.screen_processor import _capture_screen

# Bounded per-task step budget. Deliberately a small, LOCAL constant for
# this pilot scope rather than importing main.py's
# _JARVIS_MAX_ACTIONS_PER_TURN — task_engine.py must not import main.py
# (main.py imports THIS module; the reverse would be circular). Unifying
# the two governors into one shared bound is real follow-up work once
# the Task Engine covers more than one domain, not done here — see
# docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md.
#
# J5 note: this is the SAME bound _execute_step()'s tiered fallback loop
# already used pre-J5 (a value of 3 was already headroom for one primary
# attempt plus up to TWO recovery hops, i.e. a 2-tier fallback chain —
# the pilot-scope comment this replaced undersold it as "one recovery
# attempt"). No _RECOVERY_CHAIN entry deep enough to actually use the
# second hop exists today (see that dict's own comment) — this bound was
# simply never the limiting factor, and raising it further would only
# matter once a real 2-hop chain exists.
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
TASK_INCONCLUSIVE          = "INCONCLUSIVE"
TASK_BLOCKED               = "BLOCKED"
TASK_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
TASK_CANCELLED             = "CANCELLED"
# J4 note on the two states NOT listed above ("planned"/"verification" in
# the architecture doc's own aspirational table, § 7): RECEIVED already
# covers "planned" (a Task's plan is built by build_plan() before
# TASK_EXECUTING is ever set — see execute_task()). A separate VERIFYING
# state was deliberately NOT added: execute_task()/_execute_step() are a
# single synchronous call with no await point between an action and its
# verification (the classifier IS the verification, run inline, in the
# same Python statement) — there is no moment at which an external
# caller could ever observe "verifying" as distinct from "executing",
# so a state nothing can ever read would be pure ceremony, not real
# tracking. Revisit only if execution ever becomes genuinely async.


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


class Observation:
    """J6: the composed pre-action state query's OWN result — what did
    the environment look like right before ONE Step's action ran.
    Deliberately tiny, matches this module's own 'keep the model small'
    rule, and deliberately NOT a general-purpose perception object: every
    field is either the exact string an existing primitive already
    returns (get_active_window_title()/list_ui_elements() are already
    honest about failure in their own return value — an empty string or
    a 'Could not ...'/'... is only available on ...' message respectively
    — so this class does nothing extra to detect success/failure, it
    just carries what those functions themselves already said) or a
    short status word for screenshot (never the raw image bytes — see
    inspect()'s own docstring for why those are discarded immediately).
    Never persisted, never sent to memory/*, discarded with the Task it
    was recorded on — see Step.observation."""
    __slots__ = ("active_window", "ui_elements", "screenshot")

    def __init__(self, active_window: str = "", ui_elements: str | None = None, screenshot: str = ""):
        self.active_window = active_window
        self.ui_elements = ui_elements
        self.screenshot = screenshot


class Step:
    """One attempted action within a Task — deliberately small fields
    only (matches the approved architecture's own 'keep the model small'
    rule): which domain was tried, the raw result string, timing, and
    (Phase 5A) which PlanStep this attempt belongs to. task.steps is
    shared across every PlanStep in a multi-objective Task — plan_index
    is what lets a Step be traced back to the specific objective it was
    trying to satisfy. (J6) `observation` is the Observation INSPECT
    recorded right before this Step's action ran — None for a domain
    _INSPECT_CONFIG doesn't declare applicable (see that dict's own
    comment); never influences `status` (see the `status` property
    below — it reads only from `result`, exactly as it always has)."""
    __slots__ = ("domain", "result", "started_at", "elapsed_s", "plan_index", "observation")

    def __init__(
        self, domain: str, result: str, started_at: float, plan_index: int = 0,
        observation: "Observation | None" = None,
    ):
        self.domain = domain
        self.result = result
        self.started_at = started_at
        self.elapsed_s = time.monotonic() - started_at
        self.plan_index = plan_index
        self.observation = observation

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

    def record(
        self, domain: str, result: str, started_at: float, plan_index: int = 0,
        observation: "Observation | None" = None,
    ) -> Step:
        step = Step(domain, result, started_at, plan_index, observation)
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
# Three are populated by a real domain today (RESOURCE joined in J7). The
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
#   RESOURCE     — files / terminal / processes. `file_system` (J7) is
#                   the first real member — see that domain's own
#                   comment for why no _RECOVERY_CHAIN entry exists for
#                   it (same "no artificial recovery" reasoning Office
#                   already established). Terminal/process capabilities
#                   remain conceptual — J7 deliberately did not build an
#                   arbitrary shell-execution capability (no safe
#                   existing mechanism for one exists in this repo).
#   DEVELOPMENT  — repo agent / git. `repo_agent` (J8) — search/test-run/
#                   edit over an explicit repository boundary — and now
#                   `git` (J9) — status/diff/log/branch/stage/commit over
#                   the SAME explicit repository boundary, reusing
#                   repo_agent.py's own resolve_repo_root() — are both
#                   real members. Push/pull/fetch/remote administration
#                   remain out of scope (J9's own explicit boundary, not
#                   a gap); merge/rebase/deployment automation are J11+.
#   DEPLOYMENT   — deploy + verify. Concept only, not built.

FAMILY_SYSTEM      = "system"
FAMILY_APPLICATION = "application"
FAMILY_RESOURCE    = "resource"
FAMILY_DEVELOPMENT = "development"

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
    # git is declared BEFORE repo_agent (and both before browser, for the
    # same reason repo_agent was moved ahead of browser in J8) so a
    # phrase like "commit the repo changes" — which ties 1-1: "commit"
    # for git vs. "repo" for repo_agent — resolves to git, since only
    # git actually has a commit action; same tie-break-by-declaration-
    # order technique already used throughout this list. Deliberately
    # EXCLUDES "status" (already system_shortcut's own keyword, e.g.
    # "check battery status") and "push"/"pull"/"fetch"/"reset"/"clean"/
    # "force" (git_control.py's own permanently-blocked actions are
    # reached only by an explicit action name, never invented a routing
    # keyword for — see that module's docstring: J9 does not manufacture
    # natural-language routing for operations it refuses to perform).
    {
        "name": "git",
        "family": FAMILY_DEVELOPMENT,
        "keywords": ["git", "commit", "commits", "branch", "branches",
                     "diff", "stage", "staged", "unstaged", "checkout", "log"],
    },
    # repo_agent is declared BEFORE browser so a phrase like "search the
    # repository for X" (which ties 1-1: "search" for browser vs.
    # "repository" for repo_agent) resolves to repo_agent, not browser —
    # confirmed live during J8 testing (not assumed), same tie-break-by-
    # declaration-order technique already used for youtube/office above.
    {
        "name": "repo_agent",
        "family": FAMILY_DEVELOPMENT,
        # Deliberately EXCLUDES "search" itself (already a real browser
        # keyword) and generic verbs that could over-match unrelated
        # requests. "repository"/"repo"/"codebase" are specific nouns
        # naming exactly this domain; "tests" (plural, as in "run the
        # tests") doesn't collide with anything today.
        "keywords": ["repository", "repo", "codebase", "tests"],
    },
    {
        "name": "browser",
        "family": FAMILY_APPLICATION,
        # "open" was deliberately removed (real-world bug found during
        # JARVIS Part 2 diagnosis, not a guess): it's a generic verb
        # every domain's objective can contain, and it was winning
        # browser a false tie/outright score against genuinely more
        # specific requests — confirmed live: "Open YouTube in a new tab
        # in the currently open Chrome" scored browser 2 ("open"+
        # "chrome") vs. youtube's 1 ("youtube"), routing a YouTube
        # request to a literal Google search for the whole sentence.
        # Every OTHER browser keyword below (google/search/webpage/site/
        # website/url/navigate/chrome/firefox/edge) already carries
        # genuine browser objectives on its own — verified against the
        # full existing regression suite, nothing regressed.
        "keywords": ["website", "site", "browser", "search", "google",
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
    # ── RESOURCE family (J7) ─────────────────────────────────────────────
    {
        "name": "file_system",
        "family": FAMILY_RESOURCE,
        # Deliberately NOUNS only — no generic verbs (open/create/delete/
        # read/write/find/move/copy/rename) that could tie against other
        # domains' own keywords or over-match unrelated requests, same
        # "open" lesson browser's own keyword list already learned (see
        # that entry's comment). "disk"/"storage" are deliberately
        # EXCLUDED even though file_controller.py has its own disk_usage
        # action — those words already belong to system_shortcut above
        # (a real, already-working "check disk space" query); adding them
        # here would create a live routing regression, not just a
        # theoretical collision (verified — see
        # tests/test_task_engine_j7.py's own regression test for this).
        "keywords": ["file", "files", "folder", "folders", "directory",
                     "directories", "filesystem"],
    },
]


def _normalize(text: str) -> set:
    return set(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


# J7 real-world routing fix: a genuinely common, natural phrasing —
# "delete notes.txt from my desktop" — contains no bare "file"/"folder"
# noun at all, so file_system's own keywords (deliberately noun-only, see
# that domain's comment) would never match it, and _normalize() itself
# destroys the dot ("notes.txt" -> "notes txt"), losing the one real clue.
# A KNOWN file-extension token right after a dot is strong, unambiguous
# evidence the objective is about a file — injecting the literal word
# "file" into the scored word-set when one appears lets file_system's
# existing keyword still be what matches, no domain-specific scoring
# hack. Deliberately a fixed, small, common-extension allowlist (never a
# bare "\.\w+" pattern) so it can't false-positive on a decimal number
# ("12.5 percent") or an IP/version string ("192.168.1.1") — verified
# both stay unaffected, see tests/test_task_engine_j7.py. Deliberately
# EXCLUDES source-code extensions (py/js/ts/etc.) as of J8 — see
# _CODE_EXTENSION_HINT_RE below for why those moved to their own hint.
_FILE_EXTENSION_HINT_RE = re.compile(
    r"\.(txt|pdf|docx?|xlsx?|pptx?|csv|json|xml|ya?ml|"
    r"jpe?g|png|gif|bmp|svg|webp|heic|"
    r"mp3|mp4|wav|avi|mov|mkv|webm|flac|"
    r"zip|rar|7z|tar|gz|"
    r"log|md|ini|cfg|exe|dll)\b",
    re.IGNORECASE,
)

# J8 real-world routing fix, same technique/reasoning as the file-
# extension hint above: "fix the bug in helper.py"/"edit main.py"
# contain no bare "repository"/"repo"/"codebase" noun at all. Source-
# code extensions were deliberately SPLIT OUT of _FILE_EXTENSION_HINT_RE
# above into their own hint (injecting "repo" instead of "file") because
# a .py/.js/.ts/etc. mention is much more often a development/code
# context than a "manage this as a generic file" one — before this
# split, "fix the bug in helper.py" incorrectly scored file_system a
# point (from the extension) with repo_agent scoring zero. Known,
# disclosed residual ambiguity: a genuine "delete script.py from
# downloads"-style request now honestly returns [INCONCLUSIVE] from
# repo_agent's own parser rather than being deleted — safe (never a
# wrong destructive action), just less convenient for that rare phrasing
# — see tests/test_task_engine_j8.py's own test for this trade-off.
_CODE_EXTENSION_HINT_RE = re.compile(
    r"\.(py|js|ts|jsx|tsx|java|cpp|cs|go|rs|rb|php)\b",
    re.IGNORECASE,
)


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
    if _FILE_EXTENSION_HINT_RE.search(objective or ""):
        words = words | {"file"}
    if _CODE_EXTENSION_HINT_RE.search(objective or ""):
        words = words | {"repo"}
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

_YOUTUBE_CONTENT_RE = re.compile(r"\b(?:play|watch)\s+(.+)", re.IGNORECASE)
_TRAILING_ON_YOUTUBE_RE = re.compile(r"\s+on\s+youtube[.!?]*\s*$", re.IGNORECASE)
# Same six canonical names browser_control.py's own tool schema/_ALIASES
# already recognize — detection only, resolution stays entirely inside
# browser_control.py (no alias table duplicated here).
_BROWSER_NAMES = ("chrome", "edge", "firefox", "opera", "brave", "vivaldi", "safari")


def _extract_youtube_query(objective: str) -> str | None:
    """Deterministic (objective -> YouTube search query) extraction —
    JARVIS's own parsing, never a second LLM call, same technique as
    _run_system_volume's numeric extraction / _parse_office_action's
    parsing. Confirmed real-world bug this fixes: _run_youtube() used to
    hand the WHOLE raw objective to youtube_video() as the search query
    — 'open YouTube' literally searched YouTube for the phrase "open
    YouTube" and played whatever ranked first (reproduced: a real video
    titled "YouTube TV: Nothing but Net").

    Returns None for a pure NAVIGATION objective ('open YouTube', 'open
    YouTube TV', 'open YouTube in a new tab') — there is no play/watch
    verb at all, so there is nothing to search or play; _run_youtube()
    below must navigate instead, never search. Returns the actual
    requested content for a PLAYBACK objective ('play X', 'watch X',
    'open YouTube and play X', 'play X on YouTube') — everything after
    the play/watch verb, with a trailing 'on YouTube' and stray
    punctuation stripped, never the whole sentence."""
    m = _YOUTUBE_CONTENT_RE.search(objective)
    if not m:
        return None
    content = _TRAILING_ON_YOUTUBE_RE.sub("", m.group(1)).strip(" .!?")
    return content or None


def _extract_browser_name(objective: str) -> str | None:
    """Best-effort, deterministic recognition of an explicitly-named
    browser in the objective (e.g. '...in the currently open Chrome') so
    navigation can be pointed at it. Reuses browser_control.py's OWN
    alias/executable resolution (_ALIASES/_resolve_browser) — this only
    detects WHICH name to pass through, it does not re-implement
    resolving it."""
    low = objective.lower()
    for name in _BROWSER_NAMES:
        if name in low:
            return name
    return None


def _run_youtube(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    """Distinguishes NAVIGATION intent ('open YouTube', 'open YouTube
    TV', 'open YouTube in a new tab') from PLAYBACK intent ('play X',
    'open YouTube and play X', 'play X on YouTube') — see
    _extract_youtube_query()'s own docstring for the confirmed bug this
    fixes. A pure navigation objective reuses the EXISTING
    browser_control() go_to action (already native-first — it launches
    the real browser, which hands off to an already-running one instead
    of opening a second window — and already accepts an explicit
    'browser' name) instead of ever calling youtube_video()'s play
    action: no search, no video lookup, no playback, no accidental
    YouTube TV. A playback objective still reuses youtube_video()
    exactly as before, just with the actual requested content instead
    of the whole sentence."""
    query = _extract_youtube_query(objective)
    if query is None:
        url = "https://tv.youtube.com" if "youtube tv" in objective.lower() else "https://www.youtube.com"
        params = {"action": "go_to", "url": url}
        browser = _extract_browser_name(objective)
        if browser:
            params["browser"] = browser
        result = browser_control(parameters=params)
        tag = _classify_browser_result(result)
        if status_of(result):
            return result
        return _envelope.envelope(tag, result)
    return youtube_video(parameters={"action": "play", "query": query}, player=None)


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


def _classify_file_result(result: str) -> str:
    """file_controller.py's file_controller() now returns a Result-
    Envelope-tagged string for EVERY path (J7) — status_of() below
    always catches it in practice; kept only for the same defensive-but-
    normally-unreachable discipline _classify_office_result() above
    already uses, in case a future file_controller.py change ever misses
    a path."""
    tag = status_of(result)
    if tag:
        return tag
    return _envelope.STATUS_INCONCLUSIVE


# ── J7: Terminal & File System ──────────────────────────────────────────
# "Terminal" here means the safe, deterministic FILE side of that name —
# section-by-section inspection found NO existing arbitrary shell/command
# execution capability anywhere in this repository (actions/desktop.py's
# _execute_generated_code() is a restricted PYAUTOGUI-code sandbox for
# computer_control.py's own generated-automation tier, not a general
# command runner, and explicitly forbids subprocess calls in its own
# generated code). Building one now, just to fill out the name, would be
# exactly the "generic unrestricted shell agent" this stage was told not
# to build — so J7 implements only the file-system half, and terminal/
# process capabilities remain a documented placeholder (see
# FAMILY_RESOURCE's own comment) until a real, safe mechanism exists.

_FILE_SHORTCUTS = ("desktop", "downloads", "documents", "pictures", "music", "videos", "home")
_QUOTED_NAME_RE   = re.compile(r"[\"']([^\"']+)[\"']")
_CALLED_NAME_RE   = re.compile(
    r"\b(?:called|named)\s+([A-Za-z0-9 _\-\.]+?)(?:\s+(?:in|on|from|to|at)\b|[.!?]*$)",
    re.IGNORECASE,
)
_FILENAME_TOKEN_RE = re.compile(r"\b([\w\-]+\.[A-Za-z0-9]{1,6})\b")
_RENAME_TO_RE       = re.compile(r"\bto\s+([A-Za-z0-9 _\-\.]+?)[.!?]*$", re.IGNORECASE)

# Pre-J8-hardening fix: a confirmed, real live-usage gap (not a
# hypothetical) — "list files in Desktop/Consumer behaviour" and "find
# the Consumer behaviour folder on my desktop" both used to lose the
# actual subfolder/name entirely, because _extract_file_shortcut() below
# only ever finds the BARE shortcut word and _extract_file_name() only
# recognized a quoted string / "called X" phrase / a file.ext token —
# never an ordinary, unquoted multi-word name sitting next to "folder" or
# a shortcut. Confirmed live: file_controller("Desktop/Consumer
# behaviour") already worked correctly (J7's own fix) — the parser
# simply never constructed that string. The two patterns below are
# deliberately GENERAL (never hardcoded to "Consumer behaviour" or any
# other specific example) — a small, bounded stop-word list is what
# keeps them from over-capturing surrounding verbs/articles/prepositions,
# same discipline as every other regex in this module.
_FILE_STOP_WORDS = frozenset({
    "list", "find", "search", "locate", "show", "contents", "delete", "remove",
    "trash", "rename", "info", "information", "details", "metadata", "read",
    "largest", "biggest", "create", "make", "new", "folder", "file", "files",
    "the", "a", "an", "in", "on", "at", "my", "this", "that", "of", "and",
})
# Shortcut words (desktop/downloads/...) are deliberately NOT stop words
# here — a real name can legitimately contain one ("Tax Documents", "My
# Music Collection"); blocking them mid-scan would truncate exactly the
# multi-word names this fix exists to support. The narrower, correct
# check — "is the ENTIRE captured name just a bare shortcut on its own"
# ('the Desktop folder' meaning the shortcut itself, not a subfolder of
# it) — is applied once, after the full name is captured, in
# _extract_file_path() below.

_SHORTCUT_SLASH_RE = re.compile(
    r"\b(" + "|".join(_FILE_SHORTCUTS) + r")[\\/]+([^,.!?]+)", re.IGNORECASE,
)
_TRAILING_PREP_RE  = re.compile(r"\s+\b(?:on|in|from|to|at)\b.*$", re.IGNORECASE)


def _extract_file_shortcut(low_objective: str) -> str:
    for s in _FILE_SHORTCUTS:
        if s in low_objective:
            return s
    return "desktop"  # file_controller()'s own existing default


def _extract_shortcut_and_subpath(objective: str) -> tuple[str, str] | None:
    """Explicit shortcut/subpath syntax — 'Desktop/Consumer behaviour',
    'Desktop\\Consumer behaviour' — the exact string shape
    file_controller._resolve_path() has supported since J7. Captures
    everything after the slash up to the next punctuation, then trims a
    trailing prepositional tail ('... on my computer') a spoken objective
    might append. Generalizes to ANY name, never hardcoded."""
    m = _SHORTCUT_SLASH_RE.search(objective)
    if not m:
        return None
    remainder = _TRAILING_PREP_RE.sub("", m.group(2)).strip()
    if not remainder:
        return None
    return m.group(1).lower(), remainder


def _extract_named_folder(objective: str) -> str | None:
    """Finds '<name> folder' anywhere in the objective and returns
    <name> — e.g. 'the Consumer behaviour folder on my desktop' ->
    'Consumer behaviour', 'the Tax Documents folder' -> 'Tax Documents'.
    <name> is whatever consecutive non-stop-word tokens immediately
    precede the word 'folder'; hitting a stop word (an article, a verb,
    a shortcut name, a preposition — see _FILE_STOP_WORDS) stops the
    scan, so 'list files in the Desktop folder' correctly yields nothing
    (the folder REFERENCED there is the shortcut itself, not a named
    subfolder of it) rather than misreading a shortcut as a folder name.
    Never hardcoded to one example name — purely positional/stop-word
    driven, so it generalizes to any multi-word name the user states."""
    tokens = objective.split()
    lowered = [t.strip(".,!?").lower() for t in tokens]
    for i, w in enumerate(lowered):
        if w != "folder":
            continue
        j = i - 1
        name_tokens: list[str] = []
        while j >= 0 and lowered[j] not in _FILE_STOP_WORDS:
            name_tokens.insert(0, tokens[j].strip(".,!?"))
            j -= 1
        if name_tokens:
            return " ".join(name_tokens)
    return None


def _extract_file_name(objective: str) -> str | None:
    """Deterministic (objective -> ONE specific file/folder name)
    extraction — deliberately conservative, same technique as
    _parse_office_action's own regex parsing. Returns None for anything
    that doesn't unambiguously name a real target, so every action below
    that requires a specific name simply can't proceed on a vague one —
    this IS the Conservative Destructive Policy's actual mechanism, not a
    separate blocklist of words like 'everything'/'old'/'all': a request
    with no confidently-extractable name never reaches file_controller()
    at all, regardless of which vague words it happened to use.

    Order: an explicit quote wins outright; then 'called/named X'; then a
    file.ext token; then (pre-J8-hardening addition) the unquoted
    '<name> folder' pattern above — added LAST so it can never override a
    more explicit, already-reliable signal, only fill in when nothing
    else matched."""
    m = _QUOTED_NAME_RE.search(objective)
    if m:
        return m.group(1).strip()
    m = _CALLED_NAME_RE.search(objective)
    if m:
        return m.group(1).strip()
    m = _FILENAME_TOKEN_RE.search(objective)
    if m:
        return m.group(1)
    return _extract_named_folder(objective)


def _extract_file_path(objective: str) -> str:
    """The most specific safe path the objective actually states, for
    the DIRECT-NAVIGATION actions (list/largest — 'show me what's inside
    X', not 'search for X somewhere under Y'). Tries, in order: explicit
    shortcut/subpath syntax, then a bare shortcut plus a '<name> folder'
    mention elsewhere in the sentence, then just the bare shortcut
    (existing, unchanged behavior) — never hardcoded to one example, and
    the result still passes through file_controller._resolve_path()'s/
    _is_safe_path()'s own existing, unmodified safety boundary exactly
    like any other path string always has."""
    slash = _extract_shortcut_and_subpath(objective)
    if slash:
        shortcut, subpath = slash
        return f"{shortcut}/{subpath}"
    shortcut = _extract_file_shortcut(objective.lower())
    name = _extract_named_folder(objective)
    if name and name.lower() != shortcut:
        return f"{shortcut}/{name}"
    return shortcut


def _parse_file_action(objective: str) -> dict | None:
    """Deterministic (objective -> file_controller() parameters) parsing
    — JARVIS's own extraction, never a second LLM call, same technique
    and same honesty discipline as _parse_office_action: returns None
    when nothing can be confidently determined, most commonly a
    destructive/modifying request with no specific, extractable target
    (see _extract_file_name's own docstring). Deliberately covers only
    the read-only queries plus delete/create_folder/rename — move/copy/
    write/create_file are NOT parsed from free text in this pass (their
    source+destination+content combinations are genuinely harder to
    extract reliably than a single name); an objective needing one of
    those honestly returns INCONCLUSIVE here rather than guessing. All
    13 file_controller() actions remain fully available directly (SARANA
    mode, or a future parser extension) — this is a parsing-scope
    limitation, not a capability gap in file_controller.py itself."""
    low   = objective.lower()
    words = _normalize(objective)
    # `path` — the search/query ROOT (bare shortcut, e.g. for find's own
    # "locate X somewhere under this root" semantics). `nav_path` — the
    # most specific DIRECT-NAVIGATION target (may include a subfolder,
    # e.g. "Desktop/Consumer behaviour") for actions that mean "show me
    # what's inside exactly this", not "search for something under here"
    # — see _extract_file_path()'s own docstring for the distinction.
    path     = _extract_file_shortcut(low)
    nav_path = _extract_file_path(objective)

    if "largest" in words or "biggest" in words:
        return {"action": "largest", "path": nav_path}

    if words & {"find", "search", "locate"}:
        return {"action": "find", "path": path, "name": _extract_file_name(objective) or ""}

    if words & {"list", "show", "contents"}:
        return {"action": "list", "path": nav_path}

    if words & {"info", "information", "details", "metadata"}:
        name = _extract_file_name(objective)
        return {"action": "info", "path": path, "name": name} if name else None

    if "read" in words:
        name = _extract_file_name(objective)
        return {"action": "read", "path": path, "name": name} if name else None

    if words & {"delete", "remove", "trash"}:
        # THE conservative-policy checkpoint: no confidently-extracted
        # name means no delete attempt at all, regardless of phrasing.
        name = _extract_file_name(objective)
        return {"action": "delete", "path": path, "name": name} if name else None

    if "rename" in words:
        name  = _extract_file_name(objective)
        m     = _RENAME_TO_RE.search(objective)
        if not name or not m:
            return None
        return {"action": "rename", "path": path, "name": name, "new_name": m.group(1).strip()}

    if "folder" in words and (words & {"create", "make", "new"}):
        name = _extract_file_name(objective)
        return {"action": "create_folder", "path": path, "name": name} if name else None

    return None


def _run_file_system(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    """Parses the objective into file_controller.py's own (action, path,
    name, ...) parameter shape, then calls it in-process exactly as it
    already exists — no second file controller, no reimplemented
    filesystem logic. `confirmed` is threaded straight through to
    file_controller()'s EXISTING is_consequential()/is_confirmed() gate
    (J7's own fix — delete now requires it, see file_controller.py's own
    dispatcher) exactly the way _run_system_power already does for
    shutdown/restart; this handler does not reimplement or relax it."""
    params = _parse_file_action(objective)
    if params is None:
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            "no specific file/folder target could be confidently determined "
            "from this objective — ask the user exactly which file or "
            "folder (and where) before trying again; never guess a broad "
            "or destructive target",
        )
    params["confirmed"] = confirmed
    result = file_controller(parameters=params)
    tag = _classify_file_result(result)
    if status_of(result):
        return result
    return _envelope.envelope(tag, result)


def _classify_repo_result(result: str) -> str:
    """repo_agent.py's own repo_agent() returns a Result-Envelope-tagged
    string for every path — same defensive-but-normally-unreachable
    fallback discipline as the other classifiers in this module."""
    tag = status_of(result)
    if tag:
        return tag
    return _envelope.STATUS_INCONCLUSIVE


# ── J8: Software Development Agent ──────────────────────────────────────
_SEARCH_QUOTED_RE   = re.compile(r"[\"']([^\"']+)[\"']")
_REFERENCE_TO_RE    = re.compile(r"\breferences?\s+to\s+(.+?)(?:\s+(?:in|inside|within)\b.*)?[.!?]*$", re.IGNORECASE)
_SEARCH_FOR_RE      = re.compile(r"\bfor\s+(.+?)[.!?]*$", re.IGNORECASE)


def _extract_search_query(objective: str) -> str | None:
    """Deterministic (objective -> ONE search query) extraction, same
    conservative technique/discipline as _extract_file_name() — a
    request with nothing confidently extractable never reaches
    repo_agent.search_repository() with an empty/guessed query."""
    m = _SEARCH_QUOTED_RE.search(objective)
    if m:
        return m.group(1).strip()
    m = _REFERENCE_TO_RE.search(objective)
    if m:
        return m.group(1).strip()
    m = _SEARCH_FOR_RE.search(objective)
    if m:
        return m.group(1).strip()
    return None


def _parse_repo_action(objective: str) -> dict | None:
    """Deterministic (objective -> repo_agent() parameters) parsing —
    JARVIS's own extraction, never a second LLM call for WHICH action to
    take (the edit action's own CONTENT generation still legitimately
    uses Gemini inside code_helper.py — a generative task, not a routing
    decision; see repo_agent.py's own module docstring on that
    distinction). Returns None when nothing can be confidently
    determined — most commonly a search with no extractable query, or an
    edit naming no specific existing file — same honesty discipline as
    _parse_file_action/_parse_office_action."""
    words = _normalize(objective)

    if words & {"test", "tests"}:
        return {"action": "run_tests"}

    low = objective.lower()
    if (words & {"find", "search", "locate"}) or "reference" in low:
        query = _extract_search_query(objective)
        return {"action": "search", "query": query} if query else None

    if words & {"edit", "fix", "modify", "update"}:
        m = _FILENAME_TOKEN_RE.search(objective)
        if not m:
            return None
        return {"action": "edit", "file_path": m.group(1), "instruction": objective}

    return None


def _run_repo_agent(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    """Parses the objective into repo_agent.py's own (action, query/
    file_path/instruction, ...) parameter shape, then calls it in-process
    exactly as it already exists — no second repository agent. `confirmed`
    is threaded straight through to repo_agent.py's EXISTING
    is_consequential()/is_confirmed() gate for its edit action, the same
    way _run_file_system already does for file_controller.py's delete."""
    params = _parse_repo_action(objective)
    if params is None:
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            "no specific repository action could be determined from this "
            "objective — repo_agent.py needs either a specific search query, "
            "'run the tests', or an edit naming one specific existing file; "
            "ask the user to be concrete before trying again",
        )
    params["confirmed"] = confirmed
    result = repo_agent(parameters=params)
    tag = _classify_repo_result(result)
    if status_of(result):
        return result
    return _envelope.envelope(tag, result)


# ── J9: Git ──────────────────────────────────────────────────────────────
# Reuses _SEARCH_QUOTED_RE (defined above for repo_agent's search query
# extraction) for the commit message too — same conservative shape
# ("only an explicit quoted string, never a guessed message") applied a
# second time rather than writing a near-identical regex.

def _classify_git_result(result: str) -> str:
    """git_control.py's own git_control() returns a Result-Envelope-
    tagged string for every path — same defensive-but-normally-
    unreachable fallback discipline as every other classifier here."""
    tag = status_of(result)
    if tag:
        return tag
    return _envelope.STATUS_INCONCLUSIVE


def _extract_commit_message(objective: str) -> str | None:
    m = _SEARCH_QUOTED_RE.search(objective)
    return m.group(1).strip() if m else None


def _parse_git_action(objective: str) -> dict | None:
    """Deterministic (objective -> git_control() parameters) parsing —
    JARVIS's own extraction, no LLM call for WHICH Git action to take.
    Order matters: checkout/switch is checked before branch so "checkout
    the main branch" (which contains both words) maps to the honestly-
    unsupported checkout action, not silently to a branch listing.
    Returns None only when genuinely nothing can be determined (a commit
    request with no extractable quoted message) — same honesty
    discipline as _parse_file_action/_parse_repo_action."""
    words = _normalize(objective)

    # Checked FIRST and explicitly, even though these words are not
    # among the git domain's own routing keywords (see that domain's
    # comment — J9 deliberately does not invent routing keywords for
    # operations it refuses to perform). Once an objective has already
    # reached this function (meaning it DID contain "git"/"commit"/
    # "branch"/etc.), a mention of push/pull/fetch/force/reset/clean/
    # rebase/merge must still surface git_control.py's own explicit
    # BLOCKED response — never silently fall through to the "status"
    # default below, which would otherwise return a misleadingly
    # unrelated but valid-looking success for something the user
    # actually asked to push/reset/clean. Real gap found and fixed
    # during J9's own live end-to-end verification (not hypothetical):
    # "git push these changes" was silently answered with a git status
    # report instead of an honest refusal.
    if words & {"push", "pull", "fetch"}:
        return {"action": "push" if "push" in words else ("pull" if "pull" in words else "fetch")}
    if "force" in words:
        return {"action": "force_push"}
    if "reset" in words:
        return {"action": "reset_hard"}
    if "clean" in words:
        return {"action": "clean"}
    if "rebase" in words:
        return {"action": "rebase"}
    if "merge" in words:
        return {"action": "merge"}

    if words & {"checkout", "switch"}:
        return {"action": "checkout"}
    if words & {"branch", "branches"}:
        return {"action": "branch"}
    if words & {"diff"}:
        return {"action": "diff"}
    if words & {"log", "commits", "history"}:
        return {"action": "log"}
    if words & {"stage", "staged", "unstaged"}:
        return {"action": "stage"}
    if words & {"commit"}:
        message = _extract_commit_message(objective)
        return {"action": "commit", "message": message} if message else None
    # Bare "git status"/"check git" with no more specific sub-action
    # keyword — status is the safe, always-available, read-only default
    # (route() already required at least one git-domain keyword to
    # reach this function at all).
    return {"action": "status"}


def _run_git(objective: str, confirmed: bool = False, context: "TaskContext | None" = None) -> str:
    """Parses the objective into git_control.py's own (action, message/
    paths, ...) parameter shape, then calls it in-process exactly as it
    already exists — no second Git controller. `confirmed` is threaded
    straight through to git_control.py's EXISTING is_consequential()/
    is_confirmed() gate for its commit action, the same way
    _run_repo_agent already does for repo_agent.py's edit action."""
    params = _parse_git_action(objective)
    if params is None:
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            "no specific Git action could be determined from this objective — "
            "git_control.py needs a commit request to include an explicit "
            "quoted message; ask the user to be concrete before trying again",
        )
    params["confirmed"] = confirmed
    result = git_control(parameters=params)
    tag = _classify_git_result(result)
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
    "file_system": _run_file_system,
    "repo_agent": _run_repo_agent,
    "git": _run_git,
}

# Bounded, ordered, TIERED recovery chain (J5's own name for what this
# already was): _execute_step()'s while-loop re-looks-up this dict on
# whatever `domain` it's currently holding, so a chain deeper than one
# hop (a -> b -> c) already works mechanically today, bounded by
# _MAX_STEPS_PER_TASK and by `tried` (a domain already attempted this
# PlanStep is never eligible again, which also makes a cyclic
# misconfiguration like {"a": "b", "b": "a"} self-terminating rather than
# an infinite bounce — see tests/test_task_engine_j5.py's own cycle-
# defense test). Only ONE real entry exists because only one genuine
# alternative-method relationship exists in the capabilities this module
# currently routes to (see the Office note below) — J5 does not invent a
# second one to demonstrate depth (see docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md's
# own J5 entry: "do not force a recovery relationship merely because it
# looks good architecturally").
#
# Eligible for a hop (see _execute_step()): VERIFIED_FAILURE, INCONCLUSIVE,
# UI_AMBIGUOUS — a known failure is still worth trying a DIFFERENT method
# for, an ambiguous/unclear outcome even more so. Never eligible, and
# never will be regardless of what this dict contains: VERIFIED_SUCCESS
# (nothing to recover from), BLOCKED/CONFIRMATION_REQUIRED/CANCELLED
# (hard safety-terminal states — see _execute_step()'s own explicit,
# early-return handling for all three, checked BEFORE this dict is ever
# consulted). Never the SAME domain again, matching this project's own
# no-blind-retry rule (the live Calculator double-click bug).
_RECOVERY_CHAIN = {
    "youtube": "browser",
}
# Phase 4 (Office) considered and rejected inventing an office->* entry:
# office_control.py exposes no genuine alternative-method relationship
# to word/excel (unlike youtube->browser's real "try the general web"
# fallback) — a failed insert_text/set_cell falling back to browser or
# system_shortcut would not be a sane recovery of anything. Same
# no-artificial-recovery discipline as Phase 3's system domains — J5
# reconfirmed this reasoning still holds, nothing new was invented.
# J7 (file_system) applies the identical reasoning: a failed file
# operation has no sane alternative METHOD to fall back to (there is
# only one way to delete/create/rename a file), so no file_system->*
# entry exists either — same discipline, not an oversight.
# J8 (repo_agent) same again: a failed search/test-run/edit has no
# genuine alternative method either (there's one way to grep a repo, one
# test command to run) — no repo_agent->* entry.
# J9 (git) same reasoning again: a failed status/diff/log/branch/stage/
# commit has no genuine alternative METHOD to fall back to (there is
# only one way to run `git status`, one way to commit what's staged) —
# no git->* entry. Recovery also must never be used to retry a commit
# past CONFIRMATION_REQUIRED/BLOCKED — see _execute_step()'s own
# early-return handling for those, checked before this dict is ever
# consulted, unchanged by J9.


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


# ── J6: INSPECT — composed pre-action state query ───────────────────────
# JARVIS's own, deterministic, per-DOMAIN declaration of which perception
# signals (if any) are worth gathering before that domain's handler runs
# — Gemini never chooses this, exactly like route()'s domain choice.
# Deliberately empty for FIVE of today's six real domains, evidence-based,
# not an oversight:
#   youtube/browser — browser_control.py manages its own session/tab
#     state (native-launch-respects-already-open-browser, its own
#     registry) entirely independently of the OS foreground window; a
#     window-title/UI-element read informs nothing it decides.
#   system_volume/system_power/system_shortcut — pure OS-level settings
#     that apply regardless of what window currently has focus; "what's
#     the active window" is not relevant context for any of them.
# office is the one real exception: it operates on an actual GUI
# application (Word/Excel), so recording which window is focused right
# before an attempt is genuinely relevant, low-risk, real context — even
# though office_control() itself decides purely via COM's own
# ActiveWorkbook/ActiveDocument, not this. Only `window` is requested for
# it: `ui_elements`/`screenshot` are real, heavier primitives with no
# actual consumer for ANY domain today (see inspect()'s own docstring on
# why it still supports gathering them) — enabling them here would be
# exactly the "unnecessary inspection" this stage was told not to add.
_INSPECT_CONFIG: dict[str, dict[str, bool]] = {
    "office": {"want_window": True},
}


def inspect(
    want_window: bool = True, want_ui_elements: bool = False, want_screenshot: bool = False,
) -> Observation:
    """J6's composed pre-action state query — answers 'what does the
    environment look like right now', never 'did my action work' (that
    remains VERIFY's job, unchanged — see this module's own top-level
    docstring). Composes the EXISTING perception primitives exactly as
    they exist, calling only what's actually requested (never assumes
    every caller needs every signal):
      - get_active_window_title() (computer_control.py) — already
        returns "" on failure; passed through as-is, never guessed at.
      - list_ui_elements() (computer_control.py) — already returns an
        honest descriptive string on failure (e.g. "Could not determine
        the foreground window.", "UI Automation is only available on
        Windows here..."); passed through as-is.
      - _capture_screen() (screen_processor.py) — the ONE primitive that
        RAISES rather than returning an honest failure string (no mss/
        capture failure), so this is the one place a try/except is
        needed to preserve that limitation honestly instead of crashing
        the whole Step. The raw image bytes are deliberately discarded
        immediately — nothing in this module interprets pixels (Task
        Engine must never gain a vision/LLM decision step of its own —
        see this file's top-level docstring); only whether capture
        itself succeeded is recorded, as "captured" or "unavailable:
        <reason>".

    Deliberately NOT the same mechanism as main.py's own observe/verify
    (self._pending_vision, JARVIS's Gemini-facing vision escalation for
    computer_control.py's own click/type self-verification): that path
    is async, tied to a live JarvisLive session, and hands the screenshot
    to GEMINI for semantic interpretation — none of which this module
    has or should have (it is a plain, synchronous function called from
    inside a single run_in_executor call, with no event loop and no
    Gemini session, and introducing an LLM call here would violate this
    module's own 'no second AI/LLM choosing what to do' rule). INSPECT
    here is the raw, structural, deterministic signal a domain handler's
    surrounding Step can honestly record — not an interpreted one."""
    active_window = ""
    if want_window:
        try:
            active_window = get_active_window_title()
        except Exception:
            active_window = ""  # already the primitive's own honest "unavailable" value

    ui_elements = None
    if want_ui_elements:
        try:
            ui_elements = list_ui_elements()
        except Exception as e:
            ui_elements = f"UI element inspection failed: {e}"

    screenshot = ""
    if want_screenshot:
        try:
            _capture_screen()  # bytes intentionally discarded — see docstring above
            screenshot = "captured"
        except Exception as e:
            screenshot = f"unavailable: {e}"

    return Observation(active_window, ui_elements, screenshot)


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
        # J6: INSPECT, strictly before ACT — only for a domain
        # _INSPECT_CONFIG actually declares applicable (see that dict's
        # own comment); None for every other domain, exactly as before
        # J6 existed. Never affects routing/execution/status below.
        inspect_cfg = _INSPECT_CONFIG.get(domain)
        observation = inspect(**inspect_cfg) if inspect_cfg else None
        started_at = time.monotonic()
        result = handler(objective, confirmed, task.task_context)
        step = task.record(domain, result, started_at, plan_index, observation)
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

        if step.status == _envelope.STATUS_CANCELLED:
            # J4 correctness fix: CANCELLED used to fall through to the
            # SAME implicit recovery-chain check INCONCLUSIVE/UI_AMBIGUOUS
            # use below (it matched none of the explicit branches above),
            # so a cancelled action could have been "recovered" by trying
            # a different method — silently overriding the user's own
            # stop request. result_envelope.py's own ESCALATABLE_STATUSES
            # deliberately excludes CANCELLED for exactly this reason; no
            # existing handler emits it yet (verified — grep of actions/
            # *.py), but the vocabulary is shared, so this module must
            # honor it correctly the moment one does. Terminal, like
            # BLOCKED/CONFIRMATION_REQUIRED above.
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


def _strip_status_tag(result: str) -> str:
    """The evidence text of a Result Envelope string with its leading
    '[STATUS] ' prefix removed (or the original string unchanged if it
    carries no tag) — used only to avoid a doubled '[STATUS] ... [STATUS]
    ...' when re-presenting a PlanStep's own evidence inside the task-
    level report envelope() builds around it (see _build_final_report())."""
    s = (result or "").strip()
    if s.startswith("[") and "]" in s:
        return s[s.index("]") + 1:].strip()
    return s


# Short, human-readable label for each PlanStep outcome inside a
# multi-objective final report — never the internal domain/routing name
# (see _build_final_report()'s own docstring on why).
_STATUS_MARKER = {
    _envelope.STATUS_VERIFIED_SUCCESS:      "verified",
    _envelope.STATUS_VERIFIED_FAILURE:      "failed",
    _envelope.STATUS_INCONCLUSIVE:          "not verified",
    _envelope.STATUS_UI_AMBIGUOUS:          "not verified",
    _envelope.STATUS_BLOCKED:               "blocked",
    _envelope.STATUS_CONFIRMATION_REQUIRED: "needs confirmation",
    _envelope.STATUS_CANCELLED:             "cancelled",
}


def _build_final_report(task: Task, final_status: str) -> str:
    """J4: the synthesized final report for a MULTI-objective Task —
    closes the exact gap the roadmap's own J4 exit criteria name ('the
    FINAL report matches the ORIGINAL objective, not just the last
    step', docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md § 16). Before this,
    a compound task's caller only ever saw the LAST PlanStep's raw
    evidence string — correct as far as the task-level STATUS goes
    (TASK_COMPLETED already required EVERY PlanStep to verify — see
    execute_task()), but silent about what the EARLIER objectives in the
    same task actually accomplished.

    Reports every PlanStep from index 0 up to (and including) the one
    that determined the task's outcome — never the ones after it, which
    were genuinely never attempted (see execute_task()'s own 'stop the
    whole task, never skip ahead' rule). Never exposes the internal
    domain/routing name a PlanStep resolved to — that's JARVIS's own
    implementation detail, not part of a truthful outcome report to
    Gemini/the user (see this module's own 'never expose internal
    implementation details unnecessarily' J4 requirement).

    A single-objective Task never reaches this function at all — see
    execute_task()'s own _finalize_result() call, which returns a
    one-item plan's raw result completely unchanged, byte-for-byte what
    Phase 0-4 already returned."""
    attempted = task.current_step_index + 1
    total = len(task.plan)
    lines = []
    for i in range(attempted):
        plan_step = task.plan[i]
        evidence = _strip_status_tag(task.task_context.raw.get(i, ""))
        marker = _STATUS_MARKER.get(plan_step.status, plan_step.status.lower() or "not verified")
        lines.append(f"{i + 1}. {plan_step.objective} -> {evidence} ({marker})")

    if final_status == _envelope.STATUS_VERIFIED_SUCCESS:
        headline = f"All {total} objective{'s' if total != 1 else ''} verified."
    else:
        headline = f"Objective {attempted} of {total} {_STATUS_MARKER.get(final_status, 'did not verify')}."
        if attempted < total:
            headline += f" The remaining {total - attempted} objective(s) were not attempted."

    return _envelope.envelope(final_status, headline + "\n" + "\n".join(lines))


_TERMINAL_STATE_OF = {
    _envelope.STATUS_BLOCKED:               TASK_BLOCKED,
    _envelope.STATUS_CONFIRMATION_REQUIRED: TASK_AWAITING_CONFIRMATION,
    _envelope.STATUS_CANCELLED:             TASK_CANCELLED,
    _envelope.STATUS_INCONCLUSIVE:          TASK_INCONCLUSIVE,
    _envelope.STATUS_UI_AMBIGUOUS:          TASK_INCONCLUSIVE,
}


def _task_state_for(status: str) -> str:
    """The Task-level state a non-VERIFIED_SUCCESS terminating PlanStep
    status maps to — pulled out of execute_task()'s own loop into one
    small, directly-testable pure function (section 9's own 'keep state
    transitions deterministic and testable' requirement), not a second
    state machine: this is still the exact same TASK_* vocabulary
    Task.state has always used, just named once instead of inline.
    VERIFIED_FAILURE (a real, known outcome) and any status this module
    doesn't otherwise recognize both map to TASK_FAILED — the ordinary
    default, not a special case."""
    return _TERMINAL_STATE_OF.get(status, TASK_FAILED)


def _finalize_result(task: Task, final_status: str, last_result: str) -> str:
    """The ONE place a Task's execution turns into what Gemini/the user
    actually receive. A single-objective Task is untouched — `last_result`
    IS already the complete, accurate report for its one objective,
    exactly what Phase 0-4 always returned (see execute_task()'s own
    docstring: byte-for-byte unchanged). A MULTI-objective Task instead
    gets _build_final_report()'s synthesized report spanning every
    objective actually attempted, not just the terminating one."""
    if len(task.plan) <= 1:
        return last_result
    return _build_final_report(task, final_status)


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
    never attempted after a permanently-failed/blocked/cancelled/
    confirmation-required one, and JARVIS never modifies or replans the
    remaining PlanSteps — the only adaptivity is the existing,
    family-scoped, same-step recovery mechanism inside _execute_step().

    J4: the returned string is EXACTLY the last PlanStep's own raw
    result for a single-objective task (unchanged), and a synthesized,
    whole-plan report for a multi-objective one (see _finalize_result()/
    _build_final_report()) — never simply the last raw capability result
    mistaken for the whole objective's outcome."""
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
        task.state = _task_state_for(plan_step.status)
        return _finalize_result(task, plan_step.status, result)

    # Every PlanStep independently reached VERIFIED_SUCCESS.
    task.state = TASK_COMPLETED
    return _finalize_result(task, _envelope.STATUS_VERIFIED_SUCCESS, result)
