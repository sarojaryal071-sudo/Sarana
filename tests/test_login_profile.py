"""
tests/test_login_profile.py — HTTP + session-integration tests for the
SQLite user/profile system: POST /login/username end-to-end (real DB,
real FastAPI route), the authenticated profile reaching JarvisLive's
_build_config() as structured [USER PROFILE] context, and the security
requirements (pin_hash/full profile never reach the client).

Every test isolates its own temp SQLite file via patch.object(user_db,
"DB_PATH", ...) BEFORE constructing DashboardServer() (whose __init__
calls user_db.init_db()), so nothing here touches the real data/sarana.db
and every test starts from a freshly seeded, known state.

Run with:
    .venv/Scripts/python.exe -m tests.test_login_profile
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer
from core.headless_surface import HeadlessSurface
from main import JarvisLive
from users import user_db


def _isolated_server():
    """Returns (tempdir, server, client) with a freshly seeded temp DB.
    Caller must keep `tempdir` alive (don't let it get garbage collected)
    for as long as `server`/`client` are used."""
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "sarana.db"
    with patch.object(user_db, "DB_PATH", db_path):
        server = DashboardServer()
    return tmp, server, TestClient(server.app)


# ── end-to-end login: real DB, real route ─────────────────────────────────

def test_saanaa_alias_login_end_to_end() -> None:
    tmp, server, client = _isolated_server()
    with tmp:
        resp = client.post("/login/username", json={"username": "Bandana", "pin": "2060"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["username"] == "Saanaa"   # pronunciation wins as the display name
        assert body["token"]
    print("test_saanaa_alias_login_end_to_end: PASS")


def test_saroj_login_end_to_end() -> None:
    tmp, server, client = _isolated_server()
    with tmp:
        resp = client.post("/login/username", json={"username": "Saroj", "pin": "2057"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["username"] == "Saroj"
    print("test_saroj_login_end_to_end: PASS")


def test_login_token_works_on_ws_end_to_end() -> None:
    """Existing session lifecycle (token issuance -> /ws auth) is unbroken
    by requiring a PIN now."""
    tmp, server, client = _isolated_server()
    with tmp:
        token = client.post(
            "/login/username", json={"username": "Saroj", "pin": "2057"}
        ).json()["token"]
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_json({"type": "command", "text": "hello"})
        assert token in server._tokens
    print("test_login_token_works_on_ws_end_to_end: PASS")


# ── security: never leak pin_hash / full profile / user table ─────────────

def test_login_response_never_contains_pin_hash_or_profile_fields() -> None:
    tmp, server, client = _isolated_server()
    with tmp:
        resp = client.post("/login/username", json={"username": "Sana", "pin": "2060"})
        body = resp.json()
        assert set(body.keys()) == {"ok", "token", "username"}, body.keys()
        raw = resp.text
        assert "pin_hash" not in raw
        assert "2060" not in raw
        assert "gender" not in raw
        assert "voice_preference" not in raw
    print("test_login_response_never_contains_pin_hash_or_profile_fields: PASS")


def test_no_route_exposes_the_full_user_table() -> None:
    tmp, server, client = _isolated_server()
    with tmp:
        for guess in ("/api/users", "/users", "/api/user", "/api/profiles"):
            resp = client.get(guess)
            assert resp.status_code == 404, f"{guess} unexpectedly exists"
    print("test_no_route_exposes_the_full_user_table: PASS")


def test_invalid_login_error_is_generic() -> None:
    tmp, server, client = _isolated_server()
    with tmp:
        unknown = client.post("/login/username", json={"username": "NobodyHere", "pin": "0000"})
        wrong_pin = client.post("/login/username", json={"username": "Saroj", "pin": "0000"})
        assert unknown.status_code == wrong_pin.status_code == 401
        assert unknown.json()["error"] == wrong_pin.json()["error"], (
            "an unknown username and a wrong PIN must be indistinguishable to the client"
        )
    print("test_invalid_login_error_is_generic: PASS")


# ── profile reaches JarvisLive as structured [USER PROFILE] context ──────

def test_saanaa_profile_reaches_build_config_as_structured_context() -> None:
    tmp, db_path = tempfile.TemporaryDirectory(), None
    with tmp as tmpdir:
        db_path = Path(tmpdir) / "sarana.db"
        with patch.object(user_db, "DB_PATH", db_path):
            user_db.init_db()
            profile = user_db.authenticate("Bandana", "2060")

        jarvis = JarvisLive(HeadlessSurface())
        jarvis._set_web_profile(profile)
        jarvis._set_web_username(profile["pronunciation"] or profile["nickname"])
        config = jarvis._build_config()
        instr = config.system_instruction

        assert "[USER PROFILE]" in instr
        assert "Nickname: Sana" in instr
        assert "Pronunciation: Saanaa" in instr
        assert "Gender: female" in instr
        assert "Assistant name: Kanha" in instr
        assert "Voice preference: Male" in instr
        assert "Language preference: Nepali" in instr
        # Assistant identity itself is personalized, not hardcoded elsewhere.
        assert "Your name is Kanha" in instr
        # Voice actually wired into the Gemini Live config, not just text.
        assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Charon"
    print("test_saanaa_profile_reaches_build_config_as_structured_context: PASS")


def test_saroj_profile_reaches_build_config_as_structured_context() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sarana.db"
        with patch.object(user_db, "DB_PATH", db_path):
            user_db.init_db()
            profile = user_db.authenticate("Saroj", "2057")

        jarvis = JarvisLive(HeadlessSurface())
        jarvis._set_web_profile(profile)
        jarvis._set_web_username(profile["nickname"])
        config = jarvis._build_config()
        instr = config.system_instruction

        assert "[USER PROFILE]" in instr
        assert "Nickname: Saroj" in instr
        assert "Pronunciation:" not in instr   # Saroj's profile has none — no empty line
        assert "Gender: male" in instr
        assert "Assistant name: Sara" in instr
        assert "Voice preference: Female" in instr
        assert "Language preference: Nepali" in instr
        assert "Your name is Sara" in instr
        assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    print("test_saroj_profile_reaches_build_config_as_structured_context: PASS")


def test_no_profile_desktop_session_has_no_user_profile_block() -> None:
    """Desktop / an unauthenticated web session: no [USER PROFILE] section,
    no assistant-name override, default voice — exactly as before this
    feature existed."""
    jarvis = JarvisLive(HeadlessSurface())
    assert jarvis._web_profile is None
    config = jarvis._build_config()
    assert "[USER PROFILE]" not in config.system_instruction
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Charon"
    print("test_no_profile_desktop_session_has_no_user_profile_block: PASS")


def test_login_wires_profile_into_full_run_lifecycle() -> None:
    """End-to-end through dashboard/server.py's actual callback wiring
    (set_profile_callback), not by calling _set_web_profile() directly."""
    import asyncio

    async def _run():
        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = Path(tmp.name) / "sarana.db"
            with patch.object(user_db, "DB_PATH", db_path):
                server = DashboardServer()
            client = TestClient(server.app)

            jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
            server.set_profile_callback(jarvis._set_web_profile)
            server.set_username_callback(jarvis._set_web_username)

            resp = client.post("/login/username", json={"username": "Radhe", "pin": "2060"})
            assert resp.status_code == 200

            assert jarvis._web_profile is not None
            assert jarvis._web_profile["assistant_name"] == "Kanha"
            assert jarvis._web_user_name == "Saanaa"
        finally:
            tmp.cleanup()

    asyncio.run(_run())
    print("test_login_wires_profile_into_full_run_lifecycle: PASS")


if __name__ == "__main__":
    test_saanaa_alias_login_end_to_end()
    test_saroj_login_end_to_end()
    test_login_token_works_on_ws_end_to_end()
    test_login_response_never_contains_pin_hash_or_profile_fields()
    test_no_route_exposes_the_full_user_table()
    test_invalid_login_error_is_generic()
    test_saanaa_profile_reaches_build_config_as_structured_context()
    test_saroj_profile_reaches_build_config_as_structured_context()
    test_no_profile_desktop_session_has_no_user_profile_block()
    test_login_wires_profile_into_full_run_lifecycle()
    print("\nAll login/profile integration tests passed.")
