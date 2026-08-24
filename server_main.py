"""
server_main.py — headless entry point for the Sarana brain.

Phase 1 scope only: runs the exact same JarvisLive class main.py's desktop
path uses, with no PyQt6, no QApplication, no desktop window — proving the
brain is frontend-agnostic. A real API/WebSocket surface, remote clients,
and dashboard wiring are later-phase work; this file only exists to make
`python main.py` (desktop) and `python server_main.py` (headless) both
real, working ways to run the exact same brain.

Phase 7: constructs JarvisLive with auto_start=False — the dashboard/API
layer comes up immediately, but the Gemini connect loop (and therefore the
microphone, speaker, and startup briefing) does not start until a frontend
sends the existing wake signal (POST /api/wake, or any /api/command / "ws
command" message — see main.py's run() for the wiring). This is the same
JarvisLive.run(), unmodified past that one gate; nothing about the brain
itself changes between desktop and headless launches.

Requires config/api_keys.json to already contain a valid Gemini API key —
headless mode has no interactive setup dialog (see
core/headless_surface.py's docstring for why).

Usage:
    python server_main.py
"""
import asyncio

from main import JarvisLive
from core.headless_surface import HeadlessSurface


def main() -> None:
    surface = HeadlessSurface()
    jarvis = JarvisLive(surface, auto_start=False)
    print("[Sarana] Backend is running.")
    print("[Sarana] Waiting for frontend to start Jarvis.")
    try:
        asyncio.run(jarvis.run())
    except KeyboardInterrupt:
        print("\n🔴 Shutting down...")


if __name__ == "__main__":
    main()
