"""
actions/git_control.py — JARVIS's J9 Git capability: a thin, explicit-
allowlist wrapper over real `git` subprocess calls, scoped to ONE
explicitly resolved repository root.

Inspection finding this module is built on: no Git helper existed
anywhere in this repository before J9 (repo_agent.py's own J8 docstring
explicitly deferred it: "Git operations (status/commit/push/branch/
merge) — J9's job, not J8."). Everything else this module needs already
exists and is reused, never duplicated:
  - `actions/repo_agent.py`'s `resolve_repo_root()` — the SAME explicit-
    repository-boundary resolution J8 already established (defaults to
    the JARVIS repo itself; an explicit path must exist, be a directory,
    and pass file_controller.py's own `_is_safe_path()` home-folder
    check) — a second repository-root resolver would be exactly the
    duplication the roadmap warns against.
  - `actions/repo_agent.py`'s `_is_within_repo()` — the same repo-
    boundary containment check, reused for `stage`'s optional path
    argument (never a second boundary check).
  - `actions/repo_agent.py`'s `_run_one()` — the SAME bounded, timeout-
    guarded subprocess pattern J8 already uses for test-command
    execution (capture both streams, never hang forever, a timeout is
    reported honestly rather than faked as a real exit code) — pointed
    at `git` instead of a test command. No second subprocess wrapper.
  - `actions/file_controller.py`'s `_is_safe_path()` — the same J7
    home-folder boundary, checked a second, narrower time for `stage`'s
    optional explicit path argument.
  - `actions/result_envelope.py` — the ONE shared status vocabulary and
    the ONE centralized `is_consequential()`/`is_confirmed()` gate (a
    new "git_commit" entry, same tier as file_controller.py's "delete"
    (J7) and repo_agent.py's "repo_edit" (J8)) — never a second
    confirmation framework, never a new status vocabulary.

Deliberately NOT built, and why:
  - `git_control(command="...")` accepting an arbitrary Git CLI string —
    the one thing this module must never expose (section 20's own
    explicit prohibition). Every action below is a fixed, hardcoded
    argv list; nothing here ever splices caller-supplied text into a
    shell command or an arbitrary git subcommand. A commit message or a
    stage path is passed as ONE argv element to `subprocess.run` (no
    `shell=True`, exactly `_run_one()`'s existing pattern) — it can
    never be interpreted as additional Git flags or a second command.
  - push/pull/fetch/remote administration — explicitly out of scope for
    J9 (the roadmap's own "PUSH IS OUT OF SCOPE FOR J9" instruction).
    Recognized by name and permanently BLOCKED below, same as the
    genuinely destructive operations, rather than silently ignored.
  - force-push, `reset --hard`, destructive cleanup (`clean -fd`),
    rebase, merge, history rewriting — permanently BLOCKED by an
    explicit denylist, never reachable through any action name, exactly
    per the "CRITICAL SAFETY BOUNDARY" section of the roadmap.
  - branch switching (`checkout`/`switch`) — considered and deliberately
    NOT implemented. A safe verification model for "did this discard
    local changes" would require independently re-deriving Git's own
    internal same-file-conflict logic; the roadmap's own escape hatch
    ("if branch functionality becomes too large to implement safely in
    this stage, keep it read-only and report the limitation") applies
    directly. Recognized by name and returns an honest, disclosed
    [INCONCLUSIVE] rather than either faking support or being silently
    unreachable.
  - A second coding/repository agent, a second planner, a second
    execution queue, a second Result Envelope vocabulary, a second
    confirmation framework — none of those were needed; this module is
    exactly as thin as `repo_agent.py` (J8) was.
"""
from pathlib import Path

from actions import result_envelope as _envelope
from actions.repo_agent import resolve_repo_root, _is_within_repo, _run_one
from actions.file_controller import _is_safe_path

# Bounded — same "never hang forever" discipline as repo_agent.py's own
# test-run timeout, just shorter: every Git action here is expected to
# return in well under a second on a local repository.
_GIT_TIMEOUT = 30

# ── Explicit action allowlist (section 20's own requirement) ────────────
# Read-only: always safe, never require confirmation, always return real
# Git output. State-changing-but-reversible: no artificial confirmation
# gate (git add is trivially undoable; nothing is a one-way action).
# Consequential: gated through the SAME centralized is_consequential()/
# is_confirmed() mechanism file_controller.py's delete (J7) and
# repo_agent.py's edit (J8) already use.
_READ_ONLY_ACTIONS = frozenset({"status", "diff", "log", "branch"})
_STATE_CHANGING_ACTIONS = frozenset({"stage"})
_CONSEQUENTIAL_ACTIONS = frozenset({"commit"})

# Recognized by name, but deliberately never implemented — an honest,
# disclosed limitation (see module docstring), not a policy block.
_KNOWN_UNSUPPORTED_ACTIONS = frozenset({"checkout", "switch"})

# Recognized by name and permanently refused regardless of confirmation
# — this is what BLOCKED means (result_envelope.py's own distinction:
# "there is no confirmed=true that makes it proceed"). Deliberately a
# fixed, explicit denylist rather than "anything not on the read-only/
# consequential lists is fine" — a caller can never reach real `git
# push`/`git reset --hard`/etc. through this module under any action
# name, spelling, or alias listed here.
_PERMANENTLY_BLOCKED_ACTIONS = frozenset({
    "push", "pull", "fetch", "remote",
    "force_push", "force-push", "push_force", "force",
    "reset", "reset_hard", "reset-hard", "hard_reset",
    "clean", "clean_fd", "clean-fd",
    "rebase", "merge",
    "delete_branch", "branch_delete", "branch_d", "branch_force_delete",
    "filter_branch", "filter-branch", "gc", "prune", "reflog_expire",
})


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    """The one place an actual `git` subprocess is ever invoked — always
    a fixed argv list built entirely by this module's own code, never
    caller-supplied text spliced into it. Reuses repo_agent.py's own
    `_run_one()` verbatim (see module docstring)."""
    return _run_one(["git"] + args, cwd, _GIT_TIMEOUT)


def _resolve_and_check(repo_root: str) -> tuple[Path | None, str | None]:
    """Shared repo-root resolution + "is this actually a Git repository"
    check every action below needs. Returns (root, None) on success, or
    (None, envelope_string) with an already-built, honest Result
    Envelope ready to return directly."""
    root, err = resolve_repo_root(repo_root)
    if root is None:
        status = _envelope.STATUS_BLOCKED if "Access denied" in (err or "") else _envelope.STATUS_VERIFIED_FAILURE
        return None, _envelope.envelope(status, err)
    if not (root / ".git").exists():
        return None, _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"{root} is not a Git repository (no .git found)")
    return root, None


# ── Read-only actions ─────────────────────────────────────────────────────

def status(repo_root: str = "") -> str:
    """Real `git status` output. Always safe, never confirmation-gated —
    reading state changes nothing."""
    root, envelope_err = _resolve_and_check(repo_root)
    if root is None:
        return envelope_err
    code, output = _git(["status"], root)
    if code != 0:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"git status failed: {output}")
    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, output or "clean working tree")


def diff(repo_root: str = "") -> str:
    """Real `git diff` (unstaged working-tree changes). An empty diff is
    a real, verified state — not a failure — same honesty as
    repo_agent.py's search() reporting 'no matches' as VERIFIED_SUCCESS."""
    root, envelope_err = _resolve_and_check(repo_root)
    if root is None:
        return envelope_err
    code, output = _git(["diff"], root)
    if code != 0:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"git diff failed: {output}")
    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, output if output.strip() else "no uncommitted changes")


def log(repo_root: str = "", max_count: int = 10) -> str:
    """Real recent commit history. A brand-new repository with zero
    commits is a real, honest state (git itself exits non-zero with
    'does not have any commits yet' / 'unknown revision' for `git log`
    on an empty repo) — reported as VERIFIED_SUCCESS with that fact,
    never mistaken for a command failure."""
    root, envelope_err = _resolve_and_check(repo_root)
    if root is None:
        return envelope_err
    count = max(1, int(max_count))
    code, output = _git(["log", f"-n{count}", "--pretty=format:%h %ad %s", "--date=short"], root)
    if code != 0:
        low = output.lower()
        if "does not have any commits yet" in low or "unknown revision" in low or "bad default revision" in low:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "this repository has no commits yet")
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"git log failed: {output}")
    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, output or "this repository has no commits yet")


def branch(repo_root: str = "") -> str:
    """Real local branch listing (read-only — see module docstring for
    why checkout/switch is deliberately not implemented)."""
    root, envelope_err = _resolve_and_check(repo_root)
    if root is None:
        return envelope_err
    code, output = _git(["branch", "--list"], root)
    if code != 0:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"git branch failed: {output}")
    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, output or "no branches yet")


# ── State-changing but reversible ────────────────────────────────────────

def stage(repo_root: str = "", paths: str = "") -> str:
    """`git add` — either one explicit, repo-boundary-checked path, or
    everything (`-A`) when none is given. Verified afterward against the
    real index (`git diff --cached --name-only`), never assumed
    successful merely because the subprocess exited 0."""
    root, envelope_err = _resolve_and_check(repo_root)
    if root is None:
        return envelope_err

    target = (paths or "").strip()
    if target:
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = root / candidate
        if not _is_within_repo(candidate, root) or not _is_safe_path(candidate):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {candidate}")
        add_args = ["add", "--", str(candidate.relative_to(root))]
    else:
        add_args = ["add", "-A"]

    before_code, before_status = _git(["status", "--porcelain"], root)
    code, output = _git(add_args, root)
    if code != 0:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"git add failed: {output}")

    after_code, staged = _git(["diff", "--cached", "--name-only"], root)
    if after_code == 0 and staged.strip():
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"staged: {staged.strip()}")
    if before_code == 0 and not before_status.strip():
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "nothing to stage — working tree already matches the index")
    return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "git add exited 0 but the index does not show the expected staged change")


# ── Consequential ─────────────────────────────────────────────────────────

def commit(repo_root: str = "", message: str = "", confirmed: bool = False) -> str:
    """Commit is consequential (it changes repository history) — gated
    through the SAME centralized is_consequential()/is_confirmed()
    classifier file_controller.py's delete (J7) and repo_agent.py's edit
    (J8) already use. A successful commit is verified against REAL Git
    state afterward (HEAD actually changed, and the new commit's own
    message matches the request) — never reported as VERIFIED_SUCCESS
    merely because the `git commit` subprocess returned exit code 0."""
    root, envelope_err = _resolve_and_check(repo_root)
    if root is None:
        return envelope_err

    message = (message or "").strip()
    if not message:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "a commit message is required")

    staged_code, staged = _git(["diff", "--cached", "--name-only"], root)
    if staged_code != 0 or not staged.strip():
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "nothing is currently staged to commit — stage the changes first")

    if _envelope.is_consequential(action_name="git_commit") and not _envelope.is_confirmed({"confirmed": confirmed}):
        return _envelope.envelope(_envelope.STATUS_CONFIRMATION_REQUIRED, f'this will create a real commit in {root.name}: "{message}"')

    before_code, before_head = _git(["rev-parse", "HEAD"], root)
    before_head = before_head.strip() if before_code == 0 else None

    code, output = _git(["commit", "-m", message], root)
    if code != 0:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"git commit failed: {output}")

    after_code, after_head = _git(["rev-parse", "HEAD"], root)
    if after_code != 0:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"commit command exited 0 but HEAD could not be verified afterward: {output}")
    after_head = after_head.strip()

    if after_head == before_head:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "commit command exited 0 but HEAD did not change — could not confirm a real commit was created")

    subj_code, subject = _git(["log", "-1", "--pretty=%s"], root)
    if subj_code != 0 or subject.strip() != message:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"a new commit {after_head[:8]} was created but its message could not be confirmed to match the request")

    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f'commit {after_head[:8]} created in {root.name}: "{message}"')


def git_control(parameters: dict = None) -> str:
    """The one entry point main.py/task_engine.py call — mirrors the
    established xxx_control(parameters={...}) convention every other
    JARVIS capability module already uses (file_controller/office_control/
    repo_agent). Deliberately NO 'command' parameter exists anywhere in
    this module — only these fixed action names are ever recognized;
    there is no way to reach an arbitrary `git` argv through this
    function (section 20's own explicit requirement)."""
    params = parameters or {}
    action = (params.get("action") or "").lower().strip()
    repo_root = params.get("repo_root", "")

    if action in _PERMANENTLY_BLOCKED_ACTIONS:
        return _envelope.envelope(
            _envelope.STATUS_BLOCKED,
            f"'{action}' is a destructive, remote, or history-rewriting Git operation and is permanently disallowed",
        )
    if action in _KNOWN_UNSUPPORTED_ACTIONS:
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            f"branch switching ('{action}') is not supported yet — kept deliberately read-only for safety; switch branches manually for now",
        )

    if action == "status":
        return status(repo_root)
    if action == "diff":
        return diff(repo_root)
    if action == "log":
        return log(repo_root, int(params.get("max_count", 10)))
    if action == "branch":
        return branch(repo_root)
    if action == "stage":
        return stage(repo_root, params.get("paths", ""))
    if action == "commit":
        return commit(repo_root, params.get("message", ""), bool(params.get("confirmed", False)))

    return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Unknown or unsupported Git action: '{action}'")
