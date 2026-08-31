#computer_settings.py
import json
import re
import sys
import time
import subprocess
import platform
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

from actions import result_envelope as _envelope

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

if _OS == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    path = _get_base_dir() / "config" / "api_keys.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0" 

def volume_up():
    if _OS == "Windows":
        for _ in range(5): pyautogui.press("volumeup")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) + 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
            capture_output=True)

def volume_down():
    if _OS == "Windows":
        for _ in range(5): pyautogui.press("volumedown")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) - 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
            capture_output=True)

def volume_mute():
    if _OS == "Windows":
        pyautogui.press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", "set volume with output muted"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
            capture_output=True)

def volume_set(value: int):
    value = max(0, min(100, int(value)))
    if _OS == "Windows":
        try:
            # AudioDevice.EndpointVolume is the ready-to-use
            # IAudioEndpointVolume pointer in the installed pycaw version
            # — no manual .Activate(IAudioEndpointVolume._iid_, ...) COM
            # dance needed (an older pycaw API shape this code used to
            # assume; it silently failed and fell back to a keypress
            # mute-toggle hack every time — found live while building
            # get_volume()'s readback, which hit the exact same bug).
            # SetMasterVolumeLevelScalar takes a 0.0-1.0 fraction, not dB.
            from pycaw.pycaw import AudioUtilities
            devices = AudioUtilities.GetSpeakers()
            devices.EndpointVolume.SetMasterVolumeLevelScalar(value / 100.0, None)
            return
        except Exception as e:
            print(f"[Settings] pycaw failed, using keypress fallback: {e}")
            pyautogui.press("volumemute")
            pyautogui.press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {value}"],
            capture_output=True)
        return
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
            capture_output=True)
        return

def get_volume() -> int | None:
    """Best-effort ground-truth readback of the current system volume
    (0-100) via pycaw — mirrors volume_set()'s own pycaw usage/fallback
    posture. None if pycaw/the audio endpoint isn't available, never
    guessed."""
    if _OS != "Windows":
        return None
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetSpeakers()
        return round(devices.EndpointVolume.GetMasterVolumeLevelScalar() * 100)
    except Exception as e:
        print(f"[Settings] get_volume failed: {e}")
        return None


def brightness_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 144'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(min(1.0,b+0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Min(100, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness up failed on Windows: {e}")

def brightness_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 145'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(max(0.1,b-0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Max(0, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"],
                capture_output=True, timeout=5, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] Brightness down failed on Windows: {e}")

def close_app():
    if _OS == "Darwin": pyautogui.hotkey("command", "q")
    else:               pyautogui.hotkey("alt", "f4")

def close_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def full_screen():
    if _OS == "Darwin": pyautogui.hotkey("ctrl", "command", "f")
    else:               pyautogui.press("f11")

def minimize_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "m")
    else:               pyautogui.hotkey("win", "down")

def maximize_window():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True)
    elif _OS == "Windows":
        pyautogui.hotkey("win", "up")
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True)
        except Exception:
            pyautogui.hotkey("super", "up")

def snap_left():
    if _OS == "Windows":
        pyautogui.hotkey("win", "left")
    elif _OS == "Darwin":
        # macOS has no built-in snap; try Rectangle app shortcut if installed
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "option", "left")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def snap_right():
    if _OS == "Windows":
        pyautogui.hotkey("win", "right")
    elif _OS == "Darwin":
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "option", "right")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "tab")
    else:               pyautogui.hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   pyautogui.hotkey("fn", "f11")
    elif _OS == "Windows": pyautogui.hotkey("win", "d")
    else:                  pyautogui.hotkey("super", "d")

def open_task_manager():
    if _OS == "Windows":
        pyautogui.hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": pyautogui.hotkey("command", "l")
    else:               pyautogui.hotkey("ctrl", "l")

def pause_video():      pyautogui.press("space")

def refresh_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "r")
    else:               pyautogui.press("f5")

def close_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "t")
    else:               pyautogui.hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketright")
    else:               pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketleft")
    else:               pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": pyautogui.hotkey("command", "left")
    else:               pyautogui.hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": pyautogui.hotkey("command", "right")
    else:               pyautogui.hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": pyautogui.hotkey("command", "equal")
    else:               pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": pyautogui.hotkey("command", "minus")
    else:               pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": pyautogui.hotkey("command", "0")
    else:               pyautogui.hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "f")
    else:               pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": pyautogui.hotkey("command", "up")
    else:               pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": pyautogui.hotkey("command", "down")
    else:               pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


def copy():
    if _OS == "Darwin": pyautogui.hotkey("command", "c")
    else:               pyautogui.hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": pyautogui.hotkey("command", "v")
    else:               pyautogui.hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": pyautogui.hotkey("command", "x")
    else:               pyautogui.hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": pyautogui.hotkey("command", "z")
    else:               pyautogui.hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "z")
    else:               pyautogui.hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": pyautogui.hotkey("command", "a")
    else:               pyautogui.hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": pyautogui.hotkey("command", "s")
    else:               pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str): pyautogui.press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

def take_screenshot():
    if _OS == "Windows":
        pyautogui.hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        pyautogui.hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        pyautogui.hotkey("ctrl", "print_screen")

def lock_screen():
    if _OS == "Windows":
        pyautogui.hotkey("win", "l")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return

def open_system_settings():
    if _OS == "Windows":
        pyautogui.hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Windows":
        pyautogui.hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if _OS == "Windows":
        pyautogui.hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True)
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True
        )
        state = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state],
            capture_output=True)
    elif _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10, **_WIN_HIDE
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def sleep_computer() -> bool:
    """Issues a real OS suspend request — distinct from sleep_display()
    above (which only turns the MONITOR off, the display stays on
    standby with the system fully running). Returns True only if the OS
    ACCEPTED the suspend request; it does NOT mean the machine is now
    actually asleep — once a real suspend begins, the very process that
    issued it may be suspended before anything further could be checked,
    so "the call was accepted" is the strongest honest claim possible
    here (see the Universal JARVIS Computer Control Architecture design's
    own note on this exact limitation)."""
    if _OS == "Windows":
        try:
            import ctypes
            # SetSuspendState(bHibernate, bForce, bWakeupEventsDisabled) —
            # False/False/False = a normal, cooperative suspend (the same
            # kind clicking Start > Power > Sleep triggers), not a forced
            # hibernate.
            ok = ctypes.windll.powrprof.SetSuspendState(False, False, False)
            return bool(ok)
        except Exception as e:
            print(f"[Settings] sleep_computer (Windows) failed: {e}")
            return False
    elif _OS == "Darwin":
        try:
            r = subprocess.run(["pmset", "sleepnow"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception as e:
            print(f"[Settings] sleep_computer (macOS) failed: {e}")
            return False
    else:
        try:
            r = subprocess.run(["systemctl", "suspend"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception as e:
            print(f"[Settings] sleep_computer (Linux) failed: {e}")
            return False


def _bluetooth_radio_state() -> bool | None:
    """Best-effort ground-truth read of whether the Bluetooth radio is
    currently ON. None means "couldn't determine" — never guessed, never
    treated as either True or False by the caller."""
    if _OS == "Windows":
        try:
            # A Bluetooth-class PnP device list also contains protocol/
            # transport sub-devices (RFCOMM, AVRCP transports, the
            # Enumerator itself) that all report Status "Unknown" — only
            # the actual radio/adapter device reports a real "OK"/"Error"
            # state. Live-verified against this machine's real device
            # list: naively taking the first Bluetooth-class entry picked
            # an RFCOMM protocol driver (status "Unknown") instead of the
            # real "Intel(R) Wireless Bluetooth(R)" radio (status "OK"),
            # silently misreporting the radio as off when it was on —
            # filtering to OK/Error status specifically selects the radio.
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue "
                 "| Where-Object {$_.Status -in @('OK','Error')} "
                 "| Select-Object -First 1 -ExpandProperty Status)"],
                capture_output=True, text=True, timeout=8, **_WIN_HIDE,
            )
            status = (result.stdout or "").strip()
            if status:
                return status.upper() == "OK"
        except Exception as e:
            print(f"[Settings] _bluetooth_radio_state query failed: {e}")
    elif _OS == "Darwin":
        try:
            result = subprocess.run(["blueutil", "-p"], capture_output=True, text=True, timeout=5)
            out = (result.stdout or "").strip()
            if out in ("0", "1"):
                return out == "1"
        except Exception:
            pass  # blueutil is a third-party tool; not being installed is expected, not an error
    elif _OS == "Linux":
        try:
            result = subprocess.run(["rfkill", "list", "bluetooth"], capture_output=True, text=True, timeout=5)
            out = (result.stdout or "").lower()
            if "soft blocked: yes" in out or "hard blocked: yes" in out:
                return False
            if "bluetooth" in out:
                return True
        except Exception as e:
            print(f"[Settings] _bluetooth_radio_state (Linux) query failed: {e}")
    return None


def bluetooth_radio_set(state: bool) -> bool | None:
    """Attempts to set the Bluetooth radio on/off, then reads back the
    ACTUAL resulting state (ground truth) rather than assuming the
    command worked — returns that read-back value, or None if it
    couldn't be determined either way (e.g. no admin rights for the
    PnP-device toggle on Windows, or blueutil not installed on macOS) —
    the caller must treat None as "couldn't verify", never as success."""
    if _OS == "Windows":
        try:
            # Target ONLY the actual radio/adapter device (see
            # _bluetooth_radio_state's own comment on why Status OK/Error
            # identifies it) — not every Bluetooth-class sub-device
            # (RFCOMM/AVRCP transports etc.), which the original,
            # over-broad "not an Enumerator" filter would have also
            # tried to toggle individually.
            verb = "Enable-PnpDevice" if state else "Disable-PnpDevice"
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue "
                 f"| Where-Object {{$_.Status -in @('OK','Error')}} "
                 f"| ForEach-Object {{ {verb} -InstanceId $_.InstanceId "
                 f"-Confirm:$false -ErrorAction SilentlyContinue }}"],
                capture_output=True, timeout=10, **_WIN_HIDE,
            )
        except Exception as e:
            print(f"[Settings] bluetooth_radio_set (Windows) failed: {e}")
    elif _OS == "Darwin":
        try:
            subprocess.run(["blueutil", "-p", "1" if state else "0"], capture_output=True, timeout=5)
        except Exception as e:
            print(f"[Settings] bluetooth_radio_set (macOS, needs blueutil) failed: {e}")
    elif _OS == "Linux":
        try:
            subprocess.run(["rfkill", "unblock" if state else "block", "bluetooth"],
                capture_output=True, timeout=5)
        except Exception as e:
            print(f"[Settings] bluetooth_radio_set (Linux) failed: {e}")
    time.sleep(1.0)  # give the OS a moment to actually apply it before reading back
    return _bluetooth_radio_state()


def clipboard_get() -> str | None:
    if not _PYPERCLIP:
        return None
    try:
        return pyperclip.paste()
    except Exception as e:
        print(f"[Settings] clipboard_get failed: {e}")
        return None


def clipboard_set(text: str) -> bool:
    if not _PYPERCLIP:
        return False
    try:
        pyperclip.copy(text)
        return True
    except Exception as e:
        print(f"[Settings] clipboard_set failed: {e}")
        return False


def _wifi_state() -> bool | None:
    """Best-effort ground-truth read of whether Wi-Fi is currently
    enabled. None means "couldn't determine" — never guessed."""
    if _OS == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'} "
                 "| Select-Object -First 1 -ExpandProperty Status)"],
                capture_output=True, text=True, timeout=8, **_WIN_HIDE,
            )
            status = (result.stdout or "").strip()
            if status:
                return status.lower() == "up"
        except Exception as e:
            print(f"[Settings] _wifi_state query failed: {e}")
    elif _OS == "Darwin":
        try:
            iface = _get_macos_wifi_interface()
            result = subprocess.run(["networksetup", "-getairportpower", iface],
                capture_output=True, text=True, timeout=5)
            if result.stdout:
                return "On" in result.stdout
        except Exception:
            pass
    elif _OS == "Linux":
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=5)
            out = (result.stdout or "").strip().lower()
            if out:
                return out == "enabled"
        except Exception:
            pass
    return None


def restart_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/r", "/t", "10"], capture_output=True, **_WIN_HIDE)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/s", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True)

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "mute":                volume_mute,
    "unmute":              volume_mute,
    "toggle_mute":         volume_mute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         pause_video,
    "play_pause":          pause_video,
    "close_app":           close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
    # sleep / bluetooth_on / bluetooth_off / clipboard_get / clipboard_set
    # are NOT here — they're handled as dedicated branches in
    # computer_settings() below (each needs a verified Result Envelope
    # return, not the generic "Done: x." this map's callers get), so
    # listing them here too would be dead, unreachable code.
}

# Kept as the canonical dangerous-action list for THIS module's own
# reference/tests; the actual confirmation decision is delegated to
# result_envelope.is_consequential() (see computer_settings() below) so
# the same classifier is shared with computer_control.py's accomplish()
# rather than reimplemented here — see result_envelope.py's own
# _CONSEQUENTIAL_ACTION_NAMES, which this set must stay in sync with.
_DANGEROUS_ACTIONS = {"restart", "shutdown"}

# volume_set / toggle_wifi / sleep / bluetooth_on / bluetooth_off /
# clipboard_get / clipboard_set / restart / shutdown each have their own
# dedicated branch in computer_settings() below and return a full Result
# Envelope (see result_envelope.py) instead of a bare "Done: x." string —
# these are the actions with a genuine, verifiable ground-truth outcome.
# The ~50 other ACTION_MAP entries (window snapping, tab navigation,
# zoom, etc.) are fire-and-forget UI conveniences with no meaningful
# "verification" concept and are deliberately left returning their
# existing bare-string behavior — rewrapping all of them is out of scope
# and would touch working, already-tested code for no benefit.



def _detect_action(description: str) -> dict:

    from google import genai as _genai
    _client = _genai.Client(api_key=_get_api_key())

    available = ", ".join(sorted(ACTION_MAP.keys())) + \
                ", volume_set, type_text, press_key, reload_n"

    prompt = f"""You are an intent detector for a computer control assistant.

The user issued a command (possibly in any language): "{description}"

Available actions: {available}

Return ONLY a valid JSON object:
{{"action": "action_name", "value": null_or_value}}

Rules:
- Pick the single best matching action from the available list.
- For volume_set: value is an integer 0-100.
- For type_text: value is the exact text to type.
- For press_key: value is the key name (e.g. "f5", "tab", "enter").
- For reload_n: value is an integer (number of times to reload).
- If no clear match, pick the closest action.
- Return ONLY the JSON, no explanation, no markdown."""

    try:
        resp = _client.models.generate_content(model="gemini-flash-lite-latest", contents=prompt)
        text = re.sub(r"```(?:json)?", "", resp.text).strip().rstrip("`").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[Settings] Intent detection failed: {e}")
        return {"action": description.lower().replace(" ", "_"), "value": None}

def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return "pyautogui is not installed. Run: pip install pyautogui"

    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return "No action could be determined."

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    # Centralized risk/confirmation gate — the SAME classifier
    # actions/computer_control.py's accomplish() uses (see
    # actions/result_envelope.py), so confirmation logic is never
    # reimplemented per action. Backward-compatible with the pre-existing
    # confirmed="yes" string convention this function already shipped
    # with (is_confirmed() accepts both that and a real boolean).
    if _envelope.is_consequential(action_name=action) and not _envelope.is_confirmed(params):
        return _envelope.envelope(
            _envelope.STATUS_CONFIRMATION_REQUIRED,
            f"this will {action} the computer",
        )

    if action == "volume_set":
        try:
            target = max(0, min(100, int(value or 50)))
            volume_set(target)
            time.sleep(0.3)
            actual = get_volume()
            if actual is None:
                return _envelope.envelope(
                    _envelope.STATUS_INCONCLUSIVE,
                    f"volume_set({target}) was issued but the resulting level could not be read back",
                )
            if abs(actual - target) <= 2:  # small tolerance for rounding
                return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"volume is now {actual}%")
            return _envelope.envelope(
                _envelope.STATUS_VERIFIED_FAILURE,
                f"requested {target}% but the volume now reads {actual}%",
            )
        except Exception as e:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"could not set volume: {e}")

    if action == "toggle_wifi":
        before = _wifi_state()
        toggle_wifi()
        time.sleep(1.0)
        after = _wifi_state()
        if before is None or after is None:
            return _envelope.envelope(
                _envelope.STATUS_INCONCLUSIVE,
                "Wi-Fi toggle was issued but the resulting radio state could not be read back",
            )
        if after != before:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"Wi-Fi is now {'on' if after else 'off'}")
        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_FAILURE,
            f"Wi-Fi state did not change (still {'on' if before else 'off'})",
        )

    if action in ("bluetooth_on", "bluetooth_off"):
        want_on = action == "bluetooth_on"
        actual = bluetooth_radio_set(want_on)
        if actual is None:
            return _envelope.envelope(
                _envelope.STATUS_INCONCLUSIVE,
                "Bluetooth radio toggle was issued but the resulting state could not be read back "
                "(may need administrator privileges) — try accomplish() via the Settings UI instead",
            )
        if actual == want_on:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"Bluetooth radio is now {'on' if actual else 'off'}")
        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_FAILURE,
            f"Bluetooth radio is still {'on' if actual else 'off'}",
        )

    if action == "sleep":
        accepted = sleep_computer()
        if accepted:
            # Deliberately NOT claiming the machine IS asleep — see
            # sleep_computer()'s own docstring on why that can never be
            # confirmed from inside the process that just suspended itself.
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "the suspend request was accepted by the OS")
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "the OS did not accept the suspend request")

    if action in ("restart", "shutdown"):
        func = ACTION_MAP[action]
        try:
            func()
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"the {action} command was issued")
        except Exception as e:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"could not issue {action}: {e}")

    if action == "clipboard_get":
        text = clipboard_get()
        if text is None:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "clipboard could not be read")
        preview = text[:200] + ("…" if len(text) > 200 else "")
        # Not persisted anywhere — this is the tool's own return value,
        # same ephemeral lifetime as every other tool result.
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"clipboard contains: {preview}")

    if action == "clipboard_set":
        text = str(value or params.get("text", ""))
        if not text:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "no text given to place on the clipboard")
        ok = clipboard_set(text)
        if not ok:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, "could not write to the clipboard")
        after = clipboard_get()
        if after == text:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "clipboard now contains the requested text")
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "clipboard was written to but could not be read back to confirm")

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return f"Unknown action: '{raw_action}'."

    try:
        func()
        return f"Done: {action}."
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"