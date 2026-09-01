"""
dashboard/server.py — JARVIS Local HTTP Dashboard

Plain HTTP on port 8000 (no SSL warnings, no firewall issues).
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography

── API/WebSocket boundary (Phase 3) ────────────────────────────────────────

GET /api/session
    Unauthenticated. Returns the minimum a future
    web client needs to initialize: {"assistant_name", "tools", "desktop_
    connected"}. "tools" is derived live from main.py's TOOL_DECLARATIONS
    (imported lazily, never hand-duplicated here) — plugin-provided tools
    aren't included yet, since that needs a live reference to a running
    JarvisLive/plugin registry that this phase deliberately doesn't wire up
    (would require touching main.py, out of scope this phase).

WSS /ws  (existing route, extended, fully backward compatible)
    New optional query param: role ("client" default, or "desktop"). Used
    only for /api/session's desktop_connected bookkeeping — no dispatch
    logic depends on it yet.
    Message types, client → server:
        "command"              — existing, unchanged behavior
        "image_command"        — web visual intelligence: a browser-
                                  submitted image ({"data": <base64>,
                                  "mime_type", "text"}), validated here and
                                  queued to self._image_command_queue; see
                                  main.py's _process_dashboard_image_
                                  commands(), which injects it into the
                                  SAME live Gemini session desktop's
                                  screen_process tool already uses
        "vision_frame"          — web visual context (camera OR screen —
                                  see Phase 4/5): one sampled frame
                                  ({"request_id", "seq", "mime_type",
                                  "data": <base64>}), validated here
                                  exactly like "image_command" and queued
                                  to self._vision_frame_queue; see main.py's
                                  web_camera_vision/web_screen_vision tools /
                                  _process_web_vision_frames(), which
                                  batches several frames into short
                                  "observation bursts" before injecting
                                  them into the same Gemini session. Which
                                  source it came from is tracked entirely
                                  by main.py's session state (via
                                  request_id) — this handler treats camera
                                  and screen frames identically
        "vision_control"        — web visual context: a client-side
                                  lifecycle signal for an active vision
                                  request ({"request_id", "action": "stop",
                                  "reason"}) — e.g. the user pressed Stop,
                                  or the browser's camera/screen share
                                  failed. Queued to self._vision_frame_queue
                                  alongside actual frames (main.py's
                                  consumer distinguishes them by the
                                  presence of "control")
        "device_action_result" — protocol shape reserved for Phase 6
                                  (Desktop Device Agent); recognized so it
                                  doesn't fall through as noise, but nothing
                                  acts on it yet — no pending-request
                                  matching exists until Phase 6
        (anything else)        — ignored, exactly as before this phase;
                                  an unrecognized type has never crashed
                                  this loop and still doesn't
    Message types, server → client (via broadcast()):
        "log", "status", "sys", "file_received" — existing, unchanged
        "content"               — new, see broadcast_content() below
        "image_error"           — a rejected "image_command" (bad type,
                                   too large, or not a real image) — sent
                                   directly back to the requesting socket,
                                   not broadcast
        "vision_error"           — a rejected "vision_frame" — sent
                                   directly back to the requesting socket,
                                   not broadcast, same shape as
                                   "image_error"
        "camera_vision_request"  — web live camera vision: server asks the
                                   browser to open its camera and start
                                   streaming sampled frames for a given
                                   request_id (see
                                   broadcast_camera_vision_request()) — a
                                   live, one-off signal, never replayed
                                   from history to a client that connects
                                   later, same treatment as
                                   "location_refresh_request"
        "camera_vision_stop"     — web live camera vision: server tells
                                   the browser to stop its camera stream
                                   for a given request_id (see
                                   broadcast_camera_vision_stop()) — same
                                   non-history treatment as
                                   "camera_vision_request"
        "screen_vision_request"  — web screen vision (Phase 4): server
                                   asks the browser to start screen
                                   sharing (getDisplayMedia) for a given
                                   request_id (see
                                   broadcast_screen_vision_request()) —
                                   mirrors "camera_vision_request" exactly
        "screen_vision_stop"     — web screen vision: server tells the
                                   browser to stop screen sharing for a
                                   given request_id (see
                                   broadcast_screen_vision_stop()) —
                                   mirrors "camera_vision_stop" exactly
        "device_action"         — protocol shape reserved for Phase 6;
                                   nothing sends this yet

WSS /ws/phone-audio  (unchanged — this IS the future audio-input channel,
    per the migration plan; not renamed, not modified)

── Phase 6 additions ────────────────────────────────────────────────────────

CORS: a small, explicit localhost allowlist (see _cors_allowed_origins()) is
added so a Vite dev server on a different port (e.g. http://localhost:5173)
can call the plain-HTTP routes above during local development. This does
NOT apply to the WebSocket routes — browsers don't enforce CORS on WS
handshakes, and none of /ws, /ws/phone-audio, /ws/audio-out change here.
Overridable via SARANA_ALLOWED_ORIGINS (comma-separated) for whichever
production frontend origin is configured later (e.g. a Vercel domain) —
never a bare wildcard, so the production backend is never opened to
arbitrary origins.

Startup PIN print: serve() now also prints one ready-to-use pairing PIN
(and its auto-login URL) to the server's own console on startup, in
addition to the pre-existing "Press Remote Control" message. This reuses
new_key() exactly as the desktop UI's Remote Control button already does —
it is not a new or weaker auth path, just a headless-friendly way to reach
the console output the desktop UI already shows visually, since
server_main.py has no window to click a button in.

── Production CORS fix ─────────────────────────────────────────────────────

Live-verified bug: the deployed Vercel frontend (https://sarana-psi.vercel.app)
got no Access-Control-Allow-Origin header from the Render backend at all
(confirmed with a direct curl against the live URL, not assumed) — the
CORSMiddleware mechanism itself was correct, but nothing had actually put
that origin into the allowlist yet. _cors_allowed_origins() now:
  1. Accepts EITHER SARANA_ALLOWED_ORIGINS (plural, the originally
     documented name) OR SARANA_ALLOWED_ORIGIN (singular) — removes the
     exact-name-guessing risk as a failure mode entirely.
  2. Strips whitespace AND a trailing slash from every configured origin —
     "https://x.vercel.app/" (trailing slash) silently would never match
     "https://x.vercel.app" under CORS's exact-string comparison, a very
     common way to configure this "correctly" and still have it fail.
  3. Includes this specific confirmed-production origin as a baked-in
     default, alongside the existing localhost dev defaults — so this
     already-known-real origin works even before/without Render's env var
     being set correctly, while the env var remains the primary, documented
     way to add or change a production origin later.

── Phase 8 additions ────────────────────────────────────────────────────────

POST /login/username — lightweight, temporary username IDENTIFICATION, not
authentication (no password, no registration, no accounts — see the route's
own docstring). Issues a token via the exact same mechanism /login (PIN)
already uses, so it works unchanged everywhere a PIN-issued token does.
Distinct from Remote Access: a username login never implies control of any
particular physical desktop (see has_desktop_connected()). set_username_
callback() is how the accepted name reaches JarvisLive — same wiring
pattern as set_wake_callback/set_connect_callback. Proper accounts/
passwords/registration/MFA remain explicitly deferred past this phase.

── Phase 9 addition ─────────────────────────────────────────────────────────

/login/username now also fires _wake_callback() (if wired) on success — a
username login IS the start signal for the web flow now, no separate WAKE
press needed. PIN-based Remote Access (/login, /auto-login, /api/device-
login) is intentionally untouched — it keeps requiring its own explicit
start where that matters.

── SQLite user/profile addition ────────────────────────────────────────────

/login/username is now real authentication against users/user_db.py's local
SQLite store (data/sarana.db), instead of accepting any non-empty name:
requires a matching "pin" field, verified with a salted PBKDF2 hash — see
that module's docstring for schema/seeding. set_username_callback() keeps
its exact old wiring/meaning (fires with the resolved display name, still
just a string) so everything built on it (ADDRESS clause, greeting re-arm)
is untouched; a new set_profile_callback() fires separately with the full
structured profile dict (nickname/pronunciation/gender/assistant_name/
voice_preference/language_preference — never pin_hash) for main.py's new
[USER PROFILE] context block. The response shape returned to the browser
is unchanged ({"ok", "token", "username"}) — profile details never reach
the frontend.

── Resource-cleanup addition ───────────────────────────────────────────────

Every issued token used to live in _tokens/_token_keys/_session_auth_mode/
_session_usernames/_session_timezones forever — nothing ever removed one.
POST /api/logout now removes a token's bookkeeping immediately and
explicitly (see _forget_token()); a passive, rate-limited TTL sweep
(_purge_stale_tokens(), hooked into _auth()) catches anything that never
called it (browser closed/crashed). Neither touches _device_sessions (persistent device pairing, meant to
outlive a session) or has any effect on an already-open /ws,
/ws/audio-out, or /ws/phone-audio connection, which are torn down
client-side as before.

── Location foundation addition ────────────────────────────────────────────

POST /api/location accepts a one-shot browser navigator.geolocation fix
(latitude/longitude/accuracy only — never a periodic stream, never
continuous tracking) from an already-authenticated session, validated
server-side, and forwards it to main.py's _set_session_location() along
with the REQUESTING token's own canonical username (see
_session_canonical_owner — populated at /login/username, cleaned up in
_forget_token() alongside every other per-token dict). This lets
JarvisLive detect and drop a delayed fix from a login that is no longer
the active identity, without the browser ever being trusted to say who it
is. Location itself is never stored here — this file only ever forwards
it; see main.py for the actual (session-only, never-persisted) storage.
"""

import asyncio
import base64
import hashlib
import io
import math
import os
import re
import secrets
import socket
import string
import time
from pathlib import Path

from users import user_db
from core.latency_stats import LatencyStats
from actions import calendar_store, calendar_auth

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
# Deployment readiness: Render (and most PaaS hosts) assign the port to
# listen on via the PORT environment variable — the app must bind to
# whatever it says, not a fixed value. Defaults to 8000 unchanged for
# local/desktop development, where PORT is normally unset.
PORT        = int(os.environ.get("PORT", 8000))
MAX_UPLOAD_MB = 500

# Web visual intelligence (browser-submitted image -> existing Gemini Live
# session): validation constants only. The actual compression/injection
# reuses actions/screen_processor.py's own _compress() and main.py's own
# session.send_client_content() — see main.py's
# _process_dashboard_image_commands(). This is deliberately a SEPARATE,
# smaller cap than MAX_UPLOAD_MB above — that one guards the unrelated
# phone->desktop file-sharing feature (/api/upload, arbitrary files up to
# 500MB saved to disk); an image headed into one JSON WebSocket message is
# capped far tighter, and is never written to disk at all.
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES       = 15 * 1024 * 1024   # 15 MB raw, pre-compression
MAX_IMAGE_B64_CHARS   = (MAX_IMAGE_BYTES // 3 + 1) * 4 + 256   # base64 expands ~4/3; +slack

# Guarded like _UPLOAD_OK above — Pillow is already a dependency elsewhere
# in this project (actions/screen_processor.py, actions/file_processor.py)
# but is imported defensively here too, since this module must still import
# cleanly (headless/Render) even if it were ever missing.
_PIL_OK = False
try:
    from PIL import Image as _PILImage
    _PIL_OK = True
except ImportError:
    pass


def _looks_like_a_real_image(raw: bytes) -> bool:
    """Hard reject for malformed/non-image payloads pretending to have an
    image MIME type — Image.verify() raises on anything it can't actually
    decode. Deliberately stricter than actions/screen_processor.py's own
    _compress(), which silently falls back to the original bytes on a
    decode failure (fine for a trusted local OS capture, not fine for
    untrusted browser input) — this check runs BEFORE _compress() ever
    sees the bytes. Only called when _PIL_OK is True."""
    try:
        _PILImage.open(io.BytesIO(raw)).verify()
        return True
    except Exception:
        return False

# Resource-cleanup fix: a passive safety net for tokens no explicit
# POST /api/logout ever cleaned up (browser closed/crashed, network died
# mid-session). 24h is generous — this is not primary auth expiry (tokens
# still work for a normal day-long session), just a bound so an
# abandoned token can't live in memory forever. Swept lazily (see _auth()
# in _build_app()), rate-limited by _TOKEN_SWEEP_INTERVAL_SECONDS so a
# busy server isn't scanning every token dict on every single request.
_TOKEN_TTL_SECONDS = 24 * 60 * 60
_TOKEN_SWEEP_INTERVAL_SECONDS = 10 * 60


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()

def _get_gemini_key() -> str | None:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return _json.load(f).get("gemini_api_key")
    except Exception:
        return None


def _get_assistant_name() -> str:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            name = (_json.load(f).get("assistant_name") or "").strip()
            return name or "SARANA"
    except Exception:
        return "SARANA"


def _session_tools() -> list[dict]:
    """Capability list for GET /api/session, derived live from the brain's
    own TOOL_DECLARATIONS — never hand-duplicated here. Imported lazily
    (not at module load time) so dashboard/server.py never pays the cost,
    or risk, of importing main.py just by being imported itself.

    Plugin-provided tools are intentionally NOT included: that needs a live
    reference to a running JarvisLive/plugin registry, which this phase
    doesn't wire up (would require modifying main.py, out of scope here).
    """
    try:
        from main import TOOL_DECLARATIONS
        return [
            {"name": t.get("name"), "description": t.get("description", "")}
            for t in TOOL_DECLARATIONS
        ]
    except Exception as e:
        print(f"[Dashboard] Could not load tool declarations: {e}")
        return []

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'JARVIS-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw      = base64.b64decode(enc_b64)
    iv, ct   = raw[:16], raw[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN  = ("https://cdnjs.cloudflare.com/ajax/libs/"
                  "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"JARVIS Dashboard Port {port}"
        prog_rule  = "JARVIS Dashboard Python"
        py_exe     = sys.executable

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port    = not _netsh_rule_exists(port_rule)
        need_prog    = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                'Get-NetConnectionProfile | '
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rule}" protocol=TCP dir=in '
                f'localport={port} action=allow'
            )
        if need_prog:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = subprocess.run(
                [bat_path], capture_output=True, timeout=8, shell=True
            )
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,       # hwnd  (no parent window)
                "runas",    # verb  (request elevation)
                bat_path,   # file  (our .bat)
                None,       # params
                None,       # working dir
                0,          # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print("[Dashboard] Phone connections may fail until JARVIS is run as Administrator.")
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = subprocess.run(
                [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = subprocess.run(
                [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
            subprocess.run(
                ["osascript", "-e",
                 f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                 f' with administrator privileges'],
                timeout=60,
            )
        except Exception:
            pass  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


_DEFAULT_DEV_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",   # Vite default
    "http://localhost:3000", "http://127.0.0.1:3000",   # common alt dev port
    "https://sarana-psi.vercel.app",                    # confirmed production frontend — see module docstring
]


def _normalize_origin(origin: str) -> str:
    """CORS origin comparison is an exact string match — a trailing slash
    or stray whitespace silently makes an otherwise-"correct" value never
    match. Strip both so a value like "https://x.vercel.app/ " still works."""
    return origin.strip().rstrip("/")


def _cors_allowed_origins() -> list[str]:
    """Explicit origin allowlist for the plain-HTTP API routes (not the
    WebSocket routes — browsers don't apply CORS to WS handshakes).
    Defaults to common local Vite/dev-server ports plus the confirmed
    production frontend; SARANA_ALLOWED_ORIGINS (comma-separated) — or its
    singular alias SARANA_ALLOWED_ORIGIN, accepted for the same purpose —
    extends this for whichever origin(s) come next. Never a wildcard, so
    this never broadly opens the production backend to arbitrary origins.
    """
    extra = (
        os.environ.get("SARANA_ALLOWED_ORIGINS")
        or os.environ.get("SARANA_ALLOWED_ORIGIN")
        or ""
    )
    origins = [_normalize_origin(o) for o in _DEFAULT_DEV_ORIGINS]
    if extra:
        origins += [_normalize_origin(o) for o in extra.split(",") if o.strip()]
    # Dedupe while preserving order (a user-supplied origin may repeat a default).
    seen: set[str] = set()
    deduped = []
    for o in origins:
        if o not in seen:
            seen.add(o)
            deduped.append(o)
    return deduped


def _default_frontend_origin() -> str:
    """Fallback redirect target for the Google OAuth callback (see
    /auth/google/callback) when the browser didn't supply a valid
    `return_to` — the confirmed production frontend, same constant
    already relied on elsewhere in this file (see _DEFAULT_DEV_ORIGINS)."""
    for origin in _cors_allowed_origins():
        if "localhost" not in origin and "127.0.0.1" not in origin:
            return origin
    return _DEFAULT_DEV_ORIGINS[-1]


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self):
        # Idempotent: creates data/sarana.db + seeds the known profiles on
        # first run, no-ops (preserving existing data) on every run after —
        # see users/user_db.py. Safe to call unconditionally here since a
        # DashboardServer is constructed exactly once per process, both on
        # desktop and headless/web.
        user_db.init_db()
        self._ip                          = _local_ip()
        self._tokens: set[str]            = set()
        self._token_keys: dict[str, str]  = {}   # auth_token → session_key
        self._aes_cache:  dict[str, bytes]= {}   # session_key → AES bytes
        self._clients: set[WebSocket]     = set()
        self._client_roles: dict[WebSocket, str] = {}   # ws → "client" | "desktop" (Phase 3 bookkeeping only)
        self._audio_out_clients: set[WebSocket] = set()  # /ws/audio-out subscribers (Phase 4)
        # Audio-out backpressure fix: one bounded queue + one dedicated
        # sender task per /ws/audio-out client (see _register_audio_client()/
        # _audio_sender_loop()/broadcast_audio() below) — replaces the old
        # unbounded asyncio.create_task() per chunk. Keyed by the same
        # WebSocket objects as self._audio_out_clients.
        self._audio_out_queues: dict[WebSocket, "asyncio.Queue"] = {}
        self._audio_out_senders: dict[WebSocket, "asyncio.Task"] = {}
        self._audio_out_dropped: int = 0   # instrumentation — same pattern as _phone_audio_dropped below
        self._history: list[dict]         = []
        self._command_queue               = asyncio.Queue()
        # Web visual intelligence: a second, separate queue (never mixed
        # into _command_queue's plain-text items) — see main.py's
        # _process_dashboard_image_commands(), which mirrors
        # _process_dashboard_commands() one-for-one but injects an
        # inline_data image part alongside the text.
        self._image_command_queue         = asyncio.Queue()
        # Web LIVE camera vision: a third, separate queue — never mixed
        # into _command_queue or _image_command_queue. Carries validated
        # sampled camera frames AND lifecycle "vision_control" signals
        # (e.g. client-side stop) — see main.py's
        # _process_web_vision_frames(), which batches frames into short
        # observation bursts before injecting them into the Gemini session.
        self._vision_frame_queue          = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._username_callback           = None   # Phase 8: fires on a successful /login/username
        self._interrupt_callback          = None   # web interrupt control: fires main.py's interrupt()
        self._timezone_callback           = None   # fires on a successful /login/username with a timezone
        self._profile_callback            = None   # fires with the full users/user_db.py profile dict
        self._logout_callback             = None   # fires on /api/logout of a username session (memory cache reset)
        self._location_callback           = None   # fires on a successful POST /api/location
        self._capabilities_callback       = None   # fires on a successful POST /api/capabilities
        self._session_timezones: dict[str, str] = {}   # token → IANA timezone (username logins only)
        # Location foundation: the token's own canonical username (e.g.
        # "sana", NOT the display name "Saanaa" already tracked in
        # _session_usernames below — a different, existing dict used for a
        # different purpose). Populated at /login/username, passed through
        # to the location callback so main.py's _set_session_location()
        # can tell whether a location update still belongs to whichever
        # identity is CURRENTLY active — see that method's own docstring
        # for the exact race this guards against. "" for a Remote Access
        # (PIN) token, which has no associated username at all.
        self._session_canonical_owner: dict[str, str] = {}
        # Google Calendar OAuth: short-lived, single-use CSRF/identity-
        # binding state tokens for the /auth/google -> Google consent ->
        # /auth/google/callback round trip (see those routes' own
        # docstrings). state -> {"owner": canonical username, "return_to":
        # validated frontend origin, "expires": unix time}. Consumed
        # (popped) on first use at the callback — a delayed/replayed
        # callback with an already-used or expired state is rejected
        # outright, which is what stops a stale callback from ever
        # attaching credentials to the wrong identity.
        self._google_oauth_states: dict[str, dict] = {}
        # Phase 8: lightweight session bookkeeping — which auth path issued a
        # token, and (for username logins only) which name. Not a user
        # database, no registration, nothing persisted past process
        # lifetime — see /login/username's docstring for why.
        self._session_auth_mode: dict[str, str] = {}   # token → "username" | "remote"
        self._session_usernames: dict[str, str] = {}   # token → username (username logins only)
        # Resource-cleanup fix: every token used to live in these dicts/set
        # forever — nothing ever removed one, so a long-lived process
        # (or the same account logging in/out repeatedly) grew them
        # without bound. _token_created_at + _purge_stale_tokens() is the
        # passive safety net (a token untouched by an explicit logout
        # eventually gets swept — see _TOKEN_TTL_SECONDS); POST /api/logout
        # (see _build_app()) is the active/immediate path a real logout
        # takes. Both funnel through the one _forget_token() below.
        self._token_created_at: dict[str, float] = {}
        self._last_token_sweep: float = 0.0
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}  # device_token → {session_key}
        self._phone_audio_queue: asyncio.Queue    = asyncio.Queue(maxsize=200)
        self._phone_audio_dropped: int            = 0   # instrumentation — see phone_audio_ws's QueueFull handler
        self._phone_audio_queue_depth              = LatencyStats()   # instrumentation — item 3 audit
        self._phone_audio_frames: int             = 0   # sample counter for the periodic depth log above
        self._uploads_dir                 = UPLOADS_DIR
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        self.app                          = self._build_app()

    # ── one-time key management ───────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        certs = BASE_DIR / "config" / "certs"
        return (certs / "jarvis.key").exists() and (certs / "jarvis.crt").exists()

    def get_url(self) -> str:
        proto = "https" if self._ssl_enabled() else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry. When HTTPS active, points to alias port (also HTTPS)."""
        if self._ssl_enabled():
            return f"{self._ip}:{PORT + 1}"
        return f"{self._ip}:{PORT}"

    def has_desktop_connected(self) -> bool:
        """True if any currently-connected /ws client identified itself with
        role=desktop. No such client exists yet (that's Phase 6) — this is
        honest bookkeeping infrastructure, not a claim that one is present.
        """
        return "desktop" in self._client_roles.values()

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    def set_username_callback(self, fn) -> None:
        """Phase 8: fn(username: str) is called once per successful
        /login/username — this is how a web session's name reaches
        JarvisLive (see main.py's run(), mirrors set_wake_callback/
        set_connect_callback's exact wiring pattern)."""
        self._username_callback = fn

    def set_interrupt_callback(self, fn) -> None:
        """fn() is called on a successful POST /api/interrupt — the web
        equivalent of the desktop UI's INTERRUPT button/Esc key
        (ui.py's on_interrupt), reusing the exact same JarvisLive.interrupt()
        (main.py's run() wires this identically to set_wake_callback)."""
        self._interrupt_callback = fn

    def set_timezone_callback(self, fn) -> None:
        """fn(tz_name: str) is called on a successful /login/username that
        includes a "timezone" field — the browser's own IANA timezone name
        (e.g. Intl.DateTimeFormat().resolvedOptions().timeZone), so
        JarvisLive can use the device's actual local time instead of the
        server's (see main.py's _local_now())."""
        self._timezone_callback = fn

    def set_profile_callback(self, fn) -> None:
        """fn(profile: dict) is called on a successful /login/username,
        with the authenticated user's full users/user_db.py profile
        (nickname, pronunciation, gender, assistant_name, voice_preference,
        language_preference — never pin_hash). Separate from
        set_username_callback(), which keeps firing with just the resolved
        display-name string for the existing ADDRESS-clause/greeting
        wiring (see main.py's _set_web_username)."""
        self._profile_callback = fn

    def set_logout_callback(self, fn) -> None:
        """fn() is called on a successful POST /api/logout of a username
        session ONLY (never Remote Access/PIN — same scoping
        _reset_activity_history() already uses) — main.py wires this to
        _clear_memory_session() so the in-RAM PostgreSQL memory cache
        never lingers past logout."""
        self._logout_callback = fn

    def set_location_callback(self, fn) -> None:
        """fn(latitude: float, longitude: float, accuracy: float, owner: str,
        fix_timestamp: float | None) is called on a successful POST
        /api/location — the browser's one-shot navigator.geolocation fix
        (see frontend/src/lib/geolocation.js), never a periodic stream.
        `owner` is the REQUESTING token's own canonical username (see
        self._session_canonical_owner — "" for a Remote Access/PIN
        token), resolved here server-side from the token alone, never
        from anything the request body itself claims — main.py wires
        this to _set_session_location(), which uses `owner` to detect
        and drop a delayed update from a login that is no longer the
        active identity. `fix_timestamp` is the browser's own fix time
        (epoch ms, None if the client didn't send one) — see
        location_ep()'s own docstring for why."""
        self._location_callback = fn

    def set_capabilities_callback(self, fn) -> None:
        """fn(microphone: str | None, location: str | None, requester_owner: str)
        is called on a successful POST /api/capabilities — the browser's
        own REAL permission state for a capability it can observe
        directly (Permissions API query/request outcome, or an actual
        getUserMedia/getCurrentPosition attempt's own result — see
        frontend/src/lib/permissions.js), never a fabricated client-only
        toggle. Each value is one of "granted" | "denied" | "prompt" |
        "unsupported", or None if that particular capability wasn't part
        of this update. `requester_owner` mirrors set_location_callback()'s
        own `owner` — the REQUESTING token's own canonical username,
        resolved here server-side from the token alone, so main.py's
        _set_session_capabilities() can apply the exact same stale-update
        protection _set_session_location() already does."""
        self._capabilities_callback = fn

    # ── token/session cleanup ────────────────────────────────────────────

    def _forget_token(self, tok: str) -> None:
        """Removes every bookkeeping entry associated with one session
        token — the single place that happens, used identically by
        POST /api/logout (immediate, explicit) and _purge_stale_tokens()
        (passive TTL safety net). Idempotent: calling this on an unknown
        or already-removed token is a harmless no-op, so logging out an
        already-invalid/expired token never errors.

        Does NOT touch self._device_sessions (persistent "remember this
        device" pairing — intentionally outlives any one session token)
        or self._aes_cache (keyed by PIN/session_key, not by token; shared
        across whichever tokens were derived from the same PIN).
        """
        self._tokens.discard(tok)
        self._token_created_at.pop(tok, None)
        self._token_keys.pop(tok, None)
        self._session_auth_mode.pop(tok, None)
        self._session_usernames.pop(tok, None)
        self._session_timezones.pop(tok, None)
        self._session_canonical_owner.pop(tok, None)

    def _purge_stale_tokens(self) -> None:
        now = time.time()
        stale = [
            tok for tok, created in self._token_created_at.items()
            if now - created > _TOKEN_TTL_SECONDS
        ]
        for tok in stale:
            self._forget_token(tok)

    def _purge_stale_oauth_states(self) -> None:
        """Same passive-safety-net idea as _purge_stale_tokens(), for
        Google OAuth states a user started but never completed (closed
        the consent screen, network died mid-flow, etc.) — called
        opportunistically whenever a new state is created (see
        /auth/google) rather than needing its own background task."""
        now = time.time()
        stale = [s for s, v in self._google_oauth_states.items() if v["expires"] < now]
        for s in stale:
            self._google_oauth_states.pop(s, None)

    def _reset_activity_history(self) -> None:
        """Clears self._history — the Activity Log's data (see broadcast()
        below and ws_ep()'s initial-history replay). Called on every
        successful /login/username AND on /api/logout, so a fresh /ws
        connection's replay-the-last-50-entries behavior can never hand a
        new login a previous user's conversation: the leak wasn't in the
        frontend's own state (RESET_FOR_LOGOUT already cleared that
        correctly) — it was that a brand new /ws connection, opened right
        after login, immediately received this GLOBAL, never-cleared
        history and fed it straight back into the "already cleared"
        frontend state. This is the Activity Log only — completely
        separate from and never touches the persistent memory system
        (memory/memory_manager.py)."""
        self._history = []

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        await self._send_to_clients(msg)

    async def _send_to_clients(self, msg: dict) -> None:
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def broadcast_state(self, state: str) -> None:
        """Server -> client "status" message carrying SARANA's real,
        granular operational state (LISTENING/THINKING/SPEAKING/SLEEPING)
        -- see main.py's _push_state(), the one place this is called from.
        Deliberately NOT routed through broadcast()/self._history: this is
        live, ephemeral state, not conversation content -- replaying a
        stale "THINKING" from minutes ago to a freshly (re)connected /ws
        client would be meaningless, and a state change fires far more
        often than real Activity Log entries (every tool call, every
        speaking start/stop), so folding it into the same 300-slot window
        broadcast() caps would push out actual conversation history much
        faster for no benefit. Same {"type": "status", "state": ...}
        message shape the frontend already understands (see
        AssistantContext.jsx's STATUS_MESSAGE case)."""
        await self._send_to_clients({"type": "status", "state": state})

    async def broadcast_location_refresh_request(self) -> None:
        """Location capabilities: server -> client signal asking the
        browser to take a fresh navigator.geolocation fix right now (see
        main.py's _get_current_location()) -- reuses the EXISTING /ws
        connection status/log/content messages already travel over, no
        new transport. Same reasoning as broadcast_state() for staying
        out of self._history: this is a live, one-off request, not
        conversation content worth replaying to a client that connects
        later."""
        await self._send_to_clients({"type": "location_refresh_request"})

    async def broadcast_camera_vision_request(self, request_id: str, facing: str) -> None:
        """Web live camera vision: server -> client signal asking the
        browser to open its camera and start streaming sampled frames for
        the given request_id (see main.py's web_camera_vision tool /
        _process_web_vision_frames()). Same "live, one-off signal, not
        conversation content" reasoning as
        broadcast_location_refresh_request() above -- never routed through
        broadcast()/self._history, so a client that connects later never
        receives a stale "open your camera" replay."""
        await self._send_to_clients({
            "type": "camera_vision_request",
            "request_id": request_id,
            "facing": facing,
        })

    async def broadcast_camera_vision_stop(self, request_id: str) -> None:
        """Web live camera vision: server -> client signal telling the
        browser to stop its camera stream for the given request_id --
        fired once the backend's own observation session ends (answered,
        timed out, or explicitly stopped). Same non-history reasoning as
        broadcast_camera_vision_request() above."""
        await self._send_to_clients({
            "type": "camera_vision_stop",
            "request_id": request_id,
        })

    async def broadcast_screen_vision_request(self, request_id: str) -> None:
        """Web screen vision (Phase 4): server -> client signal asking the
        browser to start screen sharing (getDisplayMedia -- see
        main.py's web_screen_vision tool / lib/screenVision.js) and stream
        sampled frames for the given request_id. Mirrors
        broadcast_camera_vision_request() exactly -- same non-history
        reasoning, same "live, one-off signal" treatment. No `facing`
        (screen capture has no camera direction)."""
        await self._send_to_clients({
            "type": "screen_vision_request",
            "request_id": request_id,
        })

    async def broadcast_screen_vision_stop(self, request_id: str) -> None:
        """Web screen vision: server -> client signal telling the browser
        to stop screen sharing for the given request_id. Mirrors
        broadcast_camera_vision_stop() exactly."""
        await self._send_to_clients({
            "type": "screen_vision_stop",
            "request_id": request_id,
        })

    async def broadcast_jarvis_mode(self, active: bool) -> None:
        """JARVIS Mode: server -> client signal that self._jarvis_mode just
        changed (see main.py's jarvis_mode tool) -- the backend owns the
        authoritative mode state, this is purely a notification so the web
        frontend can render the right visual mode (Orb vs SaranaFace --
        see App.jsx). Same non-history reasoning as broadcast_state()/
        broadcast_camera_vision_request() above: a client that connects
        later gets no replay of this (JARVIS mode is session-scoped and
        already resets to off on a fresh connection either way -- see
        self._jarvis_mode's own docstring -- so a freshly-connected client
        defaulting to "off" until told otherwise is correct, not a gap)."""
        await self._send_to_clients({"type": "jarvis_mode_changed", "active": bool(active)})

    async def broadcast_expression_override(self, expression: str, duration_seconds: float) -> None:
        """SARANA Face UI: server -> client signal that main.py's
        set_expression tool was just called (see main.py's own dispatch
        branch) — a temporary, explicit mood override on top of whatever
        SaranaFace.jsx's mechanical status->expression mapping would
        otherwise show (see lib/faceExpressions.js's resolveExpression()).
        Same non-history reasoning as broadcast_jarvis_mode() above: a
        client that connects later gets no replay of this — a stale
        "sad" override from a session nobody's watching anymore is not a
        state a fresh connection should inherit."""
        await self._send_to_clients({
            "type": "expression_override",
            "expression": expression,
            "duration_ms": int(max(0.0, duration_seconds) * 1000),
        })

    async def broadcast_content(self, title: str, text: str) -> None:
        """Server→client "content" message — mirrors JarvisUI.show_content's
        shape for a future web client. Nothing calls this yet (main.py is
        untouched this phase); it exists so a later phase has a ready-made
        method instead of hand-building the dict at each call site.
        """
        await self.broadcast({"type": "content", "title": title, "text": text})

    # ── Audio-out backpressure fix ───────────────────────────────────────
    # Root cause this replaces: _play_audio() used to fan audio out via a
    # brand-new, unawaited asyncio.create_task(broadcast_audio(chunk)) per
    # ~200ms batch, with no queue and no cap on how many of those could be
    # in flight against one client at once. On a client whose downlink
    # (e.g. a phone on cellular) is ever slower than real-time audio, sends
    # piled up — concurrent, unordered, unbounded — and got progressively
    # worse turn over turn. Fix: exactly the same "bounded queue + one
    # dedicated consumer" pattern already used everywhere else in this
    # codebase (main.py's out_queue/audio_in_queue/_phone_audio_queue) —
    # one queue and one sender task per client, so sends to a given client
    # are always strictly serialized and ordered, a slow client only ever
    # backs up its OWN queue (bounded, with drop-and-count backpressure —
    # never delivery to any other client, and never unbounded memory/task
    # growth), and broadcast_audio() itself is a fast, non-blocking
    # put_nowait — it never does network I/O and never needs to be wrapped
    # in its own task by callers.

    _AUDIO_OUT_QUEUE_MAXSIZE = 40   # ≈8s of audio at ~200ms/batch — enough slack for brief jitter, bounded either way

    def _register_audio_client(self, ws: WebSocket) -> None:
        """Wire up one /ws/audio-out client: adds it to self._audio_out_clients
        and gives it its own bounded queue + dedicated sender task (see
        _audio_sender_loop()). Idempotent — safe to call more than once for
        the same client."""
        self._audio_out_clients.add(ws)
        if ws in self._audio_out_queues:
            return
        queue = asyncio.Queue(maxsize=self._AUDIO_OUT_QUEUE_MAXSIZE)
        self._audio_out_queues[ws] = queue
        self._audio_out_senders[ws] = asyncio.create_task(self._audio_sender_loop(ws, queue))

    async def _unregister_audio_client(self, ws: WebSocket) -> None:
        """Reverses _register_audio_client(): drops the client and cancels
        its sender task cleanly (awaiting the cancellation so the task is
        actually gone, not just requested to stop, before this returns) —
        called from audio_out_ws()'s finally block on disconnect."""
        self._audio_out_clients.discard(ws)
        self._audio_out_queues.pop(ws, None)
        task = self._audio_out_senders.pop(ws, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _audio_sender_loop(self, ws: WebSocket, queue: "asyncio.Queue") -> None:
        """The ONLY coroutine ever allowed to call ws.send_bytes() for this
        client — one dedicated consumer per client guarantees sends are
        always strictly serialized (never concurrent) and always delivered
        in the order they were queued, with no help needed from a lock.
        Exits (and lets the finally below prune this client) the moment a
        send fails — same "one client's failure never affects the others"
        isolation broadcast_audio() always had, just enforced per-client
        instead of per-broadcast-call now."""
        try:
            while True:
                chunk = await queue.get()
                try:
                    await ws.send_bytes(chunk)
                except Exception:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self._audio_out_clients.discard(ws)
            self._audio_out_queues.pop(ws, None)
            self._audio_out_senders.pop(ws, None)

    async def broadcast_audio(self, chunk: bytes) -> None:
        """Fan out one raw PCM16 audio chunk — the exact same bytes main.py's
        _play_audio() just wrote to the local speaker — to every currently
        connected /ws/audio-out client (Phase 4), via each client's own
        bounded queue (see _register_audio_client()/_audio_sender_loop()
        above) rather than a direct/concurrent send from here.

        Never blocks and never raises: this is just a put_nowait per client
        (no network I/O happens in this method at all any more), so callers
        (main.py's audio loop) can await it directly without wrapping it in
        their own task and without it ever delaying or interrupting local
        playback. A client whose queue is already full (its own downlink
        can't keep up with real-time audio) has its chunk dropped — with a
        rate-limited counter, same pattern as main.py's out_queue/
        _phone_audio_queue — rather than piling up unboundedly; delivery to
        every other client is completely unaffected.
        """
        for ws in list(self._audio_out_clients):
            queue = self._audio_out_queues.get(ws)
            if queue is None:
                continue   # registered client with no queue yet/already torn down — skip safely
            try:
                queue.put_nowait(chunk)
            except asyncio.QueueFull:
                self._audio_out_dropped += 1
                if self._audio_out_dropped % 50 == 1:
                    print(
                        f"[Dashboard] audio-out queue full for a client — "
                        f"{self._audio_out_dropped} chunk(s) dropped so far"
                    )

    async def broadcast_audio_stop(self) -> None:
        """Server → client signal telling the browser to immediately flush
        whatever assistant audio it already received and may still have
        scheduled to play (see frontend/src/lib/audioOut.js's stopPlayback()
        and App.jsx's "audio_stop" case) — fired the moment Gemini's own
        server-side barge-in detection reports the user started talking
        over SARANA (see main.py's `sc.interrupted` handling). Deliberately
        NOT routed through broadcast()/self._history, for the same reason
        broadcast_state()/broadcast_location_refresh_request() aren't: this
        is live, one-off signaling, not conversation content worth
        replaying to a client that connects later."""
        await self._send_to_clients({"type": "audio_stop"})

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        # Startup diagnostic ONLY -- booleans, never values. Runs exactly
        # once per process (_build_app() is called once at server startup),
        # so this is the one place that reliably answers, from the Render
        # log tab alone, "why does /auth/google say Calendar isn't
        # available" without ever needing to print a secret. See
        # actions/calendar_auth.py's/calendar_store.py's own is_configured()
        # for exactly what each of these booleans mirrors.
        print(
            "[CALENDAR_CONFIG] "
            f"oauth_libs_imported={calendar_auth._OAUTH_LIBS_OK} "
            f"client_id_configured={bool(os.environ.get('GOOGLE_CLIENT_ID'))} "
            f"client_secret_configured={bool(os.environ.get('GOOGLE_CLIENT_SECRET'))} "
            f"redirect_uri_configured={bool(os.environ.get('GOOGLE_REDIRECT_URI'))} "
            f"calendar_store_configured={calendar_store.is_configured()}"
        )

        # Phase 6: allow a local Vite dev server (different port) to call the
        # plain-HTTP routes below. Explicit allowlist only — see
        # _cors_allowed_origins(). Does not affect /ws, /ws/phone-audio, or
        # /ws/audio-out (CORS is not enforced on WebSocket handshakes).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_allowed_origins(),
            allow_methods=["*"],
            allow_headers=["*"],
        )

        def _auth(req: Request) -> bool:
            # Rate-limited passive sweep — see _purge_stale_tokens()'s own
            # docstring. Every authenticated request already calls this,
            # so it's a convenient, cheap place to hook the safety net
            # without a dedicated background task.
            now = time.time()
            if now - self._last_token_sweep > _TOKEN_SWEEP_INTERVAL_SECONDS:
                self._last_token_sweep = now
                self._purge_stale_tokens()
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                    media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            body    = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now     = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_created_at[tok] = time.time()
                self._token_keys[tok] = entered
                self._session_auth_mode[tok] = "remote"   # Phase 8 bookkeeping
                self._aes_key(entered)                   # pre-derive & cache
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Remote connection established."}
                ))
                # Bearer token in response body — no cookies needed (works on any browser/HTTP)
                return JSONResponse({"ok": True, "token": tok})
            return JSONResponse({"ok": False, "error": "Invalid or expired key"},
                                status_code=401)

        @app.post("/login/username")
        async def login_username(req: Request):
            """Username+PIN login, authenticated against the local SQLite
            profile store (users/user_db.py) — a fixed, hand-seeded set of
            known profiles, not open registration. Distinct from Remote
            Access: a username login never implies control of any
            particular physical desktop (see has_desktop_connected()/
            _client_roles, untouched by this route).

            Reuses the exact same token mechanism /login (PIN) already
            uses — the returned token works unchanged for /ws, /api/command,
            /api/wake, /ws/audio-out, and /ws/phone-audio.

            The username character allowlist below exists specifically
            because the resolved display name is woven directly into
            JarvisLive's Gemini system instruction (not just spoken back as
            conversation) — restricting it to name-shaped text is a cheap,
            worthwhile guard against embedding prompt-injection payloads via
            the "username" field.

            PIN failures and unknown usernames return the exact same 401
            error — see users/user_db.py's authenticate() docstring for why
            (never let a client distinguish "no such user" from "wrong
            PIN"). The response body only ever contains the token and the
            resolved display name — pin_hash and the rest of the profile
            never reach the client (see set_profile_callback() for how the
            rest of the profile reaches JarvisLive instead, server-side).

            Optional "timezone" field: the browser's own IANA timezone name
            (Intl.DateTimeFormat().resolvedOptions().timeZone), used so
            JarvisLive reports the user's actual device-local time instead
            of this server's (see set_timezone_callback()/main.py's
            _local_now()). Best-effort — missing or malformed values are
            silently ignored rather than failing the login; main.py itself
            re-validates against the real IANA database before ever using it.
            """
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "Invalid request body"}, status_code=400)

            username = str(body.get("username", "")).strip()
            if not username:
                return JSONResponse({"ok": False, "error": "Username is required"}, status_code=400)
            if len(username) > 40:
                return JSONResponse({"ok": False, "error": "Username is too long (max 40 characters)"}, status_code=400)
            if not re.fullmatch(r"[A-Za-z0-9 '_-]{1,40}", username):
                return JSONResponse(
                    {"ok": False, "error": "Username may only contain letters, numbers, spaces, apostrophes, hyphens, and underscores"},
                    status_code=400,
                )

            pin = str(body.get("pin", "")).strip()
            if not pin:
                return JSONResponse({"ok": False, "error": "PIN is required"}, status_code=400)

            profile = user_db.authenticate(username, pin)
            if profile is None:
                # Deliberately generic — never reveals whether the username
                # itself was even recognized (see user_db.authenticate()).
                return JSONResponse({"ok": False, "error": "Invalid username or PIN"}, status_code=401)

            # What JarvisLive actually calls the user / uses for TTS
            # addressing — pronunciation (a phonetic spelling, when the
            # profile has one) beats the plain nickname, which beats the
            # canonical username. Never the raw login alias typed in
            # (Bandana/Radhe both still address as "Sana"/"Saanaa").
            display_name = profile["pronunciation"] or profile["nickname"] or profile["username"]

            timezone = str(body.get("timezone", "")).strip()
            # IANA names are things like "Asia/Kathmandu" or "UTC" — loose
            # shape check only; main.py does the real validity check via
            # zoneinfo before ever trusting it.
            if timezone and not re.fullmatch(r"[A-Za-z0-9_+\-/]{1,50}", timezone):
                timezone = ""

            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_created_at[tok] = time.time()
            self._session_auth_mode[tok] = "username"
            self._session_usernames[tok] = display_name
            # Location foundation: the CANONICAL username (profile["username"],
            # e.g. "sana" — never the display name above), used only to
            # detect a stale location update from a superseded login — see
            # set_location_callback()'s docstring.
            self._session_canonical_owner[tok] = profile["username"]
            if timezone:
                self._session_timezones[tok] = timezone

            # Privacy fix: every username login starts the Activity Log
            # fresh — see _reset_activity_history()'s own docstring for
            # why this belongs here (a global, un-scoped _history was
            # being replayed to whichever browser opened /ws next,
            # regardless of who it actually belonged to).
            self._reset_activity_history()

            # Profile before username: _set_user_profile() (fired by
            # set_profile_callback) is what decides whether this login
            # needs a full reconnect (a different account than whatever
            # was active). _set_web_username() (fired by
            # set_username_callback) needs to know that decision BEFORE
            # deciding how to fire this login's own greeting — see its
            # docstring for why the ordering here matters.
            if self._profile_callback:
                self._profile_callback(profile)
            if self._username_callback:
                self._username_callback(display_name)
            if timezone and self._timezone_callback:
                self._timezone_callback(timezone)
            # Phase 9: logging in IS the start signal for this flow — the
            # user should never need a separate WAKE press. Reuses the
            # exact same wake mechanism unchanged (main.py's run() gate);
            # harmless/no-op if Jarvis is already running (e.g. desktop).
            if self._wake_callback:
                self._wake_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": f"{display_name} connected."}
            ))
            return JSONResponse({"ok": True, "token": tok, "username": display_name})

        @app.get("/auto-login")
        async def auto_login(key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in SARANA to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            tok     = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_created_at[tok] = time.time()
            self._token_keys[tok] = key
            self._session_auth_mode[tok] = "remote"   # Phase 8 bookkeeping
            self._aes_key(key)
            self._device_sessions[dev_tok] = {"session_key": key}

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Remote connection established via QR code."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('jarvis_token','{tok}');
  sessionStorage.setItem('jarvis_key','{key}');
  localStorage.setItem('jarvis_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Connecting to SARANA…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            if not dev_tok or dev_tok not in self._device_sessions:
                return JSONResponse({"ok": False}, status_code=401)
            session_key = self._device_sessions[dev_tok]["session_key"]
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_created_at[tok] = time.time()
            self._token_keys[tok] = session_key
            self._session_auth_mode[tok] = "remote"   # Phase 8 bookkeeping
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Known device reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            self._device_sessions.clear()
            return JSONResponse({"ok": True, "revoked": count})

        @app.get("/api/session")
        async def session_info():
            # Intentionally unauthenticated — real auth is Phase 8. This
            # endpoint exposes no secrets, only display/capability info a
            # web client needs before it can even show a login screen.
            return JSONResponse({
                "assistant_name":    _get_assistant_name(),
                "tools":             _session_tools(),
                "desktop_connected": self.has_desktop_connected(),
            })

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/interrupt")
        async def interrupt_ep(req: Request):
            """Web equivalent of the desktop INTERRUPT button — stops SARANA
            mid-speech via the exact same JarvisLive.interrupt() the desktop
            UI already calls (see set_interrupt_callback()). No new
            interruption logic lives here."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._interrupt_callback:
                self._interrupt_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/location")
        async def location_ep(req: Request):
            """Browser-location foundation: a one-shot navigator.geolocation
            fix (see frontend/src/lib/geolocation.js), sent only after the
            user has already granted permission — never a periodic stream,
            never continuous tracking. Protected by the exact same
            Bearer-token auth as /api/interrupt; no second authentication
            mechanism.

            Only latitude/longitude/accuracy/timestamp are ever read from
            the body — any other field (e.g. a claimed "username") is
            silently ignored. WHO this update belongs to is determined
            entirely by the authenticated token itself (see
            self._session_canonical_owner), never by anything the browser
            claims — this is what lets main.py's _set_session_location()
            detect and drop a delayed update from a login that is no
            longer the active identity (a genuine account switch, with or
            without an explicit logout in between).

            `timestamp` (optional): the BROWSER's own fix time
            (GeolocationPosition.timestamp, epoch milliseconds — see
            frontend/src/lib/geolocation.js), not when this request
            happened to arrive at the server. Forwarded through so
            _set_session_location() can tell two fixes apart by when they
            were actually taken, protecting against a slower-arriving-but-
            older refresh response clobbering a faster-arriving-but-newer
            one (two overlapping refresh attempts can legitimately
            complete out of order). Missing/malformed values are passed
            through as None — main.py treats that as "no ordering
            information available" and falls back to just accepting the
            update, never a hard failure."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "Invalid request body"}, status_code=400)

            def _finite(v):
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    return None
                return f if math.isfinite(f) else None

            latitude  = _finite(body.get("latitude"))
            longitude = _finite(body.get("longitude"))
            accuracy  = _finite(body.get("accuracy"))
            fix_timestamp = _finite(body.get("timestamp"))   # optional — None if absent/malformed

            if latitude is None or not (-90.0 <= latitude <= 90.0):
                return JSONResponse({"error": "Invalid latitude"}, status_code=400)
            if longitude is None or not (-180.0 <= longitude <= 180.0):
                return JSONResponse({"error": "Invalid longitude"}, status_code=400)
            if accuracy is None or accuracy < 0:
                return JSONResponse({"error": "Invalid accuracy"}, status_code=400)

            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            owner = self._session_canonical_owner.get(tok, "")
            if self._location_callback:
                self._location_callback(latitude, longitude, accuracy, owner, fix_timestamp)
            return JSONResponse({"ok": True})

        @app.post("/api/capabilities")
        async def capabilities_ep(req: Request):
            """Permissions foundation: the browser's own REAL permission
            state for a capability it can observe directly (Permissions
            API / an actual permission-request attempt's own outcome —
            see frontend/src/lib/permissions.js), never a fabricated
            client-only toggle. Same Bearer-token auth and identity-
            binding as /api/location (owner resolved from the token
            alone — see self._session_canonical_owner — never from
            anything the body claims).

            Body: {"microphone"?: str, "location"?: str}, each one of
            "granted" | "denied" | "prompt" | "unsupported" — the
            Permissions API's own vocabulary. Either key may be omitted
            (only report what actually changed); at least one must be
            present. Least privilege: this only ever carries the
            capability STATE the browser already determined on its own —
            never a coordinate, never a media stream, never any other
            device information."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "Invalid request body"}, status_code=400)

            valid_states = {"granted", "denied", "prompt", "unsupported"}
            microphone = body.get("microphone")
            location = body.get("location")
            if microphone is not None and microphone not in valid_states:
                return JSONResponse({"error": "Invalid microphone state"}, status_code=400)
            if location is not None and location not in valid_states:
                return JSONResponse({"error": "Invalid location state"}, status_code=400)
            if microphone is None and location is None:
                return JSONResponse({"error": "No capability state provided"}, status_code=400)

            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            owner = self._session_canonical_owner.get(tok, "")
            if self._capabilities_callback:
                self._capabilities_callback(
                    microphone=microphone, location=location, requester_owner=owner
                )
            return JSONResponse({"ok": True})

        # ── Google Calendar OAuth ────────────────────────────────────────

        @app.get("/auth/google")
        async def google_auth_start(token: str = "", return_to: str = ""):
            """Step 1 of the Calendar connect flow. A full-page browser
            navigation (window.location.href = ...), not a fetch — so the
            SARANA auth token travels as a query param rather than an
            Authorization header, the same reason /auto-login's key does.
            Resolves the CURRENT identity from that token via
            self._session_canonical_owner — the exact mechanism
            /api/location already uses to bind a request to an identity —
            never anything else. A PIN/Remote Access token (no canonical
            username) is rejected: Google Calendar connects to a specific
            SARANA ACCOUNT, not an anonymous remote session.

            `return_to` (optional): the frontend's own origin, so the
            final callback redirect lands back on whichever origin the
            user actually started from (local dev vs. production) —
            validated against the EXISTING CORS allowlist
            (_cors_allowed_origins()) rather than trusted outright, so
            this can never become an open redirect.
            """
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return HTMLResponse(
                    "<h2>Not signed in</h2><p>Please sign in to SARANA first, "
                    "then try connecting Google Calendar again.</p>",
                    status_code=401,
                )
            owner = self._session_canonical_owner.get(tok, "")
            if not owner:
                return HTMLResponse(
                    "<h2>Google Calendar needs a SARANA account login</h2>"
                    "<p>Remote Access sessions can't connect Google Calendar.</p>",
                    status_code=400,
                )
            if not calendar_auth.is_configured() or not calendar_store.is_configured():
                return HTMLResponse(
                    "<h2>Google Calendar isn't available on this server right now.</h2>",
                    status_code=503,
                )

            safe_return_to = _normalize_origin(return_to) if return_to else ""
            if safe_return_to not in _cors_allowed_origins():
                safe_return_to = ""   # unrecognized origin — ignored, falls back at the callback

            self._purge_stale_oauth_states()
            state = secrets.token_urlsafe(32)
            auth_url, code_verifier = calendar_auth.build_auth_url(state)
            self._google_oauth_states[state] = {
                "owner": owner,
                "return_to": safe_return_to,
                "expires": time.time() + 600,   # 10 minutes — a real consent flow, not a long-lived token
                # PKCE: this exact verifier must accompany the token
                # exchange at the callback (see calendar_auth.build_auth_url()'s
                # own docstring) — stored alongside state rather than a new
                # persistence system since it shares state's exact lifetime
                # (single-use, popped together, same 10-minute expiry).
                "code_verifier": code_verifier,
            }

            return RedirectResponse(auth_url)

        @app.get("/auth/google/callback")
        async def google_auth_callback(code: str = "", state: str = "", error: str = ""):
            """Step 2 — Google redirects the browser here after consent.
            `state` is looked up and immediately POPPED (single-use) from
            self._google_oauth_states: a missing, unrecognized, expired,
            or ALREADY-CONSUMED state is rejected outright. This is the
            concrete mechanism that stops a stale/replayed callback from
            ever attaching credentials to whichever identity happens to
            be active by the time it arrives — not a convention, an
            actual enforced check.
            """
            # Diagnostic checkpoint logging ONLY -- booleans/owner username/
            # exception type, never state/code values or any credential.
            # Added to answer "which branch actually fired" from Render's
            # log tab alone, the same way [CALENDAR_CONFIG] answers "are
            # the deps/env vars there" -- see that diagnostic's own comment
            # a few routes up. flush=True on every line here specifically
            # because sys.stdout is NOT reconfigured for line buffering
            # anywhere in this process (only encoding is, see main.py's
            # own stdout/stderr reconfigure()), so a non-flushed print can
            # sit in Render's block-buffered stdout indefinitely.
            print(
                f"[Calendar] callback reached: state_present={bool(state)} "
                f"code_present={bool(code)} error_present={bool(error)}",
                flush=True,
            )

            entry = self._google_oauth_states.pop(state, None) if state else None
            frontend = (entry or {}).get("return_to") or _default_frontend_origin()

            if error:
                # User declined consent, or Google reported some other
                # problem — never treated as success.
                print("[Calendar] Google returned error param -> cancelled", flush=True)
                return RedirectResponse(f"{frontend}/?calendar=cancelled")

            if not entry or entry["expires"] < time.time():
                print("[Calendar] state missing/expired/already used -> 400", flush=True)
                return HTMLResponse(
                    "<h2>This Google Calendar connection link has expired or "
                    "was already used.</h2><p>Please try connecting again from "
                    "SARANA.</p>",
                    status_code=400,
                )

            if not code:
                print("[Calendar] no code in callback -> error", flush=True)
                return RedirectResponse(f"{frontend}/?calendar=error")

            owner = entry["owner"]
            # PKCE: the exact verifier build_auth_url() generated for THIS
            # state, at connect time — see that function's own docstring.
            # entry was already popped (single-use) above, so this is
            # inherently one-time-use and gone whether the exchange below
            # succeeds or fails; no separate cleanup needed. A missing
            # verifier (e.g. an in-memory state entry from before this
            # fix, surviving a redeploy mid-flow) falls through to "" —
            # Google rejects that exactly like any other invalid
            # verifier, landing on the existing except/?calendar=error
            # path below rather than crashing.
            code_verifier = entry.get("code_verifier", "")
            print(f"[Calendar] exchange starting for '{owner}'", flush=True)
            loop = asyncio.get_event_loop()

            def _do_exchange_and_store():
                credentials = calendar_auth.exchange_code(code, code_verifier)
                print(f"[Calendar] exchange succeeded for '{owner}'", flush=True)
                email = calendar_auth.fetch_email(credentials)
                calendar_store.init_schema()
                print(f"[Calendar] storage starting for '{owner}'", flush=True)
                calendar_store.save_credentials(owner, credentials.to_json(), email)
                print(f"[Calendar] storage succeeded for '{owner}'", flush=True)

            try:
                await loop.run_in_executor(None, _do_exchange_and_store)
            except Exception as e:
                # Never logs the code or any token — see calendar_auth.py's
                # own docstring for that guarantee. type(e).__name__
                # distinguishes a Google/oauthlib rejection from a
                # Postgres/KeyError-class storage failure without needing
                # more detail than that.
                print(f"[Calendar] OAuth exchange/store failed for '{owner}': {type(e).__name__}: {e}", flush=True)
                return RedirectResponse(f"{frontend}/?calendar=error")

            print(f"[Calendar] callback complete for '{owner}' -> connected", flush=True)
            return RedirectResponse(f"{frontend}/?calendar=connected")

        @app.get("/api/calendar/status")
        async def calendar_status_ep(req: Request):
            """Safe status only — {"connected": bool, "email": str}, never
            a token. See actions/calendar_store.py's get_status()."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            owner = self._session_canonical_owner.get(tok, "")
            if not owner or not calendar_store.is_configured():
                return JSONResponse({"connected": False, "email": ""})
            try:
                status = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: calendar_store.get_status(owner)
                )
            except Exception as e:
                print(f"[Calendar] Status lookup failed: {e}")
                status = {"connected": False, "email": ""}
            return JSONResponse(status)

        @app.post("/api/calendar/disconnect")
        async def calendar_disconnect_ep(req: Request):
            """Removes SARANA's stored credentials for the CURRENT
            identity only (never trusts a request body/param for which
            account to disconnect — same "authenticated identity, never
            an arbitrary parameter" rule as everywhere else in this
            file). Best-effort revokes Google's own authorization too;
            that failing must never block the local disconnect from
            succeeding (mirrors the frontend's own logout handler, which
            still clears local state even if the backend call fails)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            owner = self._session_canonical_owner.get(tok, "")
            if owner and calendar_store.is_configured():
                loop = asyncio.get_event_loop()

                def _do_disconnect():
                    row = calendar_store.load_credentials(owner)
                    if row:
                        creds_json, _email = row
                        try:
                            credentials = calendar_auth.credentials_from_json(creds_json)
                            calendar_auth.revoke(credentials)
                        except Exception as e:
                            print(f"[Calendar] Revoke failed (local disconnect still proceeds): {e}")
                    calendar_store.delete_credentials(owner)

                try:
                    await loop.run_in_executor(None, _do_disconnect)
                except Exception as e:
                    print(f"[Calendar] Disconnect storage error: {e}")
            return JSONResponse({"ok": True})

        @app.post("/api/logout")
        async def logout_ep(req: Request):
            """Backend counterpart to the frontend's Logout action (see
            App.jsx's handleLogout()) — removes this token and every
            session bookkeeping entry tied to it (see _forget_token()).

            Deliberately does NOT require _auth(req) / a still-valid
            token: logging out an already-invalid or expired token must
            be a harmless no-op, not a 401 — a client retrying a logout
            it's unsure went through, or logging out after the passive
            TTL sweep already removed it, are both completely normal.
            Always returns {"ok": True} either way, and never reveals
            whether the token was valid — same "don't leak account state"
            principle as /login/username's generic error.

            Does not forcibly close any already-open /ws, /ws/audio-out,
            or /ws/phone-audio connection using this token — those are
            torn down client-side (see App.jsx's socket-teardown effect,
            which already runs on every auth-state change); this only
            prevents the token being used to open new ones.

            Privacy fix: a username-login token logging out also clears
            the Activity Log now (see _reset_activity_history()) — defense
            in depth alongside the same clear on the NEXT login, so a
            logged-out user's activity doesn't linger in the shared
            buffer even briefly. Scoped to "username" tokens only — a
            Remote Access (PIN) token logging out does NOT clear it,
            since that path is reattaching to an ongoing desktop session,
            not ending a distinct identity's session (mirrors the same
            distinction /login/username's own history-reset already
            makes by only firing there, not from /login).
            """
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if tok:
                if self._session_auth_mode.get(tok) == "username":
                    self._reset_activity_history()
                    # PostgreSQL memory migration: same username-vs-PIN
                    # scoping as the Activity Log reset above — discards
                    # the in-RAM memory cache too (see main.py's
                    # _clear_memory_session()/set_logout_callback()).
                    if self._logout_callback:
                        self._logout_callback()
                self._forget_token(tok)
            return JSONResponse({"ok": True})

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone live."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                        # Instrumentation (item 3 audit — transport
                        # latency): queue DEPTH, not per-chunk latency —
                        # deliberately not timestamping/mutating the audio
                        # payload itself (media dict handed to Gemini's
                        # SDK downstream) to avoid any risk of the SDK
                        # rejecting an unexpected key on a live audio
                        # path. Depth is still a direct, safe proxy for
                        # "is this hop backing up" (item 3's own required
                        # metric), and pairs with the drop counter above
                        # for a complete picture without touching the hot
                        # path's data shape at all.
                        self._phone_audio_queue_depth.record(self._phone_audio_queue.qsize())
                        self._phone_audio_frames += 1
                        if self._phone_audio_frames % 200 == 1:
                            print(
                                f"[Dashboard] phone-audio queue depth: "
                                f"{self._phone_audio_queue_depth.summary()}"
                            )
                    except asyncio.QueueFull:
                        # Instrumentation (item 4 audit): this used to be a
                        # completely silent drop — no way to tell whether
                        # browser mic audio was ever actually being lost
                        # here vs. just feeling slow. Still drops (correct,
                        # existing backpressure behavior — never block a
                        # live mic stream waiting for room), just now
                        # observable. self._phone_audio_dropped is the
                        # cumulative counter; the print is rate-limited so
                        # a genuine flood doesn't itself become a new
                        # source of overhead/log spam.
                        self._phone_audio_dropped += 1
                        if self._phone_audio_dropped % 50 == 1:
                            print(
                                f"[Dashboard] phone-audio queue full — "
                                f"{self._phone_audio_dropped} frame(s) dropped so far "
                                f"(qsize={self._phone_audio_queue.qsize()})"
                            )
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

        # ── Remote audio output: Gemini's spoken response → connected clients ──
        # (Phase 4). Output-only channel — the client isn't expected to send
        # anything meaningful; the receive loop exists solely to detect
        # disconnect, exactly like /ws/phone-audio's own pattern above.

        @app.websocket("/ws/audio-out")
        async def audio_out_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._register_audio_client(websocket)
            try:
                while True:
                    await websocket.receive_bytes()   # ignored; detects disconnect only
            except WebSocketDisconnect:
                pass
            finally:
                await self._unregister_audio_client(websocket)

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name                          # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = "", role: str = "client"):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            self._client_roles[websocket] = (role or "client").strip().lower()
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data     = await websocket.receive_json()
                    msg_type = data.get("type")

                    if msg_type == "command":
                        enc = data.get("enc", "")
                        t   = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()

                    elif msg_type == "image_command":
                        # Web visual intelligence — browser-submitted image
                        # ingress. Validation happens HERE, at the untrusted-
                        # input boundary (auth already enforced by /ws's own
                        # token check above); compression + the actual
                        # Gemini injection happen in main.py's
                        # _process_dashboard_image_commands(), reusing
                        # actions/screen_processor.py's _compress() exactly
                        # like the desktop screen_process tool already does.
                        # Never queued to disk, never written to a file —
                        # bytes live only in memory for the one queue hop.
                        mime = (data.get("mime_type") or "").strip().lower()
                        b64  = data.get("data") or ""
                        text = (data.get("text") or "What's in this image?").strip()

                        if mime not in ALLOWED_IMAGE_MIME_TYPES:
                            await websocket.send_json({
                                "type": "image_error",
                                "error": "That image type isn't supported. Try a JPEG, PNG, or WebP.",
                            })
                        elif not b64 or len(b64) > MAX_IMAGE_B64_CHARS:
                            await websocket.send_json({
                                "type": "image_error",
                                "error": "That image is too large to send.",
                            })
                        else:
                            try:
                                raw = base64.b64decode(b64, validate=True)
                            except Exception:
                                raw = None
                            if raw is None or len(raw) > MAX_IMAGE_BYTES:
                                await websocket.send_json({
                                    "type": "image_error",
                                    "error": "That image couldn't be read.",
                                })
                            elif _PIL_OK and not _looks_like_a_real_image(raw):
                                await websocket.send_json({
                                    "type": "image_error",
                                    "error": "That doesn't look like a valid image.",
                                })
                            else:
                                await self._image_command_queue.put({
                                    "data": raw, "mime_type": mime, "text": text,
                                })
                                if self._wake_callback:
                                    self._wake_callback()

                    elif msg_type == "vision_frame":
                        # Web LIVE camera vision — one sampled browser
                        # camera frame for an already-open request (see
                        # main.py's web_camera_vision tool, which is what
                        # actually opens a request_id). Validation is
                        # identical to "image_command" above (same untrusted-
                        # input boundary); request_id relevance (does this
                        # frame belong to the CURRENTLY active vision
                        # session?) is decided in main.py's
                        # _process_web_vision_frames(), not here — this
                        # handler only knows about bytes, not conversation
                        # state. Never queued to disk.
                        request_id = str(data.get("request_id") or "").strip()
                        mime       = (data.get("mime_type") or "").strip().lower()
                        b64        = data.get("data") or ""

                        if not request_id:
                            await websocket.send_json({
                                "type": "vision_error",
                                "error": "Missing request_id.",
                            })
                        elif mime not in ALLOWED_IMAGE_MIME_TYPES:
                            await websocket.send_json({
                                "type": "vision_error", "request_id": request_id,
                                "error": "That image type isn't supported. Try a JPEG, PNG, or WebP.",
                            })
                        elif not b64 or len(b64) > MAX_IMAGE_B64_CHARS:
                            await websocket.send_json({
                                "type": "vision_error", "request_id": request_id,
                                "error": "That frame is too large to send.",
                            })
                        else:
                            try:
                                raw = base64.b64decode(b64, validate=True)
                            except Exception:
                                raw = None
                            if raw is None or len(raw) > MAX_IMAGE_BYTES:
                                await websocket.send_json({
                                    "type": "vision_error", "request_id": request_id,
                                    "error": "That frame couldn't be read.",
                                })
                            elif _PIL_OK and not _looks_like_a_real_image(raw):
                                await websocket.send_json({
                                    "type": "vision_error", "request_id": request_id,
                                    "error": "That doesn't look like a valid frame.",
                                })
                            else:
                                await self._vision_frame_queue.put({
                                    "request_id": request_id,
                                    "seq": data.get("seq"),
                                    "mime_type": mime,
                                    "data": raw,
                                })
                                # No _wake_callback() here — a vision_frame only
                                # ever arrives for an ALREADY-open request_id
                                # (opened by the web_camera_vision tool call
                                # itself, which already has a live session).

                    elif msg_type == "vision_control":
                        # Web live camera vision — a client-side lifecycle
                        # signal for an active request, e.g. the user
                        # pressed Stop, or getUserMedia failed after the
                        # camera was already streaming. Queued alongside
                        # real frames (main.py's consumer tells them apart
                        # by the presence of "control").
                        request_id = str(data.get("request_id") or "").strip()
                        action     = str(data.get("action") or "").strip().lower()
                        if request_id and action == "stop":
                            await self._vision_frame_queue.put({
                                "request_id": request_id,
                                "control": "stop",
                                "reason": str(data.get("reason") or "")[:200],
                            })

                    elif msg_type == "device_action_result":
                        # Protocol shape reserved for Phase 6 (Desktop Device
                        # Agent). No pending-request matching exists yet —
                        # this only proves a recognized-but-not-yet-actioned
                        # type doesn't disrupt the connection or the loop.
                        print(f"[Dashboard] device_action_result received "
                              f"(Phase 6 will act on this): {data.get('action')}")

                    elif msg_type == "ping":
                        # Instrumentation (item 3 audit — transport
                        # latency): a lightweight, existing-channel RTT
                        # probe. This does NOT touch the audio protocol at
                        # all (/ws/phone-audio, /ws/audio-out are
                        # untouched) — it's the safe way to get a REAL
                        # measured browser<->Render round-trip number
                        # (which the audit could only reason about, not
                        # measure) without embedding timestamps into the
                        # binary audio payload the SDK consumes. Echoes
                        # the client's own timestamp back unchanged; the
                        # client computes its own RTT — this server holds
                        # no ping history/state.
                        await websocket.send_json({"type": "pong", "t": data.get("t")})

                    # Any other type: ignored — exactly the existing
                    # behavior, now made explicit rather than incidental.
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)
                self._client_roles.pop(websocket, None)

        return app

    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Second HTTPS server on PORT+1 sharing the same app and in-memory state.
        Chrome HTTPS-upgrades any bare IP:PORT the user types, so this port also needs TLS.
        User types IP:8001 → Chrome tries https → self-signed cert warning → accept once → done."""
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT + 1)
        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT + 1, log_level="warning",
            ssl_keyfile=str(ssl_key), ssl_certfile=str(ssl_cert),
        )
        print(f"[Dashboard] Manual entry:  {self._ip}:{PORT + 1}  (type in browser, accept cert once)")
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        # Firewall setup runs in a thread — uvicorn starts immediately,
        # no waiting for UAC dialogs or subprocess timeouts.
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT)

        use_ssl  = self._ssl_enabled()
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"

        if use_ssl:
            asyncio.create_task(self._serve_alias())

        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT, log_level="warning",
            **({"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)} if use_ssl else {}),
        )

        proto = "https" if use_ssl else "http"
        print(f"[Dashboard] {proto}://{self._ip}:{PORT}")
        print("[Dashboard] Press 'Remote Control' in JARVIS UI to get the QR code.")

        # Phase 6: also print one ready-to-use pairing PIN directly, for
        # surfaces with no button to click (server_main.py / headless).
        # Same one-time-key mechanism as Remote Control — new_key() below is
        # the exact method _make_remote_key() (main.py) already calls.
        _dev_key = self.new_key()
        print(f"[Dashboard] Web frontend pairing PIN: {_dev_key}  (valid 10 min)")
        print(f"[Dashboard] Auto-login URL: {self.get_url()}/auto-login?key={_dev_key}")

        await uvicorn.Server(cfg).serve()
