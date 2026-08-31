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


def _element_rect_key(ctrl):
    """(left, top, right, bottom) of a control's on-screen rectangle, or
    None if unavailable. Used ONLY to tell a genuine ambiguity (two
    DIFFERENT elements sharing a label) apart from a virtualization
    duplicate (the SAME visible control exposed multiple times in the
    tree) — see _all_same_visual_element."""
    try:
        r = ctrl.rectangle()
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def _element_control_type(ctrl) -> str:
    try:
        return (ctrl.element_info.control_type or "").strip().lower()
    except Exception:
        return ""


def _element_automation_id(ctrl) -> str:
    try:
        return (ctrl.element_info.automation_id or "").strip().lower()
    except Exception:
        return ""


def _all_same_visual_element(ctrls) -> bool:
    """True if every candidate shares the same on-screen rectangle — a
    real, LIVE-CONFIRMED pattern (dumped against the user's actual running
    WhatsApp Desktop: its virtualized chat list and search box each expose
    ONE visible control through dozens of duplicate AutomationElements —
    up to 79 observed for a single search box — every one sharing an
    IDENTICAL rectangle). That must never be reported as a genuine
    ambiguity. A real ambiguity is two DIFFERENT elements at DIFFERENT
    screen locations that happen to share a label (rare, but real — e.g.
    two "OK" buttons behind two different dialogs). Candidates with no
    readable rectangle at all (e.g. plain test fakes) are treated as
    "same" — the safe, backward-compatible default (see _pick_best_match's
    own tests, none of which model rectangles)."""
    keys = {_element_rect_key(c) for c in ctrls}
    return len(keys) <= 1


def _resolve_ui_target(
    description: str,
    candidates,
    max_candidates: int = _UI_FIND_MAX_CANDIDATES,
    control_type: str | None = None,
) -> tuple[str, object, str]:
    """The general UI resolver behind ui_find/ui_click/ui_type/
    list_ui_elements — the "given a natural-language description, find the
    ONE real element it refers to" primitive the whole generic
    computer-control loop depends on (see this module's own module
    docstring). Pure function, testable with plain fake objects — see
    tests/test_computer_control.py.

    Matching priority — fixed after a real, live-reproduced bug: a naive
    "first substring match wins" scan can match an INCIDENTAL occurrence
    of the needle buried inside unrelated text (e.g. a WhatsApp message
    PREVIEW that happens to mention the contact's name mid-sentence,
    appearing earlier in tree-walk order than the real contact's own
    entry) instead of the actual target. So:
      1. an exact automation-id match (a stable identifier some apps
         expose even when the visible text doesn't match what was asked)
      2. an exact visible-text match
      3. the SHORTEST name that STARTS WITH the needle (closer to a
         precise label than a long compound string that merely begins
         the same way)
      4. the SHORTEST name that merely CONTAINS the needle, only if no
         startswith match exists at all
    An optional control_type filter (e.g. "button", "edit") narrows the
    search when the same label appears on more than one KIND of control.

    Returns (status, ctrl_or_None, note):
      "found"     — ctrl is the single resolved match; note says how
      "ambiguous" — ctrl is None; note names how many DISTINCT elements
                    tied for the win (see _all_same_visual_element for how
                    virtualization duplicates are told apart from this)
      "not_found" — ctrl is None; note says why

    Never silently guesses when genuinely unsure — the caller (ui_find/
    ui_click/ui_type) is expected to report "ambiguous" honestly rather
    than picking one, per the project brief's explicit requirement."""
    needle = (description or "").strip().lower()
    if not needle:
        return "not_found", None, "no description given"

    ctype_filter = (control_type or "").strip().lower() or None

    exact: list = []
    startswith_by_len: dict[int, list] = {}
    substring_by_len: dict[int, list] = {}

    for i, ctrl in enumerate(candidates):
        if i >= max_candidates:
            break
        if ctype_filter and _element_control_type(ctrl) != ctype_filter:
            continue
        auto_id = _element_automation_id(ctrl)
        if auto_id and auto_id == needle:
            exact.append(ctrl)
            continue
        try:
            name = (ctrl.window_text() or "").strip().lower()
        except Exception:
            continue
        if not name:
            continue
        if needle == name:
            exact.append(ctrl)
        elif name.startswith(needle):
            startswith_by_len.setdefault(len(name), []).append(ctrl)
        elif needle in name:
            substring_by_len.setdefault(len(name), []).append(ctrl)

    for tier_name, winners in (
        ("exact", exact),
        ("startswith", startswith_by_len[min(startswith_by_len)] if startswith_by_len else []),
        ("substring", substring_by_len[min(substring_by_len)] if substring_by_len else []),
    ):
        if not winners:
            continue
        if len(winners) == 1 or _all_same_visual_element(winners):
            return "found", winners[0], f"{tier_name} match"
        return "ambiguous", None, (
            f"{len(winners)} distinct elements equally match '{description}' "
            f"({tier_name}) — inspect more context (list_ui_elements/observe) "
            f"or ask the user which one they mean, rather than guessing"
        )

    return "not_found", None, f"no element matches '{description}'"


def _pick_best_match(description: str, candidates, max_candidates: int = _UI_FIND_MAX_CANDIDATES):
    """Back-compat thin wrapper around _resolve_ui_target() — always
    returns a bare ctrl or None, never surfaces ambiguity itself ("found"
    -> the resolved ctrl, "ambiguous"/"not_found" -> None alike). Kept
    because it's the smallest, most directly testable surface for the
    matching-tier logic (see tests/test_computer_control.py — none of
    those fakes model rectangles, so a genuine ambiguous verdict never
    actually arises there; _all_same_visual_element treats rectangle-less
    candidates as "same", which is exactly the pre-existing "first
    shortest match wins" behavior those tests already expect). ui_find()
    itself now calls _resolve_ui_target() directly so it CAN report
    ambiguity honestly instead of collapsing it to None."""
    status, ctrl, _ = _resolve_ui_target(description, candidates, max_candidates)
    return ctrl


def _ui_find(description: str, control_type: str | None = None) -> tuple[str, object, str]:
    """Best-effort, BOUNDED, AMBIGUITY-AWARE search of the ACTIVE window's
    UI Automation tree (Windows only — see _PYWINAUTO above) for a control
    matching `description`, delegating to _resolve_ui_target() for the
    actual tiered matching/ambiguity logic. Never raises, never falls back
    to a coordinate guess itself (that's screen_find's job, a deliberately
    separate/distinct tool action — see this tool's own declaration).
    Only ever inspects the single foreground window, never the whole
    desktop, and only a bounded number of its descendants, so a huge or
    pathological control tree can't hang the tool call.

    Returns (status, ctrl_or_None, note) — status is "found" | "ambiguous"
    | "not_found" | "unavailable" (non-Windows, or pywinauto missing)."""
    if not _PYWINAUTO or _get_os() != "windows":
        return "unavailable", None, "UI Automation is only available on Windows here"

    if not (description or "").strip():
        return "not_found", None, "no description given"

    hwnd = _foreground_hwnd()
    if not hwnd:
        print("[ComputerControl] ⚠️ ui_find: no foreground window handle")
        return "not_found", None, "no foreground window handle"

    try:
        from pywinauto import Desktop
        win = Desktop(backend="uia").window(handle=hwnd)
    except Exception as e:
        print(f"[ComputerControl] ⚠️ ui_find: could not wrap active window ({e})")
        return "not_found", None, f"could not wrap the active window ({e})"

    try:
        candidates = win.descendants()
    except Exception as e:
        print(f"[ComputerControl] ⚠️ ui_find: could not enumerate controls ({e})")
        return "not_found", None, f"could not enumerate controls ({e})"

    return _resolve_ui_target(description, candidates, control_type=control_type)


def _ui_find_report(description: str, control_type: str | None = None) -> str:
    status, ctrl, note = _ui_find(description, control_type=control_type)
    if status == "unavailable":
        return note
    if status == "not_found":
        return f"NOT_FOUND: '{description}' (accessibility tree) — {note}"
    if status == "ambiguous":
        return f"[UI_AMBIGUOUS] '{description}': {note}"
    try:
        r = ctrl.rectangle()
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        return f"Found (UI Automation): '{description}' near ({cx},{cy}) — {note}"
    except Exception:
        return f"Found (UI Automation): '{description}' — {note}"


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


# ── JARVIS Mode: automatic local click/type verification ─────────────────
#
# Upgrades ui_click/ui_type from "perform the input event, hope for the
# best" to "perform it, then automatically check — using only cheap LOCAL
# signals — whether it actually had an effect." This never calls Gemini
# itself (that would mean a vision round trip on every single click,
# explicitly ruled out by the project brief) — it only produces one of
# four honest verdicts (tags below) that main.py's computer_control
# branch can optionally escalate ONCE to the EXISTING observe/verify
# same-session vision mechanism when local evidence is inconclusive.
#
# Verification tiers, cheapest/most-certain first (see
# _classify_click_result / _classify_type_result):
#   1. UI Automation state of the clicked/typed control itself
#   2. active-window title change (+ existing dialog/new-window detection)
#   3. accessibility-tree-adjacent control state (toggle/selection/staleness)
#   4. a small in-memory screenshot diff, last resort before giving up
#      locally — never written to disk, discarded immediately after use.

VERIFY_TAG_SUCCESS   = "[CLICK_VERIFIED_SUCCESS]"
VERIFY_TAG_FAILURE   = "[CLICK_VERIFIED_FAILURE]"
VERIFY_TAG_AMBIGUOUS = "[CLICK_AMBIGUOUS]"
VERIFY_TAG_NO_CHANGE = "[CLICK_NO_OBSERVABLE_CHANGE]"

TYPE_TAG_SUCCESS   = "[TYPE_VERIFIED_SUCCESS]"
TYPE_TAG_FAILURE   = "[TYPE_VERIFIED_FAILURE]"
TYPE_TAG_AMBIGUOUS = "[TYPE_AMBIGUOUS]"

# Tags that mean "local verification could not tell" — main.py escalates
# these (and only these) to the existing observe/verify vision mechanism,
# bounded by that mechanism's own cooldown/busy guard. Exported so main.py
# never has to hardcode the literal tag strings itself.
INCONCLUSIVE_TAGS = frozenset({
    VERIFY_TAG_AMBIGUOUS, VERIFY_TAG_NO_CHANGE, TYPE_TAG_AMBIGUOUS, TYPE_TAG_FAILURE,
})


def _control_signature(ctrl) -> dict:
    """Cheap, best-effort snapshot of ONE control's own UI Automation
    state. Never walks the tree, never raises. Tier-1 signal: the exact
    element that was clicked visibly changing is the strongest, cheapest
    evidence available."""
    sig = {"alive": True, "toggle": None, "selected": None}
    try:
        ctrl.window_text()
    except Exception:
        sig["alive"] = False
        return sig
    try:
        sig["toggle"] = ctrl.get_toggle_state()
    except Exception:
        pass
    try:
        sig["selected"] = ctrl.is_selected()
    except Exception:
        pass
    return sig


def _screen_signature():
    """Small, in-memory-only, grayscale-downsampled screenshot signature —
    NEVER written to disk, held only long enough for one before/after
    comparison and then discarded by the caller. Tier-4, last-resort local
    signal: far cheaper and less invasive than a Gemini vision call."""
    if not _PYAUTOGUI:
        return None
    try:
        img = pyautogui.screenshot().convert("L").resize((24, 16))
        return tuple(img.getdata())
    except Exception:
        return None


def _screen_changed(before, after, threshold: int = 12) -> bool | None:
    """None means "couldn't tell" (missing/mismatched signatures) — the
    caller treats that the same as "no signal", never as a false positive
    or negative."""
    if before is None or after is None or len(before) != len(after):
        return None
    diff = sum(1 for a, b in zip(before, after) if abs(a - b) > 20)
    return diff > threshold


def _snapshot(ctrl) -> dict:
    """All-local, no-Gemini snapshot taken before AND after a click/type,
    then diffed by _classify_click_result. Every piece is inexpensive: one
    ctypes call, one shallow window enumeration, one control's own state,
    one small downsampled screenshot."""
    return {
        "active_title": get_active_window_title(),
        "top_level":    _top_level_window_titles(),
        "ctrl":         _control_signature(ctrl),
        "screen":       _screen_signature(),
    }


def _classify_click_result(description: str, before: dict, after: dict) -> tuple[str, str]:
    """Pure classifier — no live UI calls, fully unit-testable with plain
    dicts shaped like _snapshot()'s output (see tests/test_computer_control.py).
    Returns (tag, human-readable reason). Never returns VERIFY_TAG_FAILURE
    itself (a click that raised an exception already returns early in the
    caller, before this is reached) — "nothing seemed to happen" is
    reported as NO_OBSERVABLE_CHANGE, not a false FAILURE claim, since a
    control that's genuinely inert-looking after a correct click is still
    possible (e.g. a click that opens something off-screen)."""
    needle = (description or "").strip().lower()

    dialog_note = _new_window_note(before["top_level"], after["top_level"])
    if dialog_note:
        return VERIFY_TAG_AMBIGUOUS, (
            "a new top-level window appeared right after the click." + dialog_note
        )

    if before["active_title"] != after["active_title"]:
        if needle and needle in (after["active_title"] or "").lower():
            return VERIFY_TAG_SUCCESS, (
                f"the active window changed to '{after['active_title']}', "
                f"matching the intended target"
            )
        return VERIFY_TAG_AMBIGUOUS, (
            f"the active window changed (now '{after['active_title']}') but "
            f"that could not be confirmed to match the intended target"
        )

    cb, ca = before["ctrl"], after["ctrl"]
    if cb["alive"] and not ca["alive"]:
        return VERIFY_TAG_SUCCESS, (
            "the clicked element is no longer present in the accessibility "
            "tree, consistent with the UI having moved on"
        )
    if cb.get("toggle") is not None and cb.get("toggle") != ca.get("toggle"):
        return VERIFY_TAG_SUCCESS, "the control's toggle state changed"
    if cb.get("selected") is not None and cb.get("selected") != ca.get("selected"):
        return VERIFY_TAG_SUCCESS, "the control's selection state changed"

    changed = _screen_changed(before.get("screen"), after.get("screen"))
    if changed is True:
        return VERIFY_TAG_AMBIGUOUS, (
            "the screen's pixels changed after the click, but no window- "
            "or control-level signal confirmed what changed"
        )
    return VERIFY_TAG_NO_CHANGE, (
        "no window title, dialog, control state, or visible pixel change "
        "was detected after the click"
    )


def _ui_click_by_description(description: str, control_type: str | None = None) -> str:
    status, ctrl, note = _ui_find(description, control_type=control_type)
    if status == "unavailable":
        return note
    if status == "ambiguous":
        return (
            f"[UI_AMBIGUOUS] Cannot click '{description}': {note}. Call "
            f"list_ui_elements (or observe) for more context, narrow the "
            f"description, or ask the user which one they mean — never "
            f"guess."
        )
    if status == "not_found" or ctrl is None:
        return f"UI element not found via accessibility tree: '{description}' — try screen_click instead. ({note})"

    before = _snapshot(ctrl)
    try:
        ctrl.click_input()
    except Exception as e:
        return f"Found '{description}' but click failed: {e}"
    # Brief, bounded settle so a UI transition (a pane changing, a dialog
    # appearing) has a moment to actually happen before the check below —
    # matches this file's existing convention for post-action pauses
    # (_focus_window() etc.), not a new pattern.
    time.sleep(0.3)
    after = _snapshot(ctrl)
    tag, reason = _classify_click_result(description, before, after)

    # NO automatic retry here — deliberately removed after a real,
    # live-reproduced counter-example: clicking a Windows Calculator digit
    # button (a REAL, stateful action — it appends to the display) still
    # classifies as VERIFY_TAG_NO_CHANGE, because a calculator button
    # exposes no toggle/selection/staleness signal and its window title
    # never changes — there is nothing WRONG with the click, our LOCAL
    # signals just can't see the effect. An earlier version of this
    # function retried once automatically on NO_CHANGE, reasoning that
    # "nothing observably happened" was the SAFEST case to repeat — live
    # testing proved that assumption false: every digit/operator got
    # clicked twice, silently computing the wrong result (12+7 became
    # 1122+77, then Equals doubled again to 1276). NO_OBSERVABLE_CHANGE
    # means "we don't know", never "nothing happened" — so blindly
    # clicking again is exactly the "unsafe automatic clicking" the
    # project brief warns against. Recovery now belongs one level up:
    # main.py escalates an inconclusive verdict to a REAL look (the
    # existing observe/verify vision mechanism — see
    # computer_control.INCONCLUSIVE_TAGS), and only Gemini's own
    # reasoning — which can judge whether repeating THIS specific action
    # is actually safe — decides whether to click again.
    return (
        f"Clicked (UI Automation): '{description}'. {tag} — {reason}. "
        f"This does NOT necessarily mean nothing happened — some controls "
        f"(e.g. calculator/numeric-entry-style buttons) never expose a "
        f"locally observable success signal even on a correct click. Call "
        f"action='verify' to check the real result before deciding "
        f"whether to click again — never click the same target a second "
        f"time without confirming first."
    )


def _read_control_value(ctrl) -> str | None:
    """Best-effort read-back of an editable control's current text via UI
    Automation — tries the exact ValuePattern path first, falls back to
    window_text() (many Edit-style controls surface their content there
    too). Returns None if nothing could be read (e.g. a masked password
    field, or a control exposing no readable value) — the caller treats
    that as AMBIGUOUS, never as a false FAILURE."""
    try:
        val = ctrl.get_value()
        if val is not None:
            return str(val)
    except Exception:
        pass
    try:
        txt = ctrl.window_text()
        if txt:
            return str(txt)
    except Exception:
        pass
    return None


def _classify_type_result(expected: str, before_val, after_val) -> tuple[str, str]:
    """Pure classifier, unit-testable with plain strings/None — never
    includes the actual typed/read content in its reason text (privacy:
    typed content must never become logged/remembered activity, see this
    module's own docstring)."""
    if after_val is None:
        return TYPE_TAG_AMBIGUOUS, (
            "the field's content could not be read back locally (it may "
            "be masked, e.g. a password field, or not expose its value "
            "via UI Automation)"
        )
    expected_norm = (expected or "").strip()
    if not expected_norm:
        return TYPE_TAG_SUCCESS, "no specific text was expected to verify"
    if expected_norm in after_val:
        return TYPE_TAG_SUCCESS, "the field now contains the typed text"
    if after_val == before_val:
        return TYPE_TAG_FAILURE, "the field's content did not change at all after typing"
    return TYPE_TAG_AMBIGUOUS, (
        "the field's content changed but does not appear to contain the expected text"
    )


def _ui_type_by_description(description: str, text: str, control_type: str | None = None) -> str:
    status, ctrl, note = _ui_find(description, control_type=control_type)
    if status == "unavailable":
        return note
    if status == "ambiguous":
        return (
            f"[UI_AMBIGUOUS] Cannot type into '{description}': {note}. Call "
            f"list_ui_elements (or observe) for more context, narrow the "
            f"description, or ask the user which one they mean — never "
            f"guess."
        )
    if status == "not_found" or ctrl is None:
        return f"UI input not found via accessibility tree: '{description}' — try smart_type instead. ({note})"
    before = _top_level_window_titles()
    before_val = _read_control_value(ctrl)
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
    after_val = _read_control_value(ctrl)
    tag, reason = _classify_type_result(text, before_val, after_val)
    # Discard the read-back values now that the comparison is done — never
    # persisted, never logged, never echoed verbatim below (privacy: typed
    # content must never become remembered activity history).
    before_val = after_val = None
    preview = text[:60] + ("…" if len(text) > 60 else "")
    return (
        f"Typed (UI Automation) into '{description}': {preview}. "
        f"{tag} — {reason}{note}"
    )


# ── JARVIS Mode: general-purpose UI discovery ─────────────────────────────
#
# The generic "what's actually here?" primitive the rest of the loop
# depends on. Before this, JARVIS could only search for ONE element by a
# description it had to GUESS blindly (ui_find) — it had no cheap way to
# discover what's really clickable in an app it has never seen before
# without a full vision round trip. This closes that gap LOCALLY (no
# Gemini call): a small, bounded, deduplicated text summary of the active
# window's interactive controls, cheap enough to call before almost every
# unfamiliar UI interaction.

_LIST_UI_MAX_ELEMENTS = 60  # keeps the report small/cheap for Gemini — a
# TEXT summary, not a vision call; bounded so a huge tree (WhatsApp's real
# chat list runs ~25,000 descendants) can't produce an unusable result.

_LIST_UI_INTERESTING_TYPES = frozenset({
    "button", "edit", "checkbox", "radiobutton", "combobox", "menuitem",
    "listitem", "tabitem", "hyperlink", "text",
})


def list_ui_elements(control_type: str | None = None) -> str:
    """General-purpose UI discovery for the CURRENT foreground window —
    the generic alternative to guessing a description string for ui_find
    and hoping it matches something real. Returns a bounded, human-
    readable list of control_type + accessible name (+ enabled/selected/
    toggle state where cheaply readable) for whatever's actually
    interactive right now, application-agnostic (no per-app knowledge —
    same code path for WhatsApp, Word, Settings, File Explorer, or an
    app installed five minutes ago). Deduplicates the same virtualization-
    duplicate pattern documented on _all_same_visual_element (one real
    on-screen control should be listed once, not dozens of times) so the
    output stays genuinely useful rather than repetitive.

    Never persists anything — this is a live, ephemeral read, discarded
    by the caller (Gemini's own reasoning, via the tool-call response)
    immediately after use, same as every other JARVIS observation."""
    if not _PYWINAUTO or _get_os() != "windows":
        return "UI Automation is only available on Windows here — use action='observe' (screen vision) instead."
    hwnd = _foreground_hwnd()
    if not hwnd:
        return "Could not determine the foreground window."
    try:
        from pywinauto import Desktop
        win = Desktop(backend="uia").window(handle=hwnd)
        candidates = win.descendants()
    except Exception as e:
        return f"Could not inspect the active window's UI: {e}"

    ctype_filter = (control_type or "").strip().lower() or None
    seen_rects: set = set()
    lines: list[str] = []
    for ctrl in candidates:
        if len(lines) >= _LIST_UI_MAX_ELEMENTS:
            break
        try:
            ct = _element_control_type(ctrl)
            if not ct or ct not in _LIST_UI_INTERESTING_TYPES:
                continue
            if ctype_filter and ct != ctype_filter:
                continue
            name = (ctrl.window_text() or "").strip()
            if not name:
                continue
            rect_key = _element_rect_key(ctrl)
            if rect_key is not None:
                if rect_key in seen_rects:
                    continue  # same virtualization-duplicate pattern as _resolve_ui_target
                seen_rects.add(rect_key)

            state_bits = []
            try:
                if not ctrl.is_enabled():
                    state_bits.append("disabled")
            except Exception:
                pass
            try:
                if ctrl.is_selected():
                    state_bits.append("selected")
            except Exception:
                pass
            try:
                tog = ctrl.get_toggle_state()
                if tog is not None:
                    state_bits.append(f"toggle={tog}")
            except Exception:
                pass
            state = f" [{', '.join(state_bits)}]" if state_bits else ""

            preview = name[:70] + ("…" if len(name) > 70 else "")
            lines.append(f"- {ct}: '{preview}'{state}")
        except Exception:
            continue

    if not lines:
        return (
            "No interactive elements were found in the active window (it "
            "may rely on custom drawing without accessibility support — "
            "try action='observe' for a visual look instead)."
        )
    title = get_active_window_title()
    header = f"Active window: '{title or 'unknown'}'. Interactive elements found (up to {_LIST_UI_MAX_ELEMENTS}):"
    return header + "\n" + "\n".join(lines)


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
      control_type  : optional UI Automation control type filter (e.g.
                       'button', 'edit') for list_ui_elements/ui_find/
                       ui_click/ui_type — narrows a search when the same
                       label appears on more than one kind of control

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
      list_ui_elements — GENERAL discovery: a bounded, deduplicated text
                       list of every interactive control (type + name +
                       state) in the ACTIVE window's UI Automation tree.
                       No app-specific knowledge — the same call surfaces
                       WhatsApp's chat list, Word's ribbon, a Settings
                       page, or a brand-new app's UI alike. Prefer this
                       BEFORE guessing a ui_find/ui_click/ui_type
                       description for an unfamiliar screen. Optional
                       control_type param narrows to one kind (e.g.
                       'button').
      ui_find       — find a control by name (optionally + control_type
                       and/or automation-id) in the ACTIVE window's UI
                       Automation tree (Windows only; semantic, not
                       pixels). Now ambiguity-aware: if more than one
                       DISTINCT real element equally matches, this
                       reports [UI_AMBIGUOUS] instead of silently
                       guessing — call list_ui_elements/observe for more
                       context or ask the user, never assume.
      ui_click      — ui_find + click via UI Automation. Automatically
                       verifies the result using cheap LOCAL signals only
                       (no Gemini call) and reports one of four honest
                       verdicts in the returned text: [CLICK_VERIFIED_
                       SUCCESS], [CLICK_VERIFIED_FAILURE] (click itself
                       raised), [CLICK_AMBIGUOUS], or [CLICK_NO_
                       OBSERVABLE_CHANGE]. Deliberately does NOT retry the
                       click itself, even on NO_OBSERVABLE_CHANGE — a
                       real, live-reproduced case (Calculator digit
                       buttons: no toggle/selection/title signal on a
                       correct click) proved that "nothing observably
                       changed" does NOT mean "nothing happened", so an
                       earlier auto-retry silently double-clicked and
                       computed the wrong result. Instead main.py escalates
                       an inconclusive verdict ONCE to the existing
                       observe/verify vision mechanism, and only Gemini's
                       own reasoning decides whether repeating the SAME
                       click is actually safe. May also report
                       [UI_AMBIGUOUS] up front if the description doesn't
                       resolve to one distinct element.
      ui_type       — ui_find + type via UI Automation (falls back to
                       pyautogui typing if the control has no direct
                       set_text). Automatically reads the field back and
                       reports [TYPE_VERIFIED_SUCCESS]/[TYPE_VERIFIED_
                       FAILURE]/[TYPE_AMBIGUOUS] — never persists or logs
                       the typed/read-back content itself. May also
                       report [UI_AMBIGUOUS] up front, same as ui_click.
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

        if action == "list_ui_elements":
            return list_ui_elements(params.get("control_type"))

        if action == "ui_find":
            return _ui_find_report(params.get("description", ""), params.get("control_type"))

        if action == "ui_click":
            return _ui_click_by_description(params.get("description", ""), params.get("control_type"))

        if action == "ui_type":
            return _ui_type_by_description(
                params.get("description", ""), params.get("text", ""), params.get("control_type")
            )

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