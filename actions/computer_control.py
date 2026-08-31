#computer_control.py
import io
import json
import platform
import re
import string
import subprocess
import sys

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}
import time
import random
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

# JARVIS Mode — Windows UI Automation. Already an established dependency
# in this exact codebase (requirements.txt: "pywinauto; sys_platform ==
# 'win32'") and already proven working here (actions/game_updater.py's
# Steam-install-dialog automation) — this just widens its role from that
# one narrow use to a general semantic (name/control-type, not raw pixel
# coordinates) element finder for ui_find/ui_click/ui_type. Windows-only;
# on mac/Linux these three actions honestly report unavailable and the
# existing screen_find/screen_click (vision-coordinate) fallback still
# works everywhere.
try:
    import pywinauto
    _PYWINAUTO = True
except ImportError:
    _PYWINAUTO = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"
_MEMORY_PATH  = _BASE / "memory" / "long_term.json"

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _platform_os() -> str:
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def _get_os() -> str:
    return _load_config().get("os_system", _platform_os()).lower()


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "")

_SAFE_SCREENSHOT_ROOTS = (
    Path.home(),
)

def _safe_screenshot_path(requested: str | None) -> Path:
    fallback = Path.home() / "Desktop" / "jarvis_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        for root in _SAFE_SCREENSHOT_ROOTS:
            if p.is_relative_to(root.resolve()):
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return fallback

def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")

_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    if dt == "last_name":
        return random.choice(_LAST_NAMES)

    if dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    if dt == "username":
        return f"{random.choice(_FIRST_NAMES).lower()}{random.randint(100, 9999)}"

    if dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        raw   = (
            random.choice(string.ascii_uppercase)
            + random.choice(string.digits)
            + random.choice("!@#$%")
            + "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(raw, len(raw)))

    if dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1_000_000, 9_999_999)}"

    if dt == "birthday":
        y = random.randint(1980, 2000)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{m:02d}/{d:02d}/{y}"

    if dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    if dt == "zip_code":
        return str(random.randint(10000, 99999))

    if dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000, 9999)}"

def _user_profile() -> dict:
    """Read identity fields from long-term memory."""
    try:
        if _MEMORY_PATH.exists():
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception:
        pass
    return {}

def _type(text: str, interval: float = 0.03) -> str:
    _require_pyautogui()
    time.sleep(0.3)
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _smart_type(text: str, clear_first: bool = True) -> str:
    _require_pyautogui()
    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if len(text) > 20 and _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Smart-typed (clipboard): {text[:60]}{'…' if len(text) > 60 else ''}"

    pyautogui.typewrite(text, interval=0.04)
    return f"Smart-typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _require_pyautogui()
    if x is not None and y is not None:
        pyautogui.click(x, y, button=button, clicks=clicks)
        return f"{'Double-c' if clicks == 2 else 'C'}licked ({x}, {y}) [{button}]"
    pyautogui.click(button=button, clicks=clicks)
    return f"Clicked at current position [{button}]"


def _hotkey(*keys) -> str:
    _require_pyautogui()
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    _require_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    _require_pyautogui()
    vertical   = direction in ("up", "down")
    clicks     = amount if direction in ("up", "right") else -amount
    pyautogui.scroll(clicks) if vertical else pyautogui.hscroll(clicks)
    return f"Scrolled {direction} ×{amount}"


def _move(x: int, y: int, duration: float = 0.3) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x, y, duration=duration)
    return f"Mouse → ({x}, {y})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x1, y1, duration=0.2)
    pyautogui.dragTo(x2, y2, duration=duration, button="left")
    return f"Dragged ({x1},{y1}) → ({x2},{y2})"


def _clipboard_get() -> str:
    if _PYPERCLIP:
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "(copied — pyperclip unavailable for read)"


def _clipboard_paste(text: str) -> str:
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        _require_pyautogui()
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Pasted: {text[:60]}{'…' if len(text) > 60 else ''}"
    return "pyperclip not available"


def _screenshot(save_path: str | None = None) -> str:
    _require_pyautogui()
    path = _safe_screenshot_path(save_path)
    img  = pyautogui.screenshot()
    img.save(str(path))
    return f"Screenshot saved: {path}"


def _clear_field() -> str:
    _require_pyautogui()
    select_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(select_key, "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    return "Field cleared"

def _focus_window(title: str) -> str:
    os_name = _get_os()

    if os_name == "windows":
        try:
            script = f'(New-Object -ComObject WScript.Shell).AppActivate("{title}")'
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=5, **_WIN_HIDE,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (Windows) failed: {e}"

    if os_name == "mac":
        script = (
            f'tell application "System Events" to '
            f'set frontmost of (first process whose name contains "{title}") to true'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (macOS) failed: {e}"

    if os_name == "linux":
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(0.3)
                return f"Focused window: {title}"
        except FileNotFoundError:
            pass
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowactivate"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except FileNotFoundError:
            return "focus_window (Linux) requires wmctrl or xdotool"
        except Exception as e:
            return f"focus_window (Linux) failed: {e}"

    return f"focus_window: unknown OS '{os_name}'"


# ── JARVIS Mode: active-window metadata + UI Automation ─────────────────

def _foreground_hwnd() -> int | None:
    """Windows only — the raw HWND of the current foreground window via a
    direct ctypes call (user32.dll), no subprocess/PowerShell round trip
    needed. Shared by get_active_window_title() and _ui_find() below so
    both always agree on exactly which window "active" means."""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return hwnd or None
    except Exception as e:
        print(f"[ComputerControl] ⚠️ _foreground_hwnd failed: {e}")
        return None


def get_active_window_title() -> str:
    """Best-effort title of the CURRENTLY foreground/active window — the
    metadata that accompanies every JARVIS observe/verify capture (see
    main.py's computer_control branch) so Gemini's reasoning doesn't have
    to re-derive "what app is this" purely from pixels each time. Never
    raises; an empty string means "couldn't determine it," handled
    honestly by the caller, never guessed."""
    os_name = _get_os()

    if os_name == "windows":
        try:
            import ctypes
            hwnd = _foreground_hwnd()
            if not hwnd:
                return ""
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            return (buf.value or "").strip()
        except Exception as e:
            print(f"[ComputerControl] ⚠️ get_active_window_title (Windows) failed: {e}")
            return ""

    if os_name == "mac":
        try:
            script = (
                'tell application "System Events" to get name of '
                'first application process whose frontmost is true'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            return (result.stdout or "").strip()
        except Exception as e:
            print(f"[ComputerControl] ⚠️ get_active_window_title (macOS) failed: {e}")
            return ""

    if os_name == "linux":
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return (result.stdout or "").strip()
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[ComputerControl] ⚠️ get_active_window_title (Linux) failed: {e}")
        return ""

    return ""


# Bounds the Python-side text-matching loop below, NOT the UIA fetch
# itself — win.descendants() always walks and marshals the WHOLE subtree
# before returning regardless of this cap (see its own module's
# implementation; there is no cheap way to short-circuit that fetch from
# here without scoping to a specific sub-container, which isn't generic
# across arbitrary apps). Live-measured against a real, large WhatsApp
# Desktop chat list (~24,800 total descendants): the fetch itself costs
# several seconds no matter what; raised from an earlier, too-tight 400
# to 2000 so a contact further down a long list is still reachable by
# the matching loop — the incremental per-candidate cost is small
# (sub-millisecond) relative to the fixed fetch cost either way.
_UI_FIND_MAX_CANDIDATES = 2000


def _pick_best_match(description: str, candidates, max_candidates: int = _UI_FIND_MAX_CANDIDATES):
    """Pure matching logic, deliberately separated from _ui_find() below so
    it's testable with plain fake objects (anything with a .window_text()
    method) rather than a real pywinauto/UIA session — see
    tests/test_computer_control.py.

    Matching priority — fixed after a real, live-reproduced bug: a naive
    "first substring match wins" scan can match an INCIDENTAL occurrence
    of the needle buried inside unrelated text (e.g. a WhatsApp message
    PREVIEW that happens to mention the contact's name mid-sentence,
    appearing earlier in tree-walk order than the real contact's own
    entry) instead of the actual target, silently clicking the wrong
    thing. A real "this control IS the thing" label (a contact name, a
    button caption) almost always STARTS WITH what was asked for; an
    incidental mention never does — so a startswith match is always
    preferred over a substring-anywhere match, and ties are broken by
    preferring the SHORTEST matching name (closer to a precise label than
    a long compound string that merely begins the same way)."""
    needle = (description or "").strip().lower()
    if not needle:
        return None

    best_startswith, best_startswith_len = None, None
    best_substring, best_substring_len = None, None
    for i, ctrl in enumerate(candidates):
        if i >= max_candidates:
            break
        try:
            name = (ctrl.window_text() or "").strip().lower()
        except Exception:
            continue
        if not name:
            continue
        if needle == name:
            return ctrl  # exact match — stop immediately, no ambiguity
        if name.startswith(needle):
            if best_startswith is None or len(name) < best_startswith_len:
                best_startswith, best_startswith_len = ctrl, len(name)
        elif needle in name:
            if best_substring is None or len(name) < best_substring_len:
                best_substring, best_substring_len = ctrl, len(name)
    return best_startswith or best_substring


def _ui_find(description: str):
    """Best-effort, BOUNDED search of the ACTIVE window's UI Automation
    tree (Windows only — see _PYWINAUTO above) for a control whose visible
    text loosely matches `description`, using _pick_best_match()'s
    priority order above. Returns the matched pywinauto control wrapper,
    or None — never raises, never falls back to a coordinate guess itself
    (that's screen_find's job, a deliberately separate/distinct tool
    action — see this tool's own declaration). Only ever inspects the
    single foreground window, never the whole desktop, and only a bounded
    number of its descendants, so a huge or pathological control tree
    can't hang the tool call."""
    if not _PYWINAUTO or _get_os() != "windows":
        return None

    if not (description or "").strip():
        return None

    hwnd = _foreground_hwnd()
    if not hwnd:
        print("[ComputerControl] ⚠️ ui_find: no foreground window handle")
        return None

    try:
        from pywinauto import Desktop
        win = Desktop(backend="uia").window(handle=hwnd)
    except Exception as e:
        print(f"[ComputerControl] ⚠️ ui_find: could not wrap active window ({e})")
        return None

    try:
        candidates = win.descendants()
    except Exception as e:
        print(f"[ComputerControl] ⚠️ ui_find: could not enumerate controls ({e})")
        return None

    return _pick_best_match(description, candidates)


def _ui_find_report(description: str) -> str:
    ctrl = _ui_find(description)
    if ctrl is None:
        return f"NOT_FOUND: '{description}' (accessibility tree)"
    try:
        r = ctrl.rectangle()
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        return f"Found (UI Automation): '{description}' near ({cx},{cy})"
    except Exception:
        return f"Found (UI Automation): '{description}'"


def _top_level_window_titles() -> set[str]:
    """Cheap (~50ms live-measured — see this function's own call sites),
    SHALLOW top-level window enumeration (not a deep descendants() walk).
    Used only as a generic "did something unexpected pop up" signal
    around a click — see _new_window_note() below for why this exists:
    a real, live-reproduced bug where an unrelated modal error dialog
    silently absorbed a click that was otherwise correctly targeted and
    physically performed (found the right element, clicked the right
    coordinate, click_input() raised no exception) — 'no exception' is
    NOT sufficient evidence the click actually did what was intended."""
    if not _PYWINAUTO:
        return set()
    try:
        from pywinauto import Desktop
        return {w.window_text() for w in Desktop(backend="uia").windows()}
    except Exception:
        return set()


def _new_window_note(before: set[str], after: set[str]) -> str:
    new_titles = after - before
    if not new_titles:
        return ""
    listed = ", ".join(repr(t) for t in sorted(new_titles) if t.strip())
    if not listed:
        return ""
    return (
        f" NOTE: a new window appeared right after this click: {listed} — "
        f"this may be an unrelated dialog that intercepted the click "
        f"rather than the intended target; check it before assuming the "
        f"expected result happened."
    )


def _ui_click_by_description(description: str) -> str:
    if not _PYWINAUTO or _get_os() != "windows":
        return "UI Automation is only available on Windows here — use screen_click instead."
    ctrl = _ui_find(description)
    if ctrl is None:
        return f"UI element not found via accessibility tree: '{description}' — try screen_click instead."
    before = _top_level_window_titles()
    try:
        ctrl.click_input()
    except Exception as e:
        return f"Found '{description}' but click failed: {e}"
    # Brief, bounded settle so a UI transition (a pane changing, a dialog
    # appearing) has a moment to actually happen before the check below —
    # matches this file's existing convention for post-action pauses
    # (_focus_window() etc.), not a new pattern.
    time.sleep(0.3)
    note = _new_window_note(before, _top_level_window_titles())
    return (
        f"Clicked (UI Automation): '{description}'. This confirms the "
        f"click was physically performed at the right target — it does "
        f"NOT by itself confirm the expected result happened. Call "
        f"action='verify' to actually confirm it before telling the "
        f"user it worked.{note}"
    )


def _ui_type_by_description(description: str, text: str) -> str:
    if not _PYWINAUTO or _get_os() != "windows":
        return "UI Automation is only available on Windows here — use smart_type instead."
    ctrl = _ui_find(description)
    if ctrl is None:
        return f"UI input not found via accessibility tree: '{description}' — try smart_type instead."
    before = _top_level_window_titles()
    try:
        ctrl.click_input()
        time.sleep(0.1)
        try:
            ctrl.set_text(text)   # fast, exact path for real Edit/Text controls
        except Exception:
            _require_pyautogui()
            pyautogui.typewrite(text, interval=0.03)
    except Exception as e:
        return f"Found '{description}' but type failed: {e}"
    time.sleep(0.2)
    note = _new_window_note(before, _top_level_window_titles())
    preview = text[:60] + ("…" if len(text) > 60 else "")
    return f"Typed (UI Automation) into '{description}': {preview}{note}"


def _screen_find(description: str) -> tuple[int, int] | None:
    api_key = _get_api_key()
    if not api_key:
        print("[ComputerControl] ⚠️ No API key for screen_find")
        return None

    try:
        from google import genai
        from google.genai import types as gtypes

        _require_pyautogui()
        w, h  = pyautogui.size()
        img   = pyautogui.screenshot()
        buf   = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        client = genai.Client(api_key=api_key)
        prompt = (
            f"This is a screenshot of a {w}×{h} pixel screen. "
            f"Locate the UI element described as: '{description}'. "
            f"Reply with ONLY the center coordinates as: x,y "
            f"If the element is not visible, reply: NOT_FOUND"
        )

        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=[
                gtypes.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                prompt,
            ],
        )

        text = (response.text or "").strip()
        if "NOT_FOUND" in text.upper():
            return None

        match = re.search(r"(\d+)\s*,\s*(\d+)", text)
        if match:
            return int(match.group(1)), int(match.group(2))

    except Exception as e:
        print(f"[ComputerControl] ⚠️ screen_find failed: {e}")

    return None

def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for all computer control actions.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type or paste
      x, y          : screen coordinates
      button        : 'left' | 'right' (default: left)
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      seconds       : wait duration
      title         : window title fragment for focus_window
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      type          — type text at cursor
      smart_type    — clear field + type (clipboard-backed)
      click         — left click
      double_click  — double left click
      right_click   — right click
      move          — move mouse
      drag          — click-drag between two points
      hotkey        — key combination
      press         — single key
      scroll        — scroll the wheel
      copy          — read clipboard
      paste         — write + paste clipboard
      screenshot    — capture screen (safe path only)
      wait          — sleep N seconds
      clear_field   — select-all + delete
      focus_window  — bring window to foreground
      screen_find   — AI element finder (returns x,y)
      screen_click  — AI element finder + click
      random_data   — generate fake form data
      user_data     — pull real data from memory

      JARVIS Mode only (gated by main.py's _execute_tool(), not here —
      this module has no notion of JARVIS mode itself):
      get_active_window_title — title of the currently foreground window
      ui_find       — find a control by name in the ACTIVE window's UI
                       Automation tree (Windows only; semantic, not pixels)
      ui_click      — ui_find + click via UI Automation
      ui_type       — ui_find + type via UI Automation (falls back to
                       pyautogui typing if the control has no direct
                       set_text)
      (observe/verify are NOT dispatched here — they need main.py's own
      async self._pending_vision injection, see that module's
      computer_control branch)
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified for computer_control."

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] ▶ {action}  {params}")

    try:

        if action == "type":
            return _type(params.get("text", ""))

        if action == "smart_type":
            return _smart_type(
                params.get("text", ""),
                clear_first=params.get("clear_first", True),
            )

        if action in ("click", "left_click"):
            return _click(params.get("x"), params.get("y"), "left", 1)

        if action == "double_click":
            return _click(params.get("x"), params.get("y"), "left", 2)

        if action == "right_click":
            return _click(params.get("x"), params.get("y"), "right", 1)

        if action == "move":
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action == "drag":
            return _drag(
                int(params.get("x1", 0)), int(params.get("y1", 0)),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
            )

        if action == "hotkey":
            raw  = params.get("keys", "")
            keys = [k.strip() for k in raw.split("+")] if isinstance(raw, str) else raw
            return _hotkey(*keys)

        if action == "press":
            return _press(params.get("key", "enter"))

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "copy":
            return _clipboard_get()

        if action == "paste":
            return _clipboard_paste(params.get("text", ""))

        if action == "screenshot":
            return _screenshot(params.get("path"))

        if action == "screen_find":
            coords = _screen_find(params.get("description", ""))
            return f"{coords[0]},{coords[1]}" if coords else "NOT_FOUND"

        if action == "get_active_window_title":
            title = get_active_window_title()
            return title if title else "(could not determine the active window title)"

        if action == "ui_find":
            return _ui_find_report(params.get("description", ""))

        if action == "ui_click":
            return _ui_click_by_description(params.get("description", ""))

        if action == "ui_type":
            return _ui_type_by_description(params.get("description", ""), params.get("text", ""))

        if action == "screen_click":
            desc   = params.get("description", "")
            coords = _screen_find(desc)
            if coords:
                time.sleep(0.2)
                _click(x=coords[0], y=coords[1])
                return f"Clicked '{desc}' at {coords}"
            return f"Element not found on screen: '{desc}'"

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "clear_field":
            return _clear_field()

        if action == "focus_window":
            return _focus_window(params.get("title", ""))

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            print(f"[ComputerControl] 🎲 random {dt} → {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                print(f"[ComputerControl] ⚠️ No '{field}' in memory, using random: {value}")
            return value

        return f"Unknown action: '{action}'"

    except Exception as e:
        print(f"[ComputerControl] ❌ {action}: {e}")
        return f"computer_control '{action}' failed: {e}"