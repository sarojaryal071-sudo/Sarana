"""
actions/system_shortcuts.py — a DATA-DRIVEN library of fast, trustworthy
Windows system capabilities, built on the same principle that already
makes "Open YouTube" feel instant: skip generic UI discovery entirely
when a deterministic shortcut exists, and only verify against something
real (a settings URI that either opens or doesn't, a cmdlet's actual
output) — never a screen guess.

Two kinds of entries, both loaded from config/system_shortcuts.json so
adding a new capability is a data change, not a new Python module:

  - "panes"   — a specific Settings page, opened via the official
    ms-settings: URI scheme (source: Microsoft's own windows-dev-docs
    repo — see the registry's _meta.panes_source). Fire-and-forget: like
    computer_settings.py's existing open_system_settings()/
    open_file_explorer(), there's no real ground truth to read back
    (the OS call either raises or it doesn't), so this returns a plain
    honest string, NOT a Result Envelope status — wrapping it in
    VERIFIED_SUCCESS would be a claim this code cannot actually back up.

  - "queries" — a single built-in, read-only Windows cmdlet (Get-PnpDevice,
    Get-CimInstance, Get-NetIPConfiguration, Get-Printer,
    Get-NetFirewallProfile, netsh) whose OWN output IS the ground truth,
    so these DO return a Result Envelope status — the same shared
    result_envelope.py used by computer_control.py and
    computer_settings.py, so JARVIS reads one consistent set of
    [VERIFIED_SUCCESS]/[VERIFIED_FAILURE]/[INCONCLUSIVE] tags everywhere.

Matching a free-text request (e.g. "check bluetooth devices") to a
registry entry is deliberately a small, deterministic keyword/alias
scorer — NOT a second LLM call — matching this codebase's existing rule
that risk/dispatch decisions stay auditable and cheap
(see result_envelope.is_consequential()'s own docstring on this).

Nothing here performs a consequential action (send/delete/purchase/
uninstall/etc.) — every entry is either a read-only info query or an
already-reversible Settings-page open, so none of these need to pass
through result_envelope.is_consequential()'s confirmation gate; if a
future entry ever changes that (e.g. actually connecting/forgetting a
device), it must be added to result_envelope.py's classifier, not
special-cased here.
"""
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

import psutil

from actions import result_envelope as _envelope

_OS = platform.system()  # "Windows" | "Darwin" | "Linux" — the registry
                          # itself is Windows-only for now (ms-settings:
                          # and every seeded cmdlet are Windows-specific);
                          # see system_shortcut()'s own OS check below.

if _OS == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

_REGISTRY_CACHE: dict | None = None
_MATCH_THRESHOLD = 15  # minimum alias/keyword-overlap score to accept a
                        # match — below this, system_shortcut() honestly
                        # reports "no known shortcut" instead of guessing.


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _load_registry() -> dict:
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    path = _get_base_dir() / "config" / "system_shortcuts.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            _REGISTRY_CACHE = json.load(f)
    except Exception as e:
        print(f"[SystemShortcuts] Failed to load registry ({path}): {e}")
        _REGISTRY_CACHE = {"panes": [], "queries": []}
    return _REGISTRY_CACHE


def reload_registry() -> None:
    """Drops the cache so the next call re-reads the JSON file — used by
    tests, and safe to call if the registry file is hand-edited live."""
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None


# ── free-text -> registry entry matching (deterministic, no LLM call) ──

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).strip()


def _score(target_norm: str, aliases: list, name: str) -> int:
    best = 0
    for phrase in list(aliases or []) + [name or ""]:
        p = _normalize(phrase)
        if not p:
            continue
        if p == target_norm:
            return 100
        if p in target_norm or target_norm in p:
            best = max(best, 70)
            continue
        overlap = len(set(p.split()) & set(target_norm.split()))
        if overlap:
            best = max(best, overlap * 10)
    return best


def resolve(target: str):
    """Returns ("pane"|"query", entry_dict) for the best-scoring registry
    match, or None if nothing clears _MATCH_THRESHOLD — deliberately
    refuses to guess at a weak match rather than running the wrong
    shortcut (same 'don't guess, say so' principle as UI_AMBIGUOUS)."""
    target_norm = _normalize(target)
    if not target_norm:
        return None
    reg = _load_registry()
    best_kind, best_entry, best_score = None, None, 0
    for kind, key in (("pane", "panes"), ("query", "queries")):
        for entry in reg.get(key, []):
            s = _score(target_norm, entry.get("aliases", []), entry.get("name", ""))
            if s > best_score:
                best_kind, best_entry, best_score = kind, entry, s
    if best_score < _MATCH_THRESHOLD:
        return None
    return best_kind, best_entry


# ── panes: fire-and-forget deep link, no fabricated verification ───────

def open_pane(entry: dict) -> str:
    if _OS != "Windows":
        return f"'{entry.get('name', entry.get('id'))}' is a Windows-only shortcut."
    try:
        os.startfile(entry["uri"])
        return f"Opened {entry['name']} settings."
    except Exception as e:
        return f"Could not open {entry['name']} settings: {e}"


# ── queries: real cmdlet output, wrapped in the shared Result Envelope ─

def _parse_json_items(stdout: str):
    """Normalizes PowerShell's ConvertTo-Json output (a single object OR
    an array OR a bare scalar list, depending on how many rows matched)
    into always-a-list. Empty stdout and JSON `null` both mean 'the
    cmdlet ran and genuinely found zero rows' (Select-Object on an empty
    collection prints nothing) and become []; only NON-empty output that
    fails to parse as JSON — a real "something's wrong" signal — returns
    None, so the caller can tell 'zero results' apart from 'unreadable
    output' instead of treating every empty run as inconclusive."""
    stdout = (stdout or "").strip()
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
    except Exception:
        return None
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def _format_items(entry: dict, items: list) -> str:
    parts = []
    for item in items[:10]:
        if isinstance(item, dict):
            bits = [f"{k}: {v}" for k, v in item.items() if v not in (None, "")]
            parts.append(", ".join(bits) if bits else str(item))
        else:
            parts.append(str(item))
    more = f" (+{len(items) - 10} more)" if len(items) > 10 else ""
    return "; ".join(parts) + more


def _parse_netsh_networks(stdout: str) -> list:
    names = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("SSID") and ":" in line and "BSSID" not in line:
            name = line.split(":", 1)[1].strip()
            if name:
                names.append(name)
    return names


def _parse_netsh_interface(stdout: str) -> dict:
    info = {}
    wanted = {"SSID", "State", "Signal", "Radio type", "Channel", "Receive rate (Mbps)"}
    for line in (stdout or "").splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key in wanted and val:
            info[key] = val
    return info


# ── psutil handlers: in-process, no subprocess spawn at all ────────────
# For data psutil can already read directly (disk usage, per-interface
# IPs, battery, process list), this is strictly better than shelling out
# to PowerShell — faster (no process-spawn cost) and no dependency on
# PowerShell's own availability/execution policy. Each handler returns a
# list of dicts (same shape run_query()'s JSON path already produces) or
# [] for "ran fine, genuinely nothing to report" — never None, since a
# real exception already gets caught and reported by run_query() itself.

def _psutil_disk_usage() -> list:
    rows = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue  # e.g. an empty optical/removable drive — skip, don't fail the whole query
        rows.append({
            "Drive": part.device,
            "UsedGB": round(usage.used / (1024 ** 3), 1),
            "FreeGB": round(usage.free / (1024 ** 3), 1),
        })
    return rows

def _psutil_network_info() -> list:
    rows = []
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if getattr(addr.family, "name", "") == "AF_INET":
                rows.append({"Interface": iface, "IPv4": addr.address})
    return rows

def _psutil_battery_status() -> list:
    battery = psutil.sensors_battery()
    if battery is None:
        return []  # genuinely no battery (desktop) — not a failure
    return [{"Percent": round(battery.percent, 1), "PluggedIn": bool(battery.power_plugged)}]

def _psutil_running_processes() -> list:
    """Top processes by CURRENT cpu%, not Get-Process's old 'CPU' column
    (which was cumulative CPU TIME in seconds since launch — a genuinely
    different, easily-misread number the previous PowerShell version of
    this query used). psutil.cpu_percent() needs one 'priming' call
    before it's meaningful, so this deliberately takes a bounded 0.3s
    sample window rather than returning a guaranteed-wrong first-call
    0.0% for every process."""
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(0.3)
    rows = []
    for p in procs:
        try:
            rows.append({"Name": p.info.get("name") or "?", "CPU%": round(p.cpu_percent(None), 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    rows.sort(key=lambda r: r["CPU%"], reverse=True)
    return rows[:6]

_PSUTIL_HANDLERS = {
    "disk_usage": _psutil_disk_usage,
    "network_info": _psutil_network_info,
    "battery_status": _psutil_battery_status,
    "running_processes": _psutil_running_processes,
}


def run_query(entry: dict) -> str:
    name = entry.get("name", entry.get("id", "query"))

    if entry.get("kind") == "psutil":
        handler = _PSUTIL_HANDLERS.get(entry.get("handler"))
        if handler is None:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"no psutil handler registered for '{name}'")
        try:
            items = handler()
        except Exception as e:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"could not run the '{name}' query: {e}")
        if not items:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, entry.get("empty_message", "nothing was found"))
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, _format_items(entry, items))

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", entry["command"]],
            capture_output=True, text=True, timeout=10, **_WIN_HIDE,
        )
    except subprocess.TimeoutExpired:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"the '{name}' query timed out")
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"could not run the '{name}' query: {e}")

    stdout = result.stdout or ""
    if result.returncode != 0 and not stdout.strip():
        err = (result.stderr or "").strip()[:200] or "no output"
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"the '{name}' query failed: {err}")

    parser = entry.get("parser")
    if parser == "netsh_networks":
        names = _parse_netsh_networks(stdout)
        if not names:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, entry.get("empty_message", "nothing was found"))
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"found {len(names)} network(s): " + ", ".join(names[:15]))

    if parser == "netsh_interface":
        info = _parse_netsh_interface(stdout)
        if not info:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, entry.get("empty_message", "nothing was found"))
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, ", ".join(f"{k}: {v}" for k, v in info.items()))

    items = _parse_json_items(stdout)
    if items is None:
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"the '{name}' query returned output that could not be read")
    if not items:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, entry.get("empty_message", "nothing was found"))
    return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, _format_items(entry, items))


# ── public entry points (wired into computer_settings.py) ──────────────

def system_shortcut(target: str) -> str:
    target = (target or "").strip()
    if not target:
        return "No target given — say what you'd like to open or check (e.g. 'bluetooth devices', 'display settings')."
    if _OS != "Windows":
        return "The system shortcuts library currently only covers Windows."
    match = resolve(target)
    if not match:
        return (
            f"No known fast-path shortcut matches '{target}'. Call list_system_shortcuts to see "
            "what's available, or fall back to computer_control's accomplish() for a general UI action."
        )
    kind, entry = match
    return open_pane(entry) if kind == "pane" else run_query(entry)


def list_shortcuts() -> str:
    reg = _load_registry()
    pane_names = ", ".join(e["name"] for e in reg.get("panes", []))
    query_names = ", ".join(e["name"] for e in reg.get("queries", []))
    return (
        f"Settings pages I can open directly: {pane_names}. "
        f"Live info I can check directly: {query_names}."
    )
