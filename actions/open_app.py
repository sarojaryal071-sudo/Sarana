import time
import subprocess
import platform
import shutil

from actions import result_envelope as _envelope
# J2 (Universal Actions): reuses computer_control.py's OWN existing
# get_active_window_title() as the real post-launch verification signal
# — this is the "migrate onto the general controller" step. Nothing
# about the OS-specific launch logic below (_launch_windows/_launch_macos/
# _launch_linux) changes; computer_control.py is the shared substrate
# this module now depends on for verification, not a second window-
# detection implementation. No circular import: computer_control.py
# imports neither this module nor send_message.py.
from actions.computer_control import get_active_window_title

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "x-terminal-emulator"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    # JARVIS Mode — direct OS deep links: deterministic, zero UI
    # automation needed just to REACH the right settings pane (see the
    # Bluetooth/Wi-Fi example in this project's own computer-control
    # investigation). No API key, no screen-scraping — connecting to a
    # SPECIFIC already-listed device still needs computer_control's
    # ui_find/ui_click once the pane is open.
    "bluetooth":          {"Windows": "ms-settings:bluetooth",   "Darwin": "System Preferences",   "Linux": "gnome-control-center bluetooth"},
    "bluetooth settings": {"Windows": "ms-settings:bluetooth",   "Darwin": "System Preferences",   "Linux": "gnome-control-center bluetooth"},
    "wifi":               {"Windows": "ms-settings:network-wifi","Darwin": "System Preferences",   "Linux": "gnome-control-center wifi"},
    "wi-fi":              {"Windows": "ms-settings:network-wifi","Darwin": "System Preferences",   "Linux": "gnome-control-center wifi"},
    "wifi settings":      {"Windows": "ms-settings:network-wifi","Darwin": "System Preferences",   "Linux": "gnome-control-center wifi"},
    "sound":              {"Windows": "ms-settings:sound",       "Darwin": "System Preferences",   "Linux": "gnome-control-center sound"},
    "sound settings":     {"Windows": "ms-settings:sound",       "Darwin": "System Preferences",   "Linux": "gnome-control-center sound"},
    "audio":              {"Windows": "ms-settings:sound",       "Darwin": "System Preferences",   "Linux": "gnome-control-center sound"},
    "audio settings":     {"Windows": "ms-settings:sound",       "Darwin": "System Preferences",   "Linux": "gnome-control-center sound"},
    "volume mixer":       {"Windows": "ms-settings:apps-volume", "Darwin": "System Preferences",   "Linux": "gnome-control-center sound"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _launch_windows(app_name: str) -> bool:

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


_LINUX_TERMINAL_FALLBACKS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]

def _launch_linux(app_name: str) -> bool:

    # terminal emulators: try common ones in order
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def _window_confirms_app(window_title: str, app_name: str) -> bool:
    """Best-effort, deterministic match between the foreground window
    title and the requested app — never exact (titles vary with open
    documents, unsaved-state markers, etc.), but real evidence rather
    than assuming a launch command succeeding means the app actually
    appeared. Matches on the app name as a whole first, then its first
    significant word (titles commonly read '<content> - <App Name>')."""
    if not window_title:
        return False
    title_l = window_title.lower()
    name_l = app_name.lower().strip()
    if name_l in title_l or title_l in name_l:
        return True
    first_word = name_l.split()[0] if name_l.split() else ""
    return len(first_word) > 2 and first_word in title_l


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """J2 (Universal Actions): returns a Result Envelope — 'the launch
    command was sent' is no longer conflated with 'the app is verified
    open'. VERIFIED_SUCCESS requires the foreground window to actually
    confirm it (see _window_confirms_app); a launch call that reports
    success but can't be confirmed that way is honestly INCONCLUSIVE,
    never upgraded to a guess."""
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no application name was given")

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"unsupported operating system: {_SYSTEM}")

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    before = get_active_window_title()
    try:
        launched = launcher(normalized)
        if not launched and normalized.lower() != app_name.lower():
            launched = launcher(app_name)
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"failed to open {app_name}: {e}")

    if not launched:
        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_FAILURE,
            f"no launch method succeeded for '{app_name}' — it may not be installed",
        )

    time.sleep(0.4)  # small extra grace period on top of the launcher's own settling sleeps
    after = get_active_window_title()
    if _window_confirms_app(after, app_name):
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"{app_name} is now the active window ('{after}')")
    return _envelope.envelope(
        _envelope.STATUS_INCONCLUSIVE,
        f"a launch command for {app_name} was issued, but the foreground window "
        f"('{after or 'unknown'}') does not confirm it — it may still be loading",
    )