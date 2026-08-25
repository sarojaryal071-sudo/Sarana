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
"""

import asyncio
import base64
import hashlib
import os
import re
import secrets
import socket
import string
import time
from pathlib import Path

from users import user_db

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
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
        self._history: list[dict]         = []
        self._command_queue               = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._username_callback           = None   # Phase 8: fires on a successful /login/username
        self._interrupt_callback          = None   # web interrupt control: fires main.py's interrupt()
        self._timezone_callback           = None   # fires on a successful /login/username with a timezone
        self._profile_callback            = None   # fires with the full users/user_db.py profile dict
        self._session_timezones: dict[str, str] = {}   # token → IANA timezone (username logins only)
        # Phase 8: lightweight session bookkeeping — which auth path issued a
        # token, and (for username logins only) which name. Not a user
        # database, no registration, nothing persisted past process
        # lifetime — see /login/username's docstring for why.
        self._session_auth_mode: dict[str, str] = {}   # token → "username" | "remote"
        self._session_usernames: dict[str, str] = {}   # token → username (username logins only)
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}  # device_token → {session_key}
        self._phone_audio_queue: asyncio.Queue    = asyncio.Queue(maxsize=200)
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

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def broadcast_content(self, title: str, text: str) -> None:
        """Server→client "content" message — mirrors JarvisUI.show_content's
        shape for a future web client. Nothing calls this yet (main.py is
        untouched this phase); it exists so a later phase has a ready-made
        method instead of hand-building the dict at each call site.
        """
        await self.broadcast({"type": "content", "title": title, "text": text})

    async def broadcast_audio(self, chunk: bytes) -> None:
        """Fan out one raw PCM16 audio chunk — the exact same bytes main.py's
        _play_audio() just wrote to the local speaker — to every currently
        connected /ws/audio-out client (Phase 4).

        Mirrors broadcast()'s existing per-client isolation: one client's
        send error or disconnect never affects delivery to the others, and
        this method itself never raises, so callers (main.py's audio loop)
        can fire it without their own try/except and without it ever
        delaying or interrupting local playback.
        """
        dead: set[WebSocket] = set()
        for ws in list(self._audio_out_clients):
            try:
                await ws.send_bytes(chunk)
            except Exception:
                dead.add(ws)
        self._audio_out_clients -= dead

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

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
            self._session_auth_mode[tok] = "username"
            self._session_usernames[tok] = display_name
            if timezone:
                self._session_timezones[tok] = timezone

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
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
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
            self._audio_out_clients.add(websocket)
            try:
                while True:
                    await websocket.receive_bytes()   # ignored; detects disconnect only
            except WebSocketDisconnect:
                pass
            finally:
                self._audio_out_clients.discard(websocket)

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

                    elif msg_type == "device_action_result":
                        # Protocol shape reserved for Phase 6 (Desktop Device
                        # Agent). No pending-request matching exists yet —
                        # this only proves a recognized-but-not-yet-actioned
                        # type doesn't disrupt the connection or the loop.
                        print(f"[Dashboard] device_action_result received "
                              f"(Phase 6 will act on this): {data.get('action')}")

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
