"""
scripts/live_browser_check.py — one-off Phase 6 live-localhost verification.

Not a pytest/unittest suite kept in tests/ — this is a scripted stand-in
for "open the browser and click around" (section 20 of the Phase 6 spec),
used because this environment has no interactive GUI browser available.
It performs exactly what src/lib/api.js and src/lib/websocket.js do:
    GET  /api/session
    POST /login {pin}
    WS   /ws?token=...   -> send {"type":"command", "text": ...}
    WS   /ws/audio-out?token=...  -> confirm it accepts a connection

Usage:
    .venv/Scripts/python.exe scripts/live_browser_check.py <PIN>
"""
import asyncio
import ssl
import sys

import requests
import websockets

BASE = "https://127.0.0.1:8000"
WS_BASE = "wss://127.0.0.1:8000"


def main():
    if len(sys.argv) < 2:
        print("Usage: live_browser_check.py <PIN>  |  live_browser_check.py --token <TOKEN>")
        sys.exit(1)

    print("1) GET /api/session ...")
    r = requests.get(f"{BASE}/api/session", verify=False, timeout=8)
    r.raise_for_status()
    session = r.json()
    print(f"   assistant_name={session['assistant_name']!r} tools={len(session['tools'])} "
          f"desktop_connected={session['desktop_connected']}")

    if sys.argv[1] == "--token":
        token = sys.argv[2]
        print("2) Reusing an already-issued token (PIN was already consumed by an earlier run).")
    else:
        pin = sys.argv[1]
        print("2) POST /login (PIN pairing) ...")
        r = requests.post(f"{BASE}/login", json={"pin": pin}, verify=False, timeout=8)
        body = r.json()
        if not body.get("ok"):
            print(f"   LOGIN FAILED: {body}")
            sys.exit(1)
        token = body["token"]
        print(f"   token acquired ({token[:12]}...)")

    asyncio.run(_ws_checks(token))


async def _ws_checks(token: str):
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    print("3) WS /ws/audio-out — connect FIRST so we don't miss the reply's audio ...")
    audio_ws = await websockets.connect(f"{WS_BASE}/ws/audio-out?token={token}", ssl=ssl_ctx)

    async def _drain_audio():
        chunks = 0
        total_bytes = 0
        try:
            while True:
                msg = await asyncio.wait_for(audio_ws.recv(), timeout=10)
                chunks += 1
                total_bytes += len(msg)
        except asyncio.TimeoutError:
            pass
        return chunks, total_bytes

    audio_task = asyncio.create_task(_drain_audio())

    print("4) WS /ws — connect + send a command ...")
    async with websockets.connect(f"{WS_BASE}/ws?token={token}", ssl=ssl_ctx) as ws:
        await ws.send('{"type": "command", "text": "Phase 6 live check: say hello"}')
        print("   command sent — listening for server messages (8s)...")
        try:
            for _ in range(20):
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                print(f"   <- {msg[:200]}")
        except asyncio.TimeoutError:
            print("   (no more messages within timeout — expected once the turn settles)")

    chunks, total_bytes = await audio_task
    await audio_ws.close()
    print(f"   audio-out: received {chunks} chunk(s), {total_bytes} bytes total")

    print("\nDone.")


if __name__ == "__main__":
    main()
