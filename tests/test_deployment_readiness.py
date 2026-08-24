"""
tests/test_deployment_readiness.py — focused tests for the Render/Vercel
deployment-readiness changes: GEMINI_API_KEY env-var priority, PORT
env-var support, and headless-safe local audio (no crash/no TaskGroup
re-raise when there's no microphone/speaker).

Run with:
    .venv/Scripts/python.exe -m tests.test_deployment_readiness
"""
import asyncio
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from core.headless_surface import HeadlessSurface
import main
from main import JarvisLive


# ── GEMINI_API_KEY environment variable priority ─────────────────────────

def test_env_var_takes_priority_over_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "api_keys.json"
        cfg_path.write_text(json.dumps({"gemini_api_key": "from-json-file"}), encoding="utf-8")

        with patch.object(main, "API_CONFIG_PATH", cfg_path), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "from-env-var"}):
            assert main._get_api_key() == "from-env-var"
    print("test_env_var_takes_priority_over_json: PASS")


def test_json_fallback_still_works_without_env_var() -> None:
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "api_keys.json"
        cfg_path.write_text(json.dumps({"gemini_api_key": "from-json-file"}), encoding="utf-8")

        env_without_key = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with patch.object(main, "API_CONFIG_PATH", cfg_path), \
             patch.dict(os.environ, env_without_key, clear=True):
            assert main._get_api_key() == "from-json-file"
    print("test_json_fallback_still_works_without_env_var: PASS")


def test_empty_env_var_falls_back_to_json() -> None:
    """An empty string is falsy — must not be treated as "set"."""
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "api_keys.json"
        cfg_path.write_text(json.dumps({"gemini_api_key": "from-json-file"}), encoding="utf-8")

        with patch.object(main, "API_CONFIG_PATH", cfg_path), \
             patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            assert main._get_api_key() == "from-json-file"
    print("test_empty_env_var_falls_back_to_json: PASS")


# ── PORT environment variable ─────────────────────────────────────────────

def test_port_defaults_to_8000_without_env_var() -> None:
    env_without_port = {k: v for k, v in os.environ.items() if k != "PORT"}
    with patch.dict(os.environ, env_without_port, clear=True):
        import dashboard.server as server_module
        importlib.reload(server_module)
        try:
            assert server_module.PORT == 8000, server_module.PORT
        finally:
            importlib.reload(server_module)  # leave the shared module in its default state
    print("test_port_defaults_to_8000_without_env_var: PASS")


def test_port_respects_env_var() -> None:
    with patch.dict(os.environ, {"PORT": "10000"}):
        import dashboard.server as server_module
        importlib.reload(server_module)
        try:
            assert server_module.PORT == 10000, server_module.PORT
        finally:
            pass
    # Restore the module to its default (no PORT set) state for any test
    # that runs after this one in the same process.
    env_without_port = {k: v for k, v in os.environ.items() if k != "PORT"}
    with patch.dict(os.environ, env_without_port, clear=True):
        importlib.reload(server_module)
        assert server_module.PORT == 8000
    print("test_port_respects_env_var: PASS")


# ── headless-safe local audio ─────────────────────────────────────────────

def test_listen_audio_returns_cleanly_without_local_mic() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        with patch("main.sd.InputStream", side_effect=OSError("no such audio device")):
            # Must return promptly (not hang in the while-True loop) and
            # must NOT raise — a raise here would cancel every sibling task
            # in run()'s TaskGroup, including _play_audio()'s browser feed.
            await asyncio.wait_for(jarvis._listen_audio(), timeout=2)
    asyncio.run(_run())
    print("test_listen_audio_returns_cleanly_without_local_mic: PASS")


def test_play_audio_continues_broadcasting_without_local_speaker() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.audio_in_queue = asyncio.Queue()

        broadcasts = []

        class _FakeDashboard:
            async def broadcast_audio(self, chunk):
                broadcasts.append(chunk)

        jarvis._dashboard = _FakeDashboard()

        with patch("main.sd.RawOutputStream", side_effect=OSError("no such audio device")):
            await jarvis.audio_in_queue.put(b"\x00\x01" * 100)
            task = asyncio.create_task(jarvis._play_audio())
            await asyncio.sleep(0.3)
            task.cancel()
            results = await asyncio.gather(task, return_exceptions=True)

        result = results[0]
        if result is not None:
            assert isinstance(result, asyncio.CancelledError), (
                f"_play_audio() must not raise when there is no local speaker — got {result!r}"
            )
        assert broadcasts, (
            "audio must still reach the browser via broadcast_audio() even with no local speaker"
        )

    asyncio.run(_run())
    print("test_play_audio_continues_broadcasting_without_local_speaker: PASS")


def test_headless_still_no_pyqt6() -> None:
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis is not None
    leaked = [m for m in sys.modules if m == "PyQt6" or m.startswith("PyQt6.")]
    assert not leaked, f"PyQt6 modules leaked into sys.modules: {leaked}"
    print("test_headless_still_no_pyqt6: PASS — sys.modules has no PyQt6 entries")


# ── PortAudio-not-installed (Render): the import itself, not just stream
# construction, must never crash — see main.py's guarded `import
# sounddevice as sd` / `sd = None` fallback. ──────────────────────────────

def test_listen_audio_returns_cleanly_when_sd_is_none() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        with patch.object(main, "sd", None):
            await asyncio.wait_for(jarvis._listen_audio(), timeout=2)
    asyncio.run(_run())
    print("test_listen_audio_returns_cleanly_when_sd_is_none: PASS")


def test_play_audio_continues_broadcasting_when_sd_is_none() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface())
        jarvis.audio_in_queue = asyncio.Queue()
        broadcasts = []

        class _FakeDashboard:
            async def broadcast_audio(self, chunk):
                broadcasts.append(chunk)

        jarvis._dashboard = _FakeDashboard()

        with patch.object(main, "sd", None):
            await jarvis.audio_in_queue.put(b"\x00\x01" * 100)
            task = asyncio.create_task(jarvis._play_audio())
            await asyncio.sleep(0.3)
            task.cancel()
            results = await asyncio.gather(task, return_exceptions=True)

        result = results[0]
        if result is not None:
            assert isinstance(result, asyncio.CancelledError), (
                f"_play_audio() must not raise when sd is None — got {result!r}"
            )
        assert broadcasts, (
            "audio must still reach the browser via broadcast_audio() even when sd is None"
        )

    asyncio.run(_run())
    print("test_play_audio_continues_broadcasting_when_sd_is_none: PASS")


def test_main_module_imports_with_sounddevice_unavailable() -> None:
    """The literal repro from the PortAudio bug report: `from main import
    JarvisLive` must succeed even when sounddevice/PortAudio can't be
    imported at all (OSError at import time), not just when a stream
    later fails to open. Uses a real subprocess with a fake sounddevice
    module shadowing the real one via PYTHONPATH — the most faithful
    simulation of Render's actual failure short of removing PortAudio
    from this machine.
    """
    import subprocess

    with tempfile.TemporaryDirectory() as fake_dir:
        Path(fake_dir, "sounddevice.py").write_text(
            'raise OSError("PortAudio library not found")\n', encoding="utf-8"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = fake_dir + os.pathsep + env.get("PYTHONPATH", "")
        repo_root = Path(__file__).resolve().parent.parent

        result = subprocess.run(
            [sys.executable, "-c", "from main import JarvisLive; print('IMPORT_OK')"],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"stderr:\n{result.stderr}"
        assert "IMPORT_OK" in result.stdout, result.stdout

    print("test_main_module_imports_with_sounddevice_unavailable: PASS")


if __name__ == "__main__":
    test_env_var_takes_priority_over_json()
    test_json_fallback_still_works_without_env_var()
    test_empty_env_var_falls_back_to_json()
    test_port_defaults_to_8000_without_env_var()
    test_port_respects_env_var()
    test_listen_audio_returns_cleanly_without_local_mic()
    test_play_audio_continues_broadcasting_without_local_speaker()
    test_listen_audio_returns_cleanly_when_sd_is_none()
    test_play_audio_continues_broadcasting_when_sd_is_none()
    test_main_module_imports_with_sounddevice_unavailable()
    test_headless_still_no_pyqt6()
    print("\nAll deployment-readiness tests passed.")
