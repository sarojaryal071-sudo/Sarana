"""
tests/test_login_greeting.py — Priority 1 regression tests, requested
explicitly: proving the full login -> Gemini connection -> startup
greeting lifecycle actually fires, end to end, through the real
dashboard/server.py HTTP routes and main.py's run() loop (not just the
individual unit-level pieces already covered elsewhere).

Reuses tests/test_voice_reconnect.py's fake Gemini Live session harness
(_FakeGenaiClient/_FakeLive/_FakeConnectCM/_FakeLiveSession) rather than
duplicating it -- same reasoning as that file's own docstring: a real
async-context-manager-shaped fake is needed to let the TaskGroup actually
stay alive long enough to observe the greeting, and one already exists.

Run with:
    .venv/Scripts/python.exe -m tests.test_login_greeting
"""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import main
from core.headless_surface import HeadlessSurface
from dashboard.server import DashboardServer
from main import JarvisLive
from users import user_db
from tests.test_voice_reconnect import _FakeGenaiClient


def _isolated_desktop_config(tmp_dir: str, user_name: str) -> Path:
    cfg_path = Path(tmp_dir) / "api_keys.json"
    cfg_path.write_text(
        json.dumps({"gemini_api_key": "x", "assistant_name": "SARANA", "user_name": user_name}),
        encoding="utf-8",
    )
    return cfg_path


def test_successful_web_login_triggers_startup_briefing() -> None:
    """The exact flow requested: real POST /login/username -> real
    set_profile_callback/set_username_callback/set_wake_callback wiring
    (as run() actually sets it up) -> the auto_start=False gate releases
    -> a fresh Gemini connection is made -> the startup briefing fires."""
    async def _run():
        recorder = []
        sent_messages = []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = Path(tmp.name) / "sarana.db"
            with patch.object(user_db, "DB_PATH", db_path):
                server = DashboardServer()
            client_http = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(server.app)

            jarvis = JarvisLive(HeadlessSurface(), auto_start=False)

            with patch("main.genai.Client", side_effect=make_client), \
                 patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
                 patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")):
                # This is run()'s own wiring, reproduced exactly as it does
                # it (dashboard.server would normally be constructed
                # inside run(); here it's pre-built above so the test can
                # isolate its SQLite DB — the callback wiring itself is
                # identical to what run() performs).
                with patch("dashboard.server.DashboardServer", return_value=server):
                    task = asyncio.create_task(jarvis.run())
                    await asyncio.sleep(0.2)

                    assert len(recorder) == 0, "must not connect before login/wake"

                    resp = client_http.post(
                        "/login/username", json={"username": "Saroj", "pin": "2057"}
                    )
                    assert resp.status_code == 200, resp.text

                    await asyncio.sleep(0.6)

                    assert len(recorder) == 1, "login must trigger exactly one Gemini connection"
                    assert len(sent_messages) == 1, (
                        "successful login -> Gemini connection must trigger the startup briefing"
                    )
                    assert "Saroj" in sent_messages[0]

                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            tmp.cleanup()

    asyncio.run(_run())
    print("test_successful_web_login_triggers_startup_briefing: PASS")


def test_logout_then_different_user_login_produces_new_greeting() -> None:
    """Saroj logs in (greeting #1) -> logs out -> Bandana logs in on the
    same running process (greeting #2, personalized to Saanaa/Kanha)."""
    async def _run():
        recorder = []
        sent_messages = []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = Path(tmp.name) / "sarana.db"
            with patch.object(user_db, "DB_PATH", db_path):
                server = DashboardServer()
            client_http = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(server.app)

            jarvis = JarvisLive(HeadlessSurface(), auto_start=False)

            with patch("main.genai.Client", side_effect=make_client), \
                 patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
                 patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")), \
                 patch("dashboard.server.DashboardServer", return_value=server):
                task = asyncio.create_task(jarvis.run())
                await asyncio.sleep(0.2)

                resp1 = client_http.post("/login/username", json={"username": "Saroj", "pin": "2057"})
                token1 = resp1.json()["token"]
                await asyncio.sleep(0.6)
                assert len(sent_messages) == 1

                client_http.post("/api/logout", headers={"Authorization": f"Bearer {token1}"})

                resp2 = client_http.post("/login/username", json={"username": "Bandana", "pin": "2060"})
                assert resp2.status_code == 200
                assert resp2.json()["username"] == "Saanaa"
                await asyncio.sleep(0.8)

                assert len(sent_messages) == 2, "the new account's own login must produce its own greeting"
                assert "Saanaa" in sent_messages[1]
                assert jarvis._user_profile["assistant_name"] == "Kanha"

                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            tmp.cleanup()

    asyncio.run(_run())
    print("test_logout_then_different_user_login_produces_new_greeting: PASS")


def test_desktop_login_still_triggers_startup_briefing() -> None:
    """Desktop (auto_start=True) regression companion — the _briefing_sent
    once-per-process path must still fire normally."""
    async def _run():
        recorder = []
        sent_messages = []

        def make_client(*a, **kw):
            return _FakeGenaiClient(recorder, sent_messages, *a, **kw)

        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = Path(tmp.name) / "sarana.db"
            cfg_path = _isolated_desktop_config(tmp.name, "Saroj")
            with patch.object(user_db, "DB_PATH", db_path):
                user_db.init_db()

            jarvis = JarvisLive(HeadlessSurface())  # auto_start=True

            with patch.object(main, "API_CONFIG_PATH", cfg_path), \
                 patch.object(user_db, "DB_PATH", db_path), \
                 patch("main.genai.Client", side_effect=make_client), \
                 patch("main.sd.InputStream", side_effect=OSError("no audio device in test")), \
                 patch("main.sd.RawOutputStream", side_effect=OSError("no audio device in test")), \
                 patch("dashboard.server.DashboardServer", side_effect=lambda: DashboardServer()):
                task = asyncio.create_task(jarvis.run())
                await asyncio.sleep(0.6)

                assert len(sent_messages) == 1
                assert "Saroj" in sent_messages[0]

                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            tmp.cleanup()

    asyncio.run(_run())
    print("test_desktop_login_still_triggers_startup_briefing: PASS")


if __name__ == "__main__":
    test_successful_web_login_triggers_startup_briefing()
    test_logout_then_different_user_login_produces_new_greeting()
    test_desktop_login_still_triggers_startup_briefing()
    print("\nAll login-greeting lifecycle tests passed.")
