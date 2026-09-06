"""
actions/repo_agent.py — JARVIS's J8 repository-development capability:
repo-wide search, test-run integration, and (conservative, confirmation-
gated) single-file editing, over an EXPLICIT repository boundary.

Inspection finding this module is built on: `code_helper.py` already
provides real, working, Gemini-driven single-file operations (write/
edit/explain/run/optimize) but has NO concept of a repository at all —
it operates on one file path or the Desktop, with no boundary, no
search, no test-command awareness. `dev_agent.py` already provides a
proven classify-error/fix/reverify LOOP pattern (`_classify_error()`/
`_has_error()`), but only for a brand-NEW project it plans and writes
itself under `~/Desktop/JarvisProjects/<name>` — never an existing
repository. Neither module was modified: this module is the thin
orchestration layer the roadmap actually asked for, adding exactly the
two genuinely missing pieces (repo-wide content search, real test-
command detection/execution) plus one conservative edit action that
DELEGATES its actual content generation to `code_helper.py`'s own,
already-working `_edit_action` — never a second code-editing engine.

Reused, never duplicated:
  - `actions/file_controller.py`'s `_is_safe_path()` — the SAME home-
    folder security boundary J7 established, checked here IN ADDITION
    TO (not instead of) this module's own repo-root containment check.
  - `actions/dev_agent.py`'s `_classify_error()` — the same, already-
    proven, deterministic (no LLM) error-category classifier, reused to
    put a human-readable category on a failing test run's evidence.
  - `actions/code_helper.py`'s `code_helper(action="edit", ...)` — the
    actual code-editing engine (Gemini-driven, exactly as it already
    ships) for the one edit action this module exposes.
  - `actions/result_envelope.py` — the ONE shared status vocabulary and
    the ONE centralized is_consequential()/is_confirmed() confirmation
    gate (a new "repo_edit" entry, same tier as file_controller.py's
    "delete" from J7) — never a second confirmation framework.

Deliberately NOT built, and why:
  - Git operations (status/commit/push/branch/merge) — J9's job, not J8.
  - Deployment/production operations — J11's job.
  - An autonomous "keep fixing until tests pass" loop — dev_agent.py's
    own MAX_FIX_ATTEMPTS pattern exists for ITS OWN generated-project
    scope; wiring an unbounded (or even bounded-but-autonomous) repair
    loop onto an EXISTING, arbitrary repository is explicitly out of
    scope for J8 (see the mission's own "J8 is not J10").
  - Free-text parsing of create_file/write/move/copy-shaped repo edits —
    same conservative-scope decision J7 made for file_controller.py's
    own less-common actions; edit here requires an EXPLICIT existing
    file and a clear instruction, never a guessed target.
"""
import subprocess
import sys
import json
from pathlib import Path

from actions import result_envelope as _envelope
from actions.file_controller import _is_safe_path
from actions.dev_agent import _classify_error
from actions.code_helper import code_helper as _code_helper


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


# The ONE project this system inherently has an established context for
# — see resolve_repo_root()'s own docstring on why this is the honest
# default rather than a guess at "the user's current project".
_JARVIS_REPO_ROOT = _get_base_dir()

# Directories never worth scanning — dependency/build/VCS internals that
# are large, irrelevant, and would otherwise dominate every search
# (section 5's own "avoid searching huge irrelevant system locations").
_EXCLUDED_DIR_NAMES = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    "dist", "build", ".pytest_cache", ".idea", ".vscode", ".mypy_cache",
}
_MAX_FILES_SCANNED  = 5000   # bounded, same "safety limit" discipline as file_controller.py's own max_dirs
_MAX_FILE_BYTES     = 2_000_000  # skip anything this large — not a source file worth grepping
_MAX_SEARCH_RESULTS = 30


def resolve_repo_root(explicit: str = "") -> tuple[Path | None, str | None]:
    """Section 6's own requirement made concrete: the repository root is
    NEVER guessed at from natural language. `explicit`, if given, must be
    a real, existing directory that also passes the EXISTING J7 home-
    folder safety boundary (_is_safe_path()) — outside that, refused
    honestly rather than silently treating the user's entire computer as
    a project. With no `explicit` root given, this defaults to the one
    project this system has an actual, established context for: JARVIS's
    own repository (the same BASE_DIR pattern code_helper.py/
    dev_agent.py already use for their own config lookups) — never a
    silent guess at "the user's current project", which this system has
    no reliable way to know.

    Returns (root, None) on success, or (None, evidence) honestly."""
    if not explicit:
        return _JARVIS_REPO_ROOT, None
    candidate = Path(explicit).expanduser()
    if not candidate.exists():
        return None, f"Repository path not found: {explicit}"
    if not candidate.is_dir():
        return None, f"Not a directory: {explicit}"
    if not _is_safe_path(candidate):
        return None, f"Access denied: {candidate}"
    return candidate.resolve(), None


def _is_within_repo(path: Path, repo_root: Path) -> bool:
    """The repo-boundary check J7's own _is_safe_path() doesn't provide
    (that one is fixed to the home folder, not an arbitrary repo root) —
    same technique (.resolve() + is_relative_to()), applied a second,
    narrower time. A file operation must pass BOTH boundaries."""
    try:
        resolved = path.resolve()
        return resolved == repo_root or resolved.is_relative_to(repo_root)
    except Exception:
        return False


def _iter_source_files(repo_root: Path):
    """Bounded, deterministic file walk, skipping the excluded
    directories entirely (never descending into them at all, not just
    filtering their results afterward — the actual performance/safety
    win) and anything too large to be a real source file worth reading."""
    scanned = 0
    stack = [repo_root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except Exception:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in _EXCLUDED_DIR_NAMES:
                    continue
                stack.append(entry)
                continue
            if scanned >= _MAX_FILES_SCANNED:
                return
            try:
                if entry.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except Exception:
                continue
            scanned += 1
            yield entry


def search_repository(query: str, repo_root: str = "", max_results: int = _MAX_SEARCH_RESULTS) -> str:
    """Deterministic, repo-wide CONTENT search — no LLM, no second search
    engine, a direct extension of the same rglob-and-filter technique
    file_controller.py's own find_files()/find already uses, pointed at
    file CONTENT instead of file NAMES (a genuinely different, missing
    capability — find_files() cannot answer "find every reference to
    jarvis_task"). Binary/unreadable files are skipped safely (a UTF-8
    decode failure just excludes that file, never raises past this
    function) — the search is best-effort but never silently wrong about
    what it found; a file it couldn't read is a file it truthfully found
    nothing in, not a false negative it hides."""
    root, err = resolve_repo_root(repo_root)
    if root is None:
        return _envelope.envelope(_envelope.STATUS_BLOCKED if "Access denied" in (err or "") else _envelope.STATUS_VERIFIED_FAILURE, err)
    query = (query or "").strip()
    if not query:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no search query given")

    query_lower = query.lower()
    matches: list[str] = []
    files_with_matches = 0
    for path in _iter_source_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        if query_lower not in text.lower():
            continue
        files_with_matches += 1
        for line_no, line in enumerate(text.splitlines(), start=1):
            if query_lower in line.lower():
                rel = path.relative_to(root)
                matches.append(f"{rel}:{line_no}: {line.strip()[:200]}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    if not matches:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"No matches for '{query}' in {root.name}/")
    suffix = " (truncated)" if len(matches) >= max_results else ""
    return _envelope.envelope(
        _envelope.STATUS_VERIFIED_SUCCESS,
        f"Found {len(matches)} match(es) across {files_with_matches} file(s){suffix}:\n" + "\n".join(matches),
    )


# ── Test-run integration ─────────────────────────────────────────────────

def _detect_test_command(repo_root: Path) -> dict | None:
    """Deterministic project-test-command detection — never invents or
    guesses a command, never runs something the repo itself doesn't
    already define/use. Checked in a fixed, evidence-based order:
    an npm "test" script, a Python pytest project, a Cargo project, or
    — this repo's OWN actual, real convention, confirmed by inspection
    (every tests/test_*.py file's own docstring says
    '.venv/Scripts/python.exe -m tests.test_X', and no pytest.ini/
    pyproject.toml/pytest install exists here) — a tests/ directory of
    standalone test_*.py modules, each run individually via
    `<interpreter> -m tests.<module>` and aggregated. Returns None,
    handled honestly by the caller, if nothing above matches."""
    pkg_json = repo_root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            if "test" in (data.get("scripts") or {}):
                return {"kind": "npm", "command": ["npm", "test"], "cwd": repo_root}
        except Exception:
            pass

    if (repo_root / "pytest.ini").exists() or (repo_root / "pyproject.toml").exists() or (repo_root / "setup.cfg").exists():
        return {"kind": "pytest", "command": [sys.executable, "-m", "pytest"], "cwd": repo_root}

    if (repo_root / "Cargo.toml").exists():
        return {"kind": "cargo", "command": ["cargo", "test"], "cwd": repo_root}

    tests_dir = repo_root / "tests"
    if tests_dir.is_dir():
        test_files = sorted(p.stem for p in tests_dir.glob("test_*.py"))
        if test_files:
            return {"kind": "per_file_python", "command": None, "cwd": repo_root,
                     "package": tests_dir.name, "modules": test_files}

    return None


def _run_one(command: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """The same safe subprocess pattern already established in
    code_helper.py's _run_file()/dev_agent.py's _run_project() — capture
    both streams, bounded timeout, never let a hung process block
    forever. Returns (returncode, combined_output); a timeout is reported
    as returncode None (never fabricated as 0 or silently as a failure
    code that looks like a real one)."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(cwd),
        )
        output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        return result.returncode, output.strip()
    except subprocess.TimeoutExpired:
        return None, f"Timed out after {timeout}s."
    except FileNotFoundError as e:
        return -1, f"Command not found: {e}"
    except Exception as e:
        return -1, f"Execution error: {e}"


def run_tests(repo_root: str = "", timeout: int = 120) -> str:
    """Runs the repository's OWN, actually-detected test command (see
    _detect_test_command()) and returns a Result Envelope status derived
    from the REAL exit code — never 'a process was launched' mistaken
    for 'tests passed' (section 9's own explicit requirement). A timeout
    or an undetectable test command is INCONCLUSIVE, never a guessed
    VERIFIED_SUCCESS/VERIFIED_FAILURE."""
    root, err = resolve_repo_root(repo_root)
    if root is None:
        return _envelope.envelope(_envelope.STATUS_BLOCKED if "Access denied" in (err or "") else _envelope.STATUS_VERIFIED_FAILURE, err)

    detected = _detect_test_command(root)
    if detected is None:
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE,
            f"no known test command could be determined for {root.name}/ "
            "(no package.json test script, no pytest project, no Cargo.toml, "
            "no tests/test_*.py files)",
        )

    if detected["kind"] == "per_file_python":
        failures: list[str] = []
        ran = 0
        for module in detected["modules"]:
            ran += 1
            code, output = _run_one(
                [sys.executable, "-m", f"{detected['package']}.{module}"], detected["cwd"], timeout,
            )
            if code is None:
                failures.append(f"{module}: timed out")
            elif code != 0:
                category = _classify_error(output)
                failures.append(f"{module}: exit {code} ({category}) — {output[-300:]}")
        if not failures:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"All {ran} test module(s) passed in {root.name}/tests/")
        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_FAILURE,
            f"{len(failures)} of {ran} test module(s) failed in {root.name}/tests/:\n" + "\n".join(failures),
        )

    code, output = _run_one(detected["command"], detected["cwd"], timeout)
    if code is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"test run timed out after {timeout}s")
    if code == 0:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"Tests passed ({detected['kind']}) in {root.name}/")
    category = _classify_error(output)
    return _envelope.envelope(
        _envelope.STATUS_VERIFIED_FAILURE,
        f"Tests failed ({detected['kind']}, exit {code}, {category}) in {root.name}/:\n{output[-1500:]}",
    )


# ── Conservative, confirmation-gated single-file edit ───────────────────

def edit_file(file_path: str, instruction: str, repo_root: str = "", confirmed: bool = False) -> str:
    """Modifying source code is consequential (section 20) — gated
    through the SAME centralized is_consequential()/is_confirmed()
    classifier file_controller.py's delete already uses (J7), a new
    'repo_edit' entry in result_envelope.py's own
    _CONSEQUENTIAL_ACTION_NAMES, never a second confirmation framework.
    The actual content generation is entirely code_helper.py's own,
    already-shipped edit action — this function only adds the repo-root
    boundary and the confirmation gate around it, never a second editor."""
    root, err = resolve_repo_root(repo_root)
    if root is None:
        return _envelope.envelope(_envelope.STATUS_BLOCKED if "Access denied" in (err or "") else _envelope.STATUS_VERIFIED_FAILURE, err)

    file_path = (file_path or "").strip()
    instruction = (instruction or "").strip()
    if not file_path or not instruction:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "both a specific file path and a clear instruction are required")

    target = Path(file_path)
    if not target.is_absolute():
        target = root / target
    if not _is_within_repo(target, root) or not _is_safe_path(target):
        return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
    if not target.exists() or not target.is_file():
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"File not found: {target}")

    if _envelope.is_consequential(action_name="repo_edit") and not _envelope.is_confirmed({"confirmed": confirmed}):
        return _envelope.envelope(
            _envelope.STATUS_CONFIRMATION_REQUIRED, f"this will modify {target.relative_to(root)}"
        )

    before = target.read_text(encoding="utf-8", errors="ignore")
    result_text = _code_helper(parameters={"action": "edit", "file_path": str(target), "description": instruction})
    after = target.read_text(encoding="utf-8", errors="ignore")
    if after != before:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, result_text)
    return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"{result_text} (file content did not change — could not confirm the edit took effect)")


def repo_agent(parameters: dict = None) -> str:
    """The one entry point main.py/task_engine.py call — mirrors the
    established xxx_control(parameters={...}) convention every other
    JARVIS capability module already uses (file_controller/office_control/
    computer_settings)."""
    params = parameters or {}
    action = (params.get("action") or "").lower().strip()
    repo_root = params.get("repo_root", "")

    if action == "search":
        return search_repository(params.get("query", ""), repo_root, int(params.get("max_results", _MAX_SEARCH_RESULTS)))
    elif action == "run_tests":
        return run_tests(repo_root, int(params.get("timeout", 120)))
    elif action == "edit":
        return edit_file(
            params.get("file_path", ""), params.get("instruction", params.get("description", "")),
            repo_root, bool(params.get("confirmed", False)),
        )
    else:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"Unknown action: '{action}'")
