"""
core/headless_surface.py — minimal AssistantSurface for running JarvisLive
without any desktop UI.

Phase 1 scope only: this exists to prove JarvisLive has no hidden PyQt6
dependency beyond the AssistantSurface it's constructed with. Every method
here is deliberately minimal — console-only feedback, no camera, no file
drop, no dashboard/WebSocket wiring. Broadcasting to real remote clients
(the dashboard, a future web frontend) is Phase 3+ work and is
intentionally NOT implemented here.

Known limitation, documented rather than silently patched: JarvisLive.run()
has one code path (invalid-API-key reconnect) that reaches past the
AssistantSurface protocol into JarvisUI's private `_win._ready` attribute
(see core/assistant_surface.py's docstring for why that exception exists).
HeadlessSurface has no equivalent object, so that specific recovery path
isn't supported yet — headless mode assumes a valid Gemini API key is
already present in config/api_keys.json before starting. Giving JarvisLive
a proper awaitable "wait for reconfiguration" hook on the Protocol itself
is a small, deliberate design change better suited to whichever later
phase formalizes remote configuration — not this one.
"""
from __future__ import annotations

from typing import Callable, Optional


class HeadlessSurface:
    """Minimal AssistantSurface implementation: no UI, no PyQt6, no
    dashboard wiring. Confirms JarvisLive's brain logic is frontend-agnostic
    beyond the AssistantSurface interface it depends on.
    """

    def __init__(self) -> None:
        # ── callback slots — JarvisLive assigns these once at startup ──────
        self.on_text_command:   Optional[Callable[[str], None]] = None
        self.on_remote_clicked: Optional[Callable[[], tuple | None]] = None
        self.on_interrupt:      Optional[Callable[[], None]] = None
        self.get_plugins:       Optional[Callable[[], list[dict]]] = None
        self.request_say:       Optional[Callable[[str], None]] = None

    # ── read-only state JarvisLive queries ──────────────────────────────────
    @property
    def muted(self) -> bool:
        return False   # no local mic to mute headlessly

    @property
    def current_file(self) -> str | None:
        return None    # no local file-drop widget headlessly

    # ── methods JarvisLive calls ────────────────────────────────────────────
    def write_log(self, text: str) -> None:
        print(f"[Headless] {text}")

    def set_state(self, state: str) -> None:
        print(f"[Headless] state={state}")

    def set_jarvis_mode(self, active: bool) -> None:
        # No HUD to update headlessly (web/server sessions get their
        # identity switch from the dashboard's jarvis_mode_changed
        # broadcast -> the React frontend instead — see App.jsx) — this
        # exists purely so JarvisLive can call self.ui.set_jarvis_mode()
        # unconditionally without needing to know which surface it's on.
        print(f"[Headless] jarvis_mode={active}")

    def show_content(self, title: str, text: str) -> None:
        print(f"[Headless] content: {title}\n{text}")

    def start_camera_stream(self) -> None:
        pass   # no camera hardware/preview widget headlessly

    def stop_camera_stream(self) -> None:
        pass

    def notify_phone_connected(self) -> None:
        print("[Headless] Phone connected via Remote Dashboard.")

    def prompt_reconfig(self) -> None:
        print(
            "[Headless] ERR: Gemini API key invalid, but headless mode has "
            "no interactive setup dialog. Fix config/api_keys.json and "
            "restart the process."
        )
