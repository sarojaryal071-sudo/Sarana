"""
tests/test_dotenv_loading.py -- the actual root cause behind "Google
Calendar is connected but JARVIS says it isn't" on a desktop/local run:
nothing in this codebase ever loaded a `.env` file, despite .env.example
documenting one. main.py now calls python-dotenv's load_dotenv() at
import time (see main.py's own "## .env loading" comment block) -- these
tests prove it via a REAL subprocess importing the REAL main.py against a
REAL .env file on disk, not a mock, since the whole point is verifying the
actual load happens at the actual place a real desktop process would hit
it.

Run with:
    .venv/Scripts/python.exe -m tests.test_dotenv_loading
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def _run_against_env_file(env_contents: str | None) -> str:
    """Spawns a fresh Python process, cwd'd at a temp directory that
    optionally has its own .env, importing main.py from the real repo
    (via sys.path) and printing whatever DATABASE_URL ends up in
    os.environ. A real subprocess (not importlib.reload) because
    load_dotenv() only runs once, at true module-import time -- exactly
    what a real `python ui.py`/`python server_main.py` launch does."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        if env_contents is not None:
            (tmp_path / ".env").write_text(env_contents, encoding="utf-8")
        script = (
            "import os, sys; "
            f"sys.path.insert(0, {str(REPO_ROOT)!r}); "
            "import main; "
            "print('DATABASE_URL=' + os.environ.get('DATABASE_URL', '<unset>'))"
        )
        env = dict(os.environ)
        env.pop("DATABASE_URL", None)   # never let the real dev shell's own value leak in
        result = subprocess.run(
            [PYTHON, "-c", script],
            cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"subprocess failed: {result.stderr[-2000:]}"
        for line in result.stdout.splitlines():
            if line.startswith("DATABASE_URL="):
                return line[len("DATABASE_URL="):]
        raise AssertionError(f"no DATABASE_URL line in output: {result.stdout!r} / {result.stderr[-2000:]}")


def test_local_env_file_is_actually_loaded() -> None:
    """The real fix: a .env file sitting next to where the process runs
    (cwd) is picked up by main.py's own load_dotenv() call, making
    DATABASE_URL (and by the same mechanism GOOGLE_CLIENT_ID/_SECRET/
    _REDIRECT_URI) visible to calendar_store.is_configured()/
    calendar_auth.is_configured() without needing a real OS-level env var
    set by hand."""
    value = _run_against_env_file("DATABASE_URL=postgresql://fake-test-value/db\n")
    assert value == "postgresql://fake-test-value/db"
    print("test_local_env_file_is_actually_loaded: PASS")


def test_no_env_file_is_a_harmless_no_op() -> None:
    """No .env present (today's exact prior behavior, and Render's own
    deployment shape, which never ships a .env file) must not raise or
    otherwise change anything -- DATABASE_URL simply stays unset."""
    value = _run_against_env_file(None)
    assert value == "<unset>"
    print("test_no_env_file_is_a_harmless_no_op: PASS")


def test_real_os_env_var_always_wins_over_env_file() -> None:
    """override=False (load_dotenv()'s own default) -- a real platform-
    injected env var (e.g. Render's own DATABASE_URL) must never be
    clobbered by whatever a stray local .env file says."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / ".env").write_text("DATABASE_URL=postgresql://from-dotenv/db\n", encoding="utf-8")
        script = (
            "import os, sys; "
            f"sys.path.insert(0, {str(REPO_ROOT)!r}); "
            "import main; "
            "print('DATABASE_URL=' + os.environ.get('DATABASE_URL', '<unset>'))"
        )
        env = dict(os.environ)
        env["DATABASE_URL"] = "postgresql://real-platform-value/db"
        result = subprocess.run(
            [PYTHON, "-c", script],
            cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"subprocess failed: {result.stderr[-2000:]}"
        assert "DATABASE_URL=postgresql://real-platform-value/db" in result.stdout
    print("test_real_os_env_var_always_wins_over_env_file: PASS")


if __name__ == "__main__":
    test_local_env_file_is_actually_loaded()
    test_no_env_file_is_a_harmless_no_op()
    test_real_os_env_var_always_wins_over_env_file()
    print("\nAll dotenv-loading tests passed.")
