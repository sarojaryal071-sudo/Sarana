"""
core/assistant_surface.py — the UI-facing contract JarvisLive depends on.

JarvisLive (main.py) is constructed with a "ui" object and calls a small,
fixed set of methods/properties on it throughout the session lifecycle,
tool dispatch, and reconnect logic. AssistantSurface names that contract
explicitly so JarvisLive can be typed against an interface instead of the
concrete PyQt6-backed JarvisUI class (ui.py).

This is a typing-only boundary — Protocol is structural, so JarvisUI
already satisfies it with no changes to ui.py. Introducing this file
changes no runtime behavior anywhere.

Member list verified directly against every `self.ui.*` access inside
JarvisLive in main.py (grepped line by line, not assumed):

  Callback slots (assigned once, in JarvisLive.__init__):
    on_text_command, on_remote_clicked, on_interrupt, get_plugins,
    request_say

  Methods called during the session lifecycle / tool execution:
    write_log, set_state, set_audio_level, set_jarvis_mode, set_expression,
    show_content, start_camera_stream, stop_camera_stream,
    notify_phone_connected, prompt_reconfig

  Read-only properties:
    muted, current_file

Known exception, intentionally NOT part of this protocol:
  JarvisLive.run()'s invalid-API-key reconnect path polls
  `self.ui._win._ready` directly — a private-attribute reach into
  JarvisUI's internal MainWindow that bypasses the public facade
  entirely. (JarvisUI does expose a public `wait_for_api_key()`, but
  it busy-waits with a blocking `time.sleep()` rather than an
  awaitable, which is presumably why the async reconnect loop reads
  the private attribute inline instead of calling it.) This is
  pre-existing behavior. Fixing it is a logic change, not a typing
  change, so it is out of scope for this sub-step and is called out
  in the accompanying implementation report instead of being
  silently patched or silently ignored.
"""
from __future__ import annotations

from typing import Callable, Protocol


class AssistantSurface(Protocol):
    """Structural interface JarvisLive requires from whatever UI/frontend
    it is constructed with. JarvisUI (ui.py) satisfies this today with no
    changes required. Any future frontend driver (e.g. a headless
    dashboard-only surface) could satisfy it too, without JarvisLive
    needing to know which concrete implementation it was given.
    """

    # ── callback slots — JarvisLive assigns these once at startup ──────────
    on_text_command:   Callable[[str], None] | None
    on_remote_clicked: Callable[[], tuple | None] | None
    on_interrupt:      Callable[[], None] | None
    get_plugins:       Callable[[], list[dict]] | None
    request_say:       Callable[[str], None] | None

    # ── read-only state JarvisLive queries ──────────────────────────────────
    @property
    def muted(self) -> bool: ...

    @property
    def current_file(self) -> str | None: ...

    # ── methods JarvisLive calls ────────────────────────────────────────────
    def write_log(self, text: str) -> None: ...
    def set_state(self, state: str) -> None: ...
    def set_audio_level(self, level: float) -> None: ...
    def set_jarvis_mode(self, active: bool) -> None: ...
    def set_expression(self, expression: str, duration_seconds: float) -> None: ...
    def show_content(self, title: str, text: str) -> None: ...
    def start_camera_stream(self) -> None: ...
    def stop_camera_stream(self) -> None: ...
    def notify_phone_connected(self) -> None: ...
    def prompt_reconfig(self) -> None: ...
