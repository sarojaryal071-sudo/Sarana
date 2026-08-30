"""
tests/test_web_image_vision.py — web visual intelligence: a browser-
submitted image reaching SARANA's EXISTING Gemini Live session, via a new
ingress path only (see dashboard/server.py's /ws "image_command" handling
and main.py's _process_dashboard_image_commands()).

This deliberately does NOT touch, replace, or re-test desktop
screen_process/close_camera — those are proven untouched here by a source-
inspection regression check, not by re-running the whole vision flow.

Run with:
    .venv/Scripts/python.exe -m tests.test_web_image_vision
"""
import asyncio
import base64
import inspect
import io
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

import dashboard.server as server_module
from dashboard.server import DashboardServer
from core.headless_surface import HeadlessSurface
from main import JarvisLive, DESKTOP_ONLY_TOOLS


def _server_with_token(token: str) -> DashboardServer:
    server = DashboardServer()
    server._tokens.add(token)
    return server


def _real_jpeg_bytes(size=(16, 12)) -> bytes:
    """A genuinely decodable JPEG — built with PIL (already a project
    dependency, see actions/screen_processor.py) rather than embedding a
    binary fixture, so this test needs no extra file."""
    from PIL import Image
    img = Image.new("RGB", size, color=(30, 60, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ── 1: authenticated + valid submission is accepted ─────────────────────

def test_ws_image_command_authenticated_and_valid_is_queued() -> None:
    token  = "test-token-img-ok"
    server = _server_with_token(token)
    client = TestClient(server.app)
    raw    = _real_jpeg_bytes()
    b64    = base64.b64encode(raw).decode("ascii")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "image_command", "data": b64,
            "mime_type": "image/jpeg", "text": "what is this?",
        })
        time.sleep(0.05)

    assert not server._image_command_queue.empty(), "valid image should have been queued"
    item = server._image_command_queue.get_nowait()
    assert item["mime_type"] == "image/jpeg"
    assert item["text"] == "what is this?"
    assert item["data"] == raw, "queued bytes must be the exact decoded image, unmodified"
    print("test_ws_image_command_authenticated_and_valid_is_queued: PASS")


# ── 2: unauthenticated submission is rejected ────────────────────────────

def test_ws_image_command_unauthenticated_is_rejected() -> None:
    """No token registered server-side — /ws's own existing auth check
    (unchanged by this feature) must still refuse the connection itself
    before an image_command message could ever be sent."""
    server = DashboardServer()   # no _tokens.add() — nothing is valid here
    client = TestClient(server.app)

    rejected = False
    try:
        with client.websocket_connect("/ws?token=not-a-real-token"):
            pass
    except Exception:
        rejected = True
    assert rejected, "an unauthenticated /ws connection must never be established"
    assert server._image_command_queue.empty()
    print("test_ws_image_command_unauthenticated_is_rejected: PASS")


# ── 3: invalid MIME type is rejected ─────────────────────────────────────

def test_ws_image_command_rejects_unsupported_mime_type() -> None:
    token  = "test-token-img-badmime"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "image_command", "data": "AAAA",
            "mime_type": "application/pdf", "text": "x",
        })
        reply = ws.receive_json()

    assert reply["type"] == "image_error", reply
    assert server._image_command_queue.empty()
    print("test_ws_image_command_rejects_unsupported_mime_type: PASS")


# ── 4: oversized payload is rejected ─────────────────────────────────────

def test_ws_image_command_rejects_oversized_payload() -> None:
    token  = "test-token-img-big"
    server = _server_with_token(token)
    client = TestClient(server.app)
    huge_b64 = "A" * (server_module.MAX_IMAGE_B64_CHARS + 100)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "image_command", "data": huge_b64,
            "mime_type": "image/jpeg", "text": "x",
        })
        reply = ws.receive_json()

    assert reply["type"] == "image_error", reply
    assert server._image_command_queue.empty()
    print("test_ws_image_command_rejects_oversized_payload: PASS")


# ── extra: malformed/non-image bytes rejected even with a valid MIME ────

def test_ws_image_command_rejects_bytes_that_are_not_a_real_image() -> None:
    token  = "test-token-img-corrupt"
    server = _server_with_token(token)
    client = TestClient(server.app)
    junk_b64 = base64.b64encode(b"not actually image data, just junk").decode("ascii")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "image_command", "data": junk_b64,
            "mime_type": "image/jpeg", "text": "x",
        })
        reply = ws.receive_json()

    assert reply["type"] == "image_error", reply
    assert server._image_command_queue.empty()
    print("test_ws_image_command_rejects_bytes_that_are_not_a_real_image: PASS")


# ── 5/6/7: bytes reach the EXISTING Gemini session as inline_data,
#           question preserved, same session object reused ──────────────

class _FakeVisionSession:
    def __init__(self):
        self.calls = []

    async def send_client_content(self, turns, turn_complete=True):
        self.calls.append({"turns": turns, "turn_complete": turn_complete})


def test_image_command_reaches_existing_session_as_inline_data() -> None:
    async def _run():
        jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
        jarvis._dashboard = SimpleNamespace(_image_command_queue=asyncio.Queue())
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session   # the SAME live session — never replaced

        raw = _real_jpeg_bytes()
        await jarvis._dashboard._image_command_queue.put({
            "data": raw, "mime_type": "image/jpeg", "text": "What's in this image?",
        })

        task = asyncio.create_task(jarvis._process_dashboard_image_commands())
        try:
            for _ in range(60):
                if fake_session.calls:
                    break
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert len(fake_session.calls) == 1, "exactly one turn should have been sent"
        assert jarvis.session is fake_session, "no new/second Gemini session was created"

        turns = fake_session.calls[0]["turns"]
        parts = turns["parts"]
        assert "inline_data" in parts[0], parts
        assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
        assert len(parts[0]["inline_data"]["data"]) > 0   # real base64 image data
        assert parts[1]["text"] == "What's in this image?"   # question preserved verbatim
        assert fake_session.calls[0]["turn_complete"] is True

    asyncio.run(_run())
    print("test_image_command_reaches_existing_session_as_inline_data: PASS")


# ── 8/9: desktop screen_process/close_camera untouched ──────────────────

def test_screen_process_and_close_camera_remain_desktop_only() -> None:
    assert "screen_process" in DESKTOP_ONLY_TOOLS
    assert "close_camera" in DESKTOP_ONLY_TOOLS
    print("test_screen_process_and_close_camera_remain_desktop_only: PASS")


def test_desktop_vision_tool_handler_source_unchanged() -> None:
    """Source-inspection regression check (same technique as
    frontend/src/lib/permissions.test.mjs's own source-grep test) — proves
    the web image ingress is a genuinely SEPARATE path, not a rewrite of
    the desktop screen_process handler: the existing capture calls and
    _pending_vision handoff must still be exactly what they were."""
    import main as main_module
    src = inspect.getsource(main_module.JarvisLive._execute_tool)
    assert "_capture_camera" in src
    assert "_capture_screen" in src
    assert "self._pending_vision = (img_b, mime_t, user_text, angle)" in src
    print("test_desktop_vision_tool_handler_source_unchanged: PASS")


# ── 10: temporary image data is not unnecessarily persisted ─────────────

def test_image_command_processing_never_writes_a_file() -> None:
    """Static check: the consumer that turns a queued image into a Gemini
    turn contains no filesystem write of any kind — bytes flow browser ->
    queue -> compress (in memory) -> base64 -> Gemini, and are discarded
    once the turn is sent, exactly like the desktop capture path's own
    self._pending_vision (never written to disk either)."""
    import main as main_module
    src = inspect.getsource(main_module.JarvisLive._process_dashboard_image_commands)
    for banned in ("open(", ".write(", "write_bytes", "NamedTemporaryFile", "tempfile"):
        assert banned not in src, f"unexpected filesystem write pattern found: {banned!r}"
    print("test_image_command_processing_never_writes_a_file: PASS")


if __name__ == "__main__":
    test_ws_image_command_authenticated_and_valid_is_queued()
    test_ws_image_command_unauthenticated_is_rejected()
    test_ws_image_command_rejects_unsupported_mime_type()
    test_ws_image_command_rejects_oversized_payload()
    test_ws_image_command_rejects_bytes_that_are_not_a_real_image()
    test_image_command_reaches_existing_session_as_inline_data()
    test_screen_process_and_close_camera_remain_desktop_only()
    test_desktop_vision_tool_handler_source_unchanged()
    test_image_command_processing_never_writes_a_file()
    print("\nAll web-image-vision tests passed.")
