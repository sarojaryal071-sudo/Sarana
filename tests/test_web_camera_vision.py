"""
tests/test_web_camera_vision.py — SARANA Web Live Camera Vision: Gemini
autonomously requesting a LIVE look through the browser's own camera (as
opposed to a user-initiated upload — see tests/test_web_image_vision.py —
or desktop's OS-level screen_process/close_camera, untouched by this
feature).

Covers: the web_camera_vision tool (open/continue a session, desktop
gating, cooldown), the "vision_frame"/"vision_control" WebSocket ingress
(dashboard/server.py), and the adaptive observation-burst consumer
(main.py's _process_web_vision_frames()) — batching, stale-request
rejection, the no-frames/hard-timeout/grace-period end conditions, and
non-persistence.

Run with:
    .venv/Scripts/python.exe -m tests.test_web_camera_vision
"""
import asyncio
import base64
import inspect
import io
import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard.server as server_module
from dashboard.server import DashboardServer
from core.headless_surface import HeadlessSurface
from main import (
    JarvisLive, DESKTOP_ONLY_TOOLS, WEB_ONLY_TOOLS, TOOL_DECLARATIONS,
    WEB_VISION_BURST_MAX_FRAMES,
)


def _server_with_token(token: str) -> DashboardServer:
    server = DashboardServer()
    server._tokens.add(token)
    return server


def _real_jpeg_bytes(size=(16, 12)) -> bytes:
    from PIL import Image
    img = Image.new("RGB", size, color=(30, 60, 90))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _fc(name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(id="fc-1", name=name, args=args)


class _FakeVisionSession:
    def __init__(self):
        self.calls = []

    async def send_client_content(self, turns, turn_complete=True):
        self.calls.append({"turns": turns, "turn_complete": turn_complete})


class _RecordingDashboard:
    """Fake dashboard: records broadcast_camera_vision_request/_stop calls
    instead of a real WebSocket fan-out; carries a real asyncio.Queue for
    _vision_frame_queue so main.py's consumer can be driven directly."""

    def __init__(self):
        self._vision_frame_queue = asyncio.Queue()
        self.requests = []   # (request_id, facing)
        self.stops = []      # request_id
        self.sys_messages = []   # diagnostic "sys" broadcasts (see main.py's
                                  # VISION_REQUESTED/VISION_FRAME_RECEIVED notes)

    async def broadcast_camera_vision_request(self, request_id, facing):
        self.requests.append((request_id, facing))

    async def broadcast_camera_vision_stop(self, request_id):
        self.stops.append(request_id)

    async def broadcast(self, msg):
        if msg.get("type") == "sys":
            self.sys_messages.append(msg.get("text", ""))


def _jarvis(auto_start=False) -> JarvisLive:
    j = JarvisLive(HeadlessSurface(), auto_start=auto_start)
    j._dashboard = _RecordingDashboard()
    return j


async def _run_frames_tick(jarvis, iterations=40, delay=0.05):
    """Runs _process_web_vision_frames() briefly, polling until the given
    predicate-free budget is exhausted or the caller's own loop breaks it
    (mirrors tests/test_web_image_vision.py's own create-task/poll/cancel
    pattern)."""
    task = asyncio.create_task(jarvis._process_web_vision_frames())
    try:
        for _ in range(iterations):
            await asyncio.sleep(delay)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ── tool: desktop gating ──────────────────────────────────────────────────

def test_web_camera_vision_is_web_only_and_never_desktop_only() -> None:
    assert "web_camera_vision" in WEB_ONLY_TOOLS
    assert "web_camera_vision" not in DESKTOP_ONLY_TOOLS
    print("test_web_camera_vision_is_web_only_and_never_desktop_only: PASS")


def test_web_camera_vision_blocked_on_desktop_with_screen_process_redirect() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=True)   # desktop
        fr = await jarvis._execute_tool(_fc("web_camera_vision", text="what is this"))
        result = fr.response["result"]
        assert "[CAPABILITY_UNAVAILABLE]" in result
        assert "screen_process" in result
        assert jarvis._web_vision_session is None, "desktop must never open a web vision session"
        assert not jarvis._dashboard.requests, "desktop must never broadcast a camera request"
    asyncio.run(_run())
    print("test_web_camera_vision_blocked_on_desktop_with_screen_process_redirect: PASS")


# ── tool: opening / continuing a session ────────────────────────────────

def test_web_camera_vision_opens_session_and_broadcasts_request() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)   # web
        fr = await jarvis._execute_tool(_fc("web_camera_vision", text="what's in my hand", facing="environment"))
        await asyncio.sleep(0.05)   # let the fire-and-forget broadcast task actually run
        result = fr.response["result"]
        assert "[VISION_ACTIVE]" in result
        assert jarvis._web_vision_session is not None
        assert jarvis._web_vision_session["text"] == "what's in my hand"
        assert jarvis._web_vision_session["awaiting_burst"] is True
        assert len(jarvis._dashboard.requests) == 1
        req_id, facing = jarvis._dashboard.requests[0]
        assert req_id == jarvis._web_vision_session["request_id"]
        assert facing == "environment"
    asyncio.run(_run())
    print("test_web_camera_vision_opens_session_and_broadcasts_request: PASS")


def test_web_camera_vision_repeat_call_continues_without_rebroadcasting() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        await jarvis._execute_tool(_fc("web_camera_vision", text="what's in my hand"))
        await asyncio.sleep(0.05)
        first_id = jarvis._web_vision_session["request_id"]
        jarvis._web_vision_last_call = 0.0   # bypass the cooldown guard for this test

        fr2 = await jarvis._execute_tool(_fc("web_camera_vision"))
        await asyncio.sleep(0.05)
        result2 = fr2.response["result"]
        assert "already open" in result2
        assert jarvis._web_vision_session["request_id"] == first_id, "must continue the SAME session, not open a new one"
        assert len(jarvis._dashboard.requests) == 1, "a continuation must never re-broadcast camera_vision_request"
        assert jarvis._web_vision_session["awaiting_burst"] is True
    asyncio.run(_run())
    print("test_web_camera_vision_repeat_call_continues_without_rebroadcasting: PASS")


def test_web_camera_vision_cooldown_blocks_rapid_double_call() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        await jarvis._execute_tool(_fc("web_camera_vision", text="x"))
        fr2 = await jarvis._execute_tool(_fc("web_camera_vision", text="y"))
        await asyncio.sleep(0.05)
        assert "wait a moment" in fr2.response["result"]
        assert len(jarvis._dashboard.requests) == 1, "the cooldown-blocked call must not touch the session at all"
    asyncio.run(_run())
    print("test_web_camera_vision_cooldown_blocks_rapid_double_call: PASS")


# ── WebSocket ingress: vision_frame / vision_control ─────────────────────

def test_ws_vision_frame_authenticated_and_valid_is_queued() -> None:
    token  = "test-token-vf-ok"
    server = _server_with_token(token)
    client = TestClient(server.app)
    raw    = _real_jpeg_bytes()
    b64    = base64.b64encode(raw).decode("ascii")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "vision_frame", "request_id": "req-1", "seq": 0,
            "mime_type": "image/jpeg", "data": b64,
        })
        time.sleep(0.05)

    assert not server._vision_frame_queue.empty()
    item = server._vision_frame_queue.get_nowait()
    assert item["request_id"] == "req-1"
    assert item["mime_type"] == "image/jpeg"
    assert item["data"] == raw
    print("test_ws_vision_frame_authenticated_and_valid_is_queued: PASS")


def test_ws_vision_frame_missing_request_id_rejected() -> None:
    token  = "test-token-vf-noreq"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "vision_frame", "mime_type": "image/jpeg",
            "data": base64.b64encode(_real_jpeg_bytes()).decode("ascii"),
        })
        reply = ws.receive_json()

    assert reply["type"] == "vision_error", reply
    assert server._vision_frame_queue.empty()
    print("test_ws_vision_frame_missing_request_id_rejected: PASS")


def test_ws_vision_frame_rejects_unsupported_mime_type() -> None:
    token  = "test-token-vf-badmime"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "vision_frame", "request_id": "req-1",
            "mime_type": "application/pdf", "data": "AAAA",
        })
        reply = ws.receive_json()

    assert reply["type"] == "vision_error", reply
    assert server._vision_frame_queue.empty()
    print("test_ws_vision_frame_rejects_unsupported_mime_type: PASS")


def test_ws_vision_frame_rejects_oversized_payload() -> None:
    token  = "test-token-vf-big"
    server = _server_with_token(token)
    client = TestClient(server.app)
    huge_b64 = "A" * (server_module.MAX_IMAGE_B64_CHARS + 100)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "vision_frame", "request_id": "req-1",
            "mime_type": "image/jpeg", "data": huge_b64,
        })
        reply = ws.receive_json()

    assert reply["type"] == "vision_error", reply
    assert server._vision_frame_queue.empty()
    print("test_ws_vision_frame_rejects_oversized_payload: PASS")


def test_ws_vision_frame_rejects_bytes_that_are_not_a_real_image() -> None:
    token  = "test-token-vf-corrupt"
    server = _server_with_token(token)
    client = TestClient(server.app)
    junk_b64 = base64.b64encode(b"not actually image data").decode("ascii")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "vision_frame", "request_id": "req-1",
            "mime_type": "image/jpeg", "data": junk_b64,
        })
        reply = ws.receive_json()

    assert reply["type"] == "vision_error", reply
    assert server._vision_frame_queue.empty()
    print("test_ws_vision_frame_rejects_bytes_that_are_not_a_real_image: PASS")


def test_ws_vision_control_stop_is_queued() -> None:
    token  = "test-token-vc-stop"
    server = _server_with_token(token)
    client = TestClient(server.app)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "vision_control", "request_id": "req-1",
            "action": "stop", "reason": "user_stopped",
        })
        time.sleep(0.05)

    assert not server._vision_frame_queue.empty()
    item = server._vision_frame_queue.get_nowait()
    assert item["request_id"] == "req-1"
    assert item["control"] == "stop"
    print("test_ws_vision_control_stop_is_queued: PASS")


# ── frame consumer: batching / injection ──────────────────────────────────

def test_web_vision_burst_is_injected_as_one_multipart_turn() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        req_id = "req-burst"
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": req_id, "started": now, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 100,   # window already elapsed
            "awaiting_burst": True, "text": "what's in my hand",
            "last_answered_at": None,
        }
        for i in range(2):
            await jarvis._dashboard._vision_frame_queue.put({
                "request_id": req_id, "seq": i,
                "mime_type": "image/jpeg", "data": _real_jpeg_bytes(),
            })

        await _run_frames_tick(jarvis, iterations=30, delay=0.05)

        assert len(fake_session.calls) == 1, "exactly one burst turn should have been sent"
        parts = fake_session.calls[0]["turns"]["parts"]
        image_parts = [p for p in parts if "inline_data" in p]
        text_parts  = [p for p in parts if "text" in p]
        assert len(image_parts) == 2, "both queued frames should be in the SAME turn"
        assert len(text_parts) == 1
        assert "[VISION_OBSERVATION]" in text_parts[0]["text"]
        assert "what's in my hand" in text_parts[0]["text"]
        assert jarvis._web_vision_session["awaiting_burst"] is False
        assert jarvis._web_vision_session["frames"] == []
    asyncio.run(_run())
    print("test_web_vision_burst_is_injected_as_one_multipart_turn: PASS")


def test_web_vision_burst_caps_at_max_frames() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        req_id = "req-cap"
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": req_id, "started": now, "deadline": now + 60,
            "frames": [], "burst_armed_at": now,   # window NOT yet elapsed —
            "awaiting_burst": True, "text": "look",   # only the frame count should trigger this
            "last_answered_at": None,
        }
        for i in range(WEB_VISION_BURST_MAX_FRAMES + 3):
            await jarvis._dashboard._vision_frame_queue.put({
                "request_id": req_id, "seq": i,
                "mime_type": "image/jpeg", "data": _real_jpeg_bytes(),
            })

        await _run_frames_tick(jarvis, iterations=30, delay=0.05)

        assert len(fake_session.calls) == 1
        image_parts = [p for p in fake_session.calls[0]["turns"]["parts"] if "inline_data" in p]
        assert len(image_parts) == WEB_VISION_BURST_MAX_FRAMES, (
            "a burst must never exceed WEB_VISION_BURST_MAX_FRAMES even if more frames were queued"
        )
    asyncio.run(_run())
    print("test_web_vision_burst_caps_at_max_frames: PASS")


def test_web_vision_stale_request_id_frame_is_dropped() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": "current-req", "started": now, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 100,
            "awaiting_burst": True, "text": "look", "last_answered_at": None,
        }
        # A frame for a DIFFERENT (old/stale) request_id must never be
        # folded into the currently active session.
        await jarvis._dashboard._vision_frame_queue.put({
            "request_id": "an-old-request", "seq": 0,
            "mime_type": "image/jpeg", "data": _real_jpeg_bytes(),
        })

        await _run_frames_tick(jarvis, iterations=20, delay=0.05)

        assert jarvis._web_vision_session["frames"] == [], "stale-request frame must never be appended"
    asyncio.run(_run())
    print("test_web_vision_stale_request_id_frame_is_dropped: PASS")


def test_web_vision_control_stop_ends_session_and_broadcasts_stop() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        jarvis.session = _FakeVisionSession()
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": "req-stop", "started": now, "deadline": now + 60,
            "frames": [], "burst_armed_at": now, "awaiting_burst": True,
            "text": "look", "last_answered_at": None,
        }
        await jarvis._dashboard._vision_frame_queue.put({
            "request_id": "req-stop", "control": "stop", "reason": "user_stopped",
        })

        await _run_frames_tick(jarvis, iterations=20, delay=0.05)

        assert jarvis._web_vision_session is None
        assert jarvis._dashboard.stops == ["req-stop"]
    asyncio.run(_run())
    print("test_web_vision_control_stop_ends_session_and_broadcasts_stop: PASS")


def test_web_vision_no_frames_at_all_ends_session_honestly() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": "req-empty", "started": now - 10, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 100,   # window elapsed, started > 6s ago
            "awaiting_burst": True, "text": "look", "last_answered_at": None,
        }

        await _run_frames_tick(jarvis, iterations=20, delay=0.05)

        assert jarvis._web_vision_session is None
        assert jarvis._dashboard.stops == ["req-empty"]
        assert len(fake_session.calls) == 1
        text = fake_session.calls[0]["turns"]["parts"][0]["text"]
        assert "[VISION_UNAVAILABLE]" in text
    asyncio.run(_run())
    print("test_web_vision_no_frames_at_all_ends_session_honestly: PASS")


def test_web_vision_hard_timeout_force_stops_and_notifies() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": "req-timeout", "started": now - 100, "deadline": now - 1,   # already past deadline
            "frames": [], "burst_armed_at": now - 100, "awaiting_burst": True,
            "text": "look", "last_answered_at": None,
        }

        await _run_frames_tick(jarvis, iterations=10, delay=0.05)

        assert jarvis._web_vision_session is None
        assert jarvis._dashboard.stops == ["req-timeout"]
        assert len(fake_session.calls) == 1
        assert "[VISION_TIMEOUT]" in fake_session.calls[0]["turns"]["parts"][0]["text"]
    asyncio.run(_run())
    print("test_web_vision_hard_timeout_force_stops_and_notifies: PASS")


def test_web_vision_grace_period_auto_stops_after_answer() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": "req-done", "started": now - 20, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 20, "awaiting_burst": False,   # already answered
            "text": "look", "last_answered_at": now - 100,   # long past the grace window
        }

        await _run_frames_tick(jarvis, iterations=10, delay=0.05)

        assert jarvis._web_vision_session is None
        assert jarvis._dashboard.stops == ["req-done"]
        assert len(fake_session.calls) == 0, "no re-call means no further Gemini turn should be sent"
    asyncio.run(_run())
    print("test_web_vision_grace_period_auto_stops_after_answer: PASS")


def test_web_vision_still_within_grace_period_does_not_stop_yet() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        jarvis.session = _FakeVisionSession()
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": "req-waiting", "started": now - 2, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 2, "awaiting_burst": False,
            "text": "look", "last_answered_at": now - 1,   # answered 1s ago, well within grace
        }

        await _run_frames_tick(jarvis, iterations=5, delay=0.05)

        assert jarvis._web_vision_session is not None, "must not end the session before the grace window elapses"
        assert jarvis._dashboard.stops == []
    asyncio.run(_run())
    print("test_web_vision_still_within_grace_period_does_not_stop_yet: PASS")


# ── tool description / capabilities text ──────────────────────────────────

def test_web_camera_vision_declaration_distinguishes_from_upload_and_screen_process() -> None:
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "web_camera_vision")
    desc = decl["description"]
    assert "DIFFERENT from a photo" in desc
    assert "call this again" in desc
    print("test_web_camera_vision_declaration_distinguishes_from_upload_and_screen_process: PASS")


def test_web_capabilities_text_mentions_live_camera_vision() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    config = jarvis._build_config()
    assert "web_camera_vision" in config.system_instruction
    print("test_web_capabilities_text_mentions_live_camera_vision: PASS")


# ── desktop regression ─────────────────────────────────────────────────────

def test_screen_process_and_close_camera_still_desktop_only() -> None:
    assert "screen_process" in DESKTOP_ONLY_TOOLS
    assert "close_camera" in DESKTOP_ONLY_TOOLS
    print("test_screen_process_and_close_camera_still_desktop_only: PASS")


def test_desktop_vision_handler_source_unchanged_by_web_camera_vision() -> None:
    import main as main_module
    src = inspect.getsource(main_module.JarvisLive._execute_tool)
    assert "self._pending_vision = (img_b, mime_t, user_text, angle)" in src
    print("test_desktop_vision_handler_source_unchanged_by_web_camera_vision: PASS")


# ── non-persistence ─────────────────────────────────────────────────────

def test_web_vision_frame_processing_never_writes_a_file() -> None:
    import main as main_module
    src = inspect.getsource(main_module.JarvisLive._process_web_vision_frames)
    for banned in ("open(", ".write(", "write_bytes", "NamedTemporaryFile", "tempfile"):
        assert banned not in src, f"unexpected filesystem write pattern found: {banned!r}"
    print("test_web_vision_frame_processing_never_writes_a_file: PASS")


# ── hallucination-prevention regression (real iPhone finding) ────────────
#
# Real-device finding: on an actual deployed iPhone session, asking "What's
# in my hand?" produced a confident, specific false claim ("तपाईंको हातमा
# एउटा रातो गोलो वस्तु देखिन्छ" — "I see a red round object in your hand")
# while the floating camera preview never even appeared — meaning
# web_camera_vision was never actually called and no real frame was ever
# received. This is a PROMPT-adherence failure (the model answering from
# imagination), not a transport/backend bug — the same class of defect as
# the earlier-fixed "stale screen_process description" bug, just the
# opposite direction (false confidence instead of false denial). The fix
# is a forceful, explicit "never claim live visual knowledge without a
# real look" rule in both the web [CAPABILITIES] text and the tool's own
# description — these tests are a regression guard for that specific text,
# not a guarantee the model can never disobey it (an LLM's own adherence to
# a system instruction cannot be proven by a unit test — see the
# implementation report's own "remaining limitations").

def test_web_capabilities_text_forbids_answering_without_a_real_look() -> None:
    jarvis = JarvisLive(HeadlessSurface(), auto_start=False)
    text = jarvis._build_config().system_instruction
    assert "you have NO knowledge of what the user is currently holding" in text
    assert "VISION_OBSERVATION" in text
    assert "you MUST call web_camera_vision and wait" in text
    print("test_web_capabilities_text_forbids_answering_without_a_real_look: PASS")


def test_web_camera_vision_declaration_mandates_a_real_look_first() -> None:
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "web_camera_vision")
    desc = decl["description"]
    assert "MANDATORY" in desc
    assert "before answering ANY question about what you" in desc
    print("test_web_camera_vision_declaration_mandates_a_real_look_first: PASS")


def test_vision_unavailable_notice_explicitly_forbids_guessing() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": "req-noguess", "started": now - 10, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 100, "awaiting_burst": True,
            "text": "look", "last_answered_at": None,
        }
        await _run_frames_tick(jarvis, iterations=20, delay=0.05)
        assert len(fake_session.calls) == 1
        text = fake_session.calls[0]["turns"]["parts"][0]["text"]
        assert "Do NOT guess or invent" in text
    asyncio.run(_run())
    print("test_vision_unavailable_notice_explicitly_forbids_guessing: PASS")


def test_invalid_frame_is_rejected_before_ever_reaching_the_session() -> None:
    """Case 2 from the hallucination-prevention requirement: a corrupt/
    invalid frame must never be silently counted as a real look — it's
    rejected at the WS boundary (dashboard/server.py), never even reaching
    main.py's queue, so it can never masquerade as real visual evidence."""
    token  = "test-token-vf-invalid-case2"
    server = _server_with_token(token)
    client = TestClient(server.app)
    junk_b64 = base64.b64encode(b"not a real image at all").decode("ascii")

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({
            "type": "vision_frame", "request_id": "req-x",
            "mime_type": "image/jpeg", "data": junk_b64,
        })
        reply = ws.receive_json()

    assert reply["type"] == "vision_error"
    assert server._vision_frame_queue.empty(), "an invalid frame must never reach the vision session's frame count"
    print("test_invalid_frame_is_rejected_before_ever_reaching_the_session: PASS")


def test_real_frame_does_reach_the_session_as_analyzable_content() -> None:
    """Case 3: contrast with the above — a genuinely valid frame DOES reach
    Gemini as real inline_data, so a correct answer remains possible."""
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        req_id = "req-real"
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": req_id, "started": now, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 100, "awaiting_burst": True,
            "text": "what color is this", "last_answered_at": None,
        }
        await jarvis._dashboard._vision_frame_queue.put({
            "request_id": req_id, "seq": 0,
            "mime_type": "image/jpeg", "data": _real_jpeg_bytes(),
        })
        await _run_frames_tick(jarvis, iterations=20, delay=0.05)
        assert len(fake_session.calls) == 1
        image_parts = [p for p in fake_session.calls[0]["turns"]["parts"] if "inline_data" in p]
        assert len(image_parts) == 1
        assert len(image_parts[0]["inline_data"]["data"]) > 0
    asyncio.run(_run())
    print("test_real_frame_does_reach_the_session_as_analyzable_content: PASS")


def test_ordinary_question_never_appears_in_web_camera_vision_triggers() -> None:
    """Case 4: an ordinary knowledge question has no visual-need cues at
    all — this is a static guard that the tool description itself scopes
    activation to genuinely visual requests, not a behavioral guarantee
    (see the module-level note on LLM adherence)."""
    decl = next(t for t in TOOL_DECLARATIONS if t["name"] == "web_camera_vision")
    desc = decl["description"]
    assert "Do NOT call this for ordinary questions" in desc
    print("test_ordinary_question_never_appears_in_web_camera_vision_triggers: PASS")


# ── production-safe diagnostics (VISION_REQUESTED / _RECEIVED / _ANALYZED) ─
#
# Added so a real-device report ("no camera preview appeared") can be
# pinpointed to an exact stage from the Activity Log alone: if the
# "Camera requested" line is MISSING, the tool was never called (a prompt
# issue); if it's present but "Analyzing" never follows, frames never
# reached the backend (a browser/permission issue) — see main.py's own
# comments at each broadcast site. Never logs actual frame bytes.

def test_opening_a_session_emits_a_camera_requested_diagnostic() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        await jarvis._execute_tool(_fc("web_camera_vision", text="what's in my hand"))
        await asyncio.sleep(0.05)
        assert any("Camera requested" in m for m in jarvis._dashboard.sys_messages)
    asyncio.run(_run())
    print("test_opening_a_session_emits_a_camera_requested_diagnostic: PASS")


def test_burst_injection_emits_an_analyzing_diagnostic() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        req_id = "req-diag"
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": req_id, "started": now, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 100, "awaiting_burst": True,
            "burst_token": 0, "text": "look", "last_answered_at": None,
        }
        await jarvis._dashboard._vision_frame_queue.put({
            "request_id": req_id, "seq": 0,
            "mime_type": "image/jpeg", "data": _real_jpeg_bytes(),
        })
        await _run_frames_tick(jarvis, iterations=20, delay=0.05)
        assert any("Analyzing" in m for m in jarvis._dashboard.sys_messages)
    asyncio.run(_run())
    print("test_burst_injection_emits_an_analyzing_diagnostic: PASS")


def test_no_frames_gives_up_emits_a_no_image_diagnostic() -> None:
    async def _run():
        jarvis = _jarvis(auto_start=False)
        jarvis.session = _FakeVisionSession()
        now = time.monotonic()
        jarvis._web_vision_session = {
            "request_id": "req-nodiag", "started": now - 10, "deadline": now + 60,
            "frames": [], "burst_armed_at": now - 100, "awaiting_burst": True,
            "burst_token": 0, "text": "look", "last_answered_at": None,
        }
        await _run_frames_tick(jarvis, iterations=20, delay=0.05)
        assert any("No camera image arrived" in m for m in jarvis._dashboard.sys_messages)
    asyncio.run(_run())
    print("test_no_frames_gives_up_emits_a_no_image_diagnostic: PASS")


def test_frame_processing_survives_an_unexpected_exception() -> None:
    """Resilience: _process_web_vision_frames() is a process-lifetime task
    created via a bare asyncio.create_task() (outside the per-connection
    TaskGroup) — nothing else would ever restart it if an unexpected bug
    raised out of one iteration. Proves one bad tick doesn't permanently
    kill the loop for the rest of the process."""
    async def _run():
        jarvis = _jarvis(auto_start=False)
        fake_session = _FakeVisionSession()
        jarvis.session = fake_session
        now = time.monotonic()
        # A session missing "deadline" -- something no real code path ever
        # produces, but exactly the kind of unexpected bug this guards
        # against -- would KeyError on `session["deadline"]` mid-iteration.
        jarvis._web_vision_session = {
            "request_id": "req-bad", "started": now,
            "frames": [], "burst_armed_at": now, "awaiting_burst": True,
            "burst_token": 0, "text": "look", "last_answered_at": None,
        }

        task = asyncio.create_task(jarvis._process_web_vision_frames())
        try:
            await asyncio.sleep(0.3)   # let the broken session's tick raise + get caught
            assert not task.done(), "the task must survive an unexpected exception, not die"

            # Now hand it a normal, well-formed session and prove it still
            # works correctly afterward -- the loop genuinely recovered.
            jarvis._web_vision_session = {
                "request_id": "req-good", "started": time.monotonic(),
                "deadline": time.monotonic() + 60, "frames": [],
                "burst_armed_at": time.monotonic() - 100, "awaiting_burst": True,
                "burst_token": 0, "text": "look", "last_answered_at": None,
            }
            await jarvis._dashboard._vision_frame_queue.put({
                "request_id": "req-good", "seq": 0,
                "mime_type": "image/jpeg", "data": _real_jpeg_bytes(),
            })
            for _ in range(30):
                if fake_session.calls:
                    break
                await asyncio.sleep(0.05)
            assert fake_session.calls, "the loop must keep processing normally after recovering from the earlier bad tick"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(_run())
    print("test_frame_processing_survives_an_unexpected_exception: PASS")


if __name__ == "__main__":
    test_web_camera_vision_is_web_only_and_never_desktop_only()
    test_web_camera_vision_blocked_on_desktop_with_screen_process_redirect()
    test_web_camera_vision_opens_session_and_broadcasts_request()
    test_web_camera_vision_repeat_call_continues_without_rebroadcasting()
    test_web_camera_vision_cooldown_blocks_rapid_double_call()
    test_ws_vision_frame_authenticated_and_valid_is_queued()
    test_ws_vision_frame_missing_request_id_rejected()
    test_ws_vision_frame_rejects_unsupported_mime_type()
    test_ws_vision_frame_rejects_oversized_payload()
    test_ws_vision_frame_rejects_bytes_that_are_not_a_real_image()
    test_ws_vision_control_stop_is_queued()
    test_web_vision_burst_is_injected_as_one_multipart_turn()
    test_web_vision_burst_caps_at_max_frames()
    test_web_vision_stale_request_id_frame_is_dropped()
    test_web_vision_control_stop_ends_session_and_broadcasts_stop()
    test_web_vision_no_frames_at_all_ends_session_honestly()
    test_web_vision_hard_timeout_force_stops_and_notifies()
    test_web_vision_grace_period_auto_stops_after_answer()
    test_web_vision_still_within_grace_period_does_not_stop_yet()
    test_web_camera_vision_declaration_distinguishes_from_upload_and_screen_process()
    test_web_capabilities_text_mentions_live_camera_vision()
    test_screen_process_and_close_camera_still_desktop_only()
    test_desktop_vision_handler_source_unchanged_by_web_camera_vision()
    test_web_vision_frame_processing_never_writes_a_file()
    test_web_capabilities_text_forbids_answering_without_a_real_look()
    test_web_camera_vision_declaration_mandates_a_real_look_first()
    test_vision_unavailable_notice_explicitly_forbids_guessing()
    test_invalid_frame_is_rejected_before_ever_reaching_the_session()
    test_real_frame_does_reach_the_session_as_analyzable_content()
    test_ordinary_question_never_appears_in_web_camera_vision_triggers()
    test_opening_a_session_emits_a_camera_requested_diagnostic()
    test_burst_injection_emits_an_analyzing_diagnostic()
    test_no_frames_gives_up_emits_a_no_image_diagnostic()
    test_frame_processing_survives_an_unexpected_exception()
    print("\nAll web-camera-vision tests passed.")
