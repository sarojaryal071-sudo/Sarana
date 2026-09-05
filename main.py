import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **                       kw)

    _subprocess.Popen = _Popen

# ─────────────────────────────────────────────────────────────────────────────

import array
import asyncio
import math
import os
import re
import threading
import time
import json
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Console encoding robustness (root cause of the "missing greeting" bug) ──
# Windows consoles often default to a legacy codepage (cp1252) that cannot
# represent most Unicode text. Earlier fixes in this file removed emoji from
# specific hardcoded print() strings, but that approach doesn't work for
# DYNAMIC content — and now that Nepali is the default response/transcript
# language (see _build_config()'s LANGUAGE clause), print()ing a live user
# or Gemini transcript routinely contains Devanagari script. When that print
# (e.g. core/headless_surface.py's write_log(), called from _receive_audio())
# raises UnicodeEncodeError, it cancels the entire run() TaskGroup — every
# sibling task, including whatever greeting/response was still being
# streamed — forcing a reconnect mid-greeting. Live-reproduced: a Nepali
# transcript crashed exactly this way and visibly cut off the startup
# greeting. Reconfiguring stdout/stderr once, here, at process start (before
# ui.py or server_main.py import anything) makes every print() in the whole
# process — desktop or web — safe for arbitrary Unicode instead of requiring
# each call site to stay hand-verified ASCII-only. errors="replace" degrades
# an unprintable character to "?" rather than crashing; it never raises.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Render/headless fix: sounddevice binds to the native PortAudio library,
# which Render's containers don't have installed — `import sounddevice`
# itself raises OSError there (not just stream construction), before any
# of _listen_audio()/_play_audio()'s own runtime guards ever get a chance
# to run. Guarding the import makes `from main import JarvisLive` (what
# server_main.py does) succeed regardless; sd is checked at every use site
# below, same pattern actions/screen_processor.py and core/tts.py now use.
try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None
from google import genai
from google.genai import types
from core.assistant_surface import AssistantSurface
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
    set_active_owner, clear_active_session, start_persistence_worker,
    owner_language, upcoming_events_for_prompt,
)
from memory.migrate_long_term import migrate_if_needed
from users import user_db
from core.latency_stats import LatencyStats

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather           import get_weather_text
from actions.geo               import (
    geocode_place, reverse_geocode, format_place,
    find_nearby_places, format_nearby_places, haversine_m, format_distance,
)
from actions.routing           import get_route
from actions import calendar as calendar_actions
from actions import calendar_store, calendar_auth
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen, _compress
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control, get_active_window_title
from actions.office_control    import office_control
from actions import task_engine
from actions import gesture_control
from actions.computer_control  import INCONCLUSIVE_TAGS as _cc_INCONCLUSIVE_TAGS
from actions import result_envelope as _envelope

# Escalation tags checked by the ui_click/ui_type/accomplish branch below:
# the pre-existing per-action tags (CLICK_AMBIGUOUS etc.) PLUS accomplish's
# unified [INCONCLUSIVE]/[UI_AMBIGUOUS] envelope tags — same escalation
# policy either way, just now covering both tag vocabularies.
_cc_ESCALATABLE_TAGS = _cc_INCONCLUSIVE_TAGS | frozenset(
    f"[{s}]" for s in _envelope.ESCALATABLE_STATUSES
)
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from memory.config_manager     import get_brief_enabled
from core.plugin_loader        import discover_plugins

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# Resource-cleanup fix: self._session_log (conversation turns for
# _save_session_summary()/proactive context) used to grow without bound
# for the life of a single, never-reconnected session. _save_session_summary()
# only ever reads the last 40 entries (log[-40:]) and proactive mode only
# ever reads the last 8 (self._session_log[-8:]) — 50 keeps both of those
# exactly as they already behave, with a little headroom, while making the
# list's maximum size explicit and bounded instead of unbounded.
SESSION_LOG_MAX_ENTRIES = 50

def _get_api_key() -> str:
    """Deployment-readiness: GEMINI_API_KEY environment variable takes
    priority (this is how Render provides it — never committed to the
    repo, never present in config/api_keys.json there), falling back to
    the existing config/api_keys.json lookup unchanged for desktop/local
    development, where no env var is normally set."""
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        return env_key
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are SARANA, an AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

class _IdentityChanged(Exception):
    """Internal marker raised by _watch_for_reconnect_request() to unwind
    the current Gemini connection's TaskGroup when a login switches the
    active account mid-session — see that method and
    _set_user_profile()/run() for the full flow. Never raised for a real
    error; run()'s except block special-cases this to reconnect
    immediately, without the network-error backoff/messaging."""


_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()


def _time_of_day_category(hour: int) -> str:
    """Phase 9: pure, testable classification of an hour-of-day (0-23) into
    a coarse category — used by _send_startup_briefing() to give Gemini
    computed CONTEXT (not a hardcoded message) so the actual greeting
    wording is always generated, never a fixed string. Boundaries are a
    judgment call, not a spec — documented here rather than scattered
    inline so they're easy to find and adjust.
    """
    if 0 <= hour < 5:
        return "late_night"     # comfort/friend tone, not a cheerful "good morning"
    if 5 <= hour < 8:
        return "early_morning"
    if 8 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"              # 21:00-23:59


# users/user_db.py's voice_preference -> a Gemini Live prebuilt voice name.
# Explicit preference, NOT derived from gender (see users/user_db.py's seed
# data: Saanaa's profile is female with voice_preference "Male", Saroj's is
# male with voice_preference "Female" — the map only ever looks at this
# value). "Charon" stays the default for no-preference/desktop sessions,
# identical to the hardcoded value this replaces.
_VOICE_PREFERENCE_MAP = {
    "male": "Charon",
    "female": "Kore",
}
_DEFAULT_VOICE_NAME = "Charon"


def _voice_name_for_preference(preference: str | None) -> str:
    if not preference:
        return _DEFAULT_VOICE_NAME
    return _VOICE_PREFERENCE_MAP.get(preference.strip().lower(), _DEFAULT_VOICE_NAME)


# SARANA Face UI — mirrors frontend/src/lib/faceExpressions.js's
# FACE_EXPRESSIONS and ui.py's own expression vocabulary exactly (the same
# 15-word set every rendering surface understands). Kept as one module-
# level constant so the set_expression tool's dispatch validation and its
# own declared JSON-schema enum (below) can't silently drift apart.
_VALID_FACE_EXPRESSIONS = frozenset({
    "neutral", "listening", "thinking", "speaking", "happy",
    "concerned", "sad", "curious", "confused", "reassuring",
    "empathetic", "surprised", "calm", "focused", "excited",
})

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it. "
            "Returns a Result Envelope tag: [VERIFIED_SUCCESS] means the app is confirmed as "
            "the active window — say it opened. [INCONCLUSIVE] means a launch command was sent "
            "but this couldn't confirm it actually appeared (it may still be loading) — tell the "
            "user honestly, don't claim it's open. [VERIFIED_FAILURE] means it did not launch."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "get_weather",
        "description": (
            "Gets REAL current weather conditions and a short forecast (temperature, "
            "feels-like, conditions, wind, precipitation/rain chance) for the user's "
            "current location, or a named place. Use for any weather question -- "
            "'what's it like outside', 'will it rain today', 'how hot is it', 'what's "
            "the forecast'. If the user doesn't name a place, this automatically uses "
            "their current device location -- do not ask them where they are first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "place": {
                    "type": "STRING",
                    "description": "Optional named city/place. Omit to use the user's current location.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_current_place",
        "description": (
            "Resolves the user's current city/area/country from their device location. "
            "Use for 'where am I', 'what city am I in', 'what area is this', 'what "
            "neighborhood is this'. Cheap and fast (cached) -- call it directly."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "find_nearby_places",
        "description": (
            "Finds REAL nearby places (pharmacy, restaurant, cafe, supermarket, "
            "hospital, ATM, bar, hotel, etc.) near the user's current device location. "
            "Use for 'find a X near me', 'is there a X nearby', 'nearest X', "
            "'X around me'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "What to search for, e.g. 'pharmacy', 'coffee shop', 'restaurant'",
                },
                "radius_m": {
                    "type": "INTEGER",
                    "description": "Search radius in meters (default 1500, max 5000)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_directions",
        "description": (
            "Gets REAL distance and estimated travel time from the user's current "
            "device location to a named destination, walking or driving. Use for "
            "'how far is X', 'directions to X', 'how long to get to X', 'walking "
            "directions to X'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "destination": {"type": "STRING", "description": "Destination name or address"},
                "mode": {"type": "STRING", "description": "walking | driving (default: driving)"},
            },
            "required": ["destination"],
        },
    },
    {
        "name": "refresh_location",
        "description": (
            "Requests a fresh device location fix right now. Use ONLY when the user "
            "explicitly says something like 'I moved', 'update my location', "
            "'refresh my location', or asks 'where am I now' wanting a genuinely fresh "
            "check -- NOT for ordinary location-based questions, which already use the "
            "current location automatically without needing this."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "get_calendar_events",
        "description": (
            "Reads the user's REAL Google Calendar events for a given time range. Use "
            "for 'what's on my calendar', 'what do I have today/tomorrow', 'do I have "
            "anything at X', 'what's my next appointment', 'what does my week look "
            "like'. Requires the user to have connected Google Calendar first -- if "
            "not connected, this returns [CALENDAR_NOT_CONNECTED]."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "start": {
                    "type": "STRING",
                    "description": (
                        "Start of the range, local date/time, ISO format e.g. "
                        "'2026-08-29T00:00:00' -- compute this from [CURRENT DATE & "
                        "TIME] above, never guess today's date."
                    ),
                },
                "end": {
                    "type": "STRING",
                    "description": "End of the range, local ISO datetime, same format as start.",
                },
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "find_free_time",
        "description": (
            "Finds real free time slots on the user's Google Calendar within a given "
            "window, for a given duration. Use for 'find me free time', 'when am I "
            "free tomorrow', 'find an hour free this afternoon'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "start": {"type": "STRING", "description": "Start of the search window, local ISO datetime."},
                "end": {"type": "STRING", "description": "End of the search window, local ISO datetime."},
                "duration_minutes": {
                    "type": "INTEGER",
                    "description": "Desired free-slot length in minutes (default 30).",
                },
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Creates a REAL event on the user's Google Calendar. Use for 'schedule "
            "X', 'add X to my calendar', 'book a meeting'. If the time is missing or "
            "genuinely ambiguous, ask the user instead of inventing one -- do not "
            "call this tool with a guessed time."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Event title"},
                "start": {"type": "STRING", "description": "Start time, local ISO datetime, e.g. '2026-08-30T15:00:00'."},
                "end": {
                    "type": "STRING",
                    "description": "End time, local ISO datetime. Omit if duration_minutes is given instead.",
                },
                "duration_minutes": {
                    "type": "INTEGER",
                    "description": "Duration in minutes, used only if 'end' is omitted (default 60).",
                },
                "description": {"type": "STRING", "description": "Optional event notes/description."},
                "location": {"type": "STRING", "description": "Optional event location."},
                "attendees": {
                    "type": "ARRAY", "items": {"type": "STRING"},
                    "description": "Optional attendee email addresses.",
                },
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "update_calendar_event",
        "description": (
            "Modifies an existing REAL Google Calendar event (e.g. moving a meeting "
            "to a new time). Use event_id if already known from a recent "
            "get_calendar_events call; otherwise supply query (a word from the event "
            "title) and day so the right event can be found. If more than one event "
            "matches, this returns the candidates instead of changing anything -- ask "
            "the user which one they mean, then call this again with a specific "
            "event_id."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "event_id": {"type": "STRING", "description": "Google Calendar event ID, if already known."},
                "query": {"type": "STRING", "description": "Word(s) from the event's title, if event_id is not known."},
                "day": {
                    "type": "STRING",
                    "description": "Local ISO date the event is on, e.g. '2026-08-29' -- used with query.",
                },
                "new_title": {"type": "STRING", "description": "New title, if changing it."},
                "new_start": {"type": "STRING", "description": "New start time, local ISO datetime."},
                "new_end": {"type": "STRING", "description": "New end time, local ISO datetime."},
            },
            "required": [],
        },
    },
    {
        "name": "delete_calendar_event",
        "description": (
            "Deletes/cancels a REAL Google Calendar event. Use event_id if already "
            "known; otherwise supply query and day so the right event can be found. "
            "If more than one event matches, this returns the candidates instead of "
            "deleting anything -- ask the user which one they mean before calling "
            "this again with a specific event_id. Never call this on an ambiguous or "
            "uncertain match."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "event_id": {"type": "STRING", "description": "Google Calendar event ID, if already known."},
                "query": {"type": "STRING", "description": "Word(s) from the event's title, if event_id is not known."},
                "day": {
                    "type": "STRING",
                    "description": "Local ISO date the event is on, e.g. '2026-08-29' -- used with query.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "send_message",
        "description": (
            "Sends a text message via WhatsApp, Telegram, or other messaging platform. This is a "
            "CONSEQUENTIAL action — sending a message cannot be undone — so it requires explicit "
            "user confirmation (set confirmed=true only after the user has actually said yes in "
            "THIS conversation) before it will run; otherwise it returns [CONFIRMATION_REQUIRED]. "
            "Returns a Result Envelope tag: there is no way to confirm real delivery, so a "
            "successful attempt returns [INCONCLUSIVE] (the message was typed and sent via the "
            "app's UI, but delivery isn't independently verifiable) — tell the user it was sent "
            "via the app, never claim confirmed delivery. [VERIFIED_FAILURE] means it did not go "
            "through at all (e.g. the app/page never opened)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."},
                "confirmed":    {"type": "BOOLEAN", "description": "Set true only after the user has explicitly confirmed sending THIS message in THIS conversation — never infer it"}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos. Reuses the SAME browser tab "
            "across repeated play/search calls in one conversation — you never need to "
            "close or reopen anything between them, just call this again with the new query. "
            "If the user asks to play something but doesn't say what yet (e.g. 'open YouTube "
            "and play my favorite song'), ask them what to play — then, the moment they "
            "answer, call THIS SAME TOOL again with action='play' and their answer as the "
            "query. Never hand a YouTube request off to web_search or any other tool once "
            "it's started, even across a clarifying question — a YouTube request stays a "
            "YouTube request until it's actually done."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures YOUR OWN screen or webcam (the device you're running "
            "on) and lets you analyze it. MUST be called when user asks "
            "what is on THEIR screen, to look through YOUR camera, or to "
            "analyze THEIR screen. This is separate from a photo the user "
            "attaches or sends you directly in the conversation — that "
            "arrives on its own, with no tool call needed here; just look "
            "at it and answer naturally. "
            "After this tool captures a screen/webcam image it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "web_camera_vision",
        "description": (
            "WEB/MOBILE ONLY. Temporarily uses the user's own browser/phone "
            "camera to see what they are currently pointing it at, holding "
            "up, or standing in front of — for questions that genuinely need "
            "a live look right now, e.g. 'what's in my hand', 'what am I "
            "holding', 'what am I looking at', 'can you identify this', "
            "'what does this sign say', 'describe what you see'. "
            "MANDATORY: you must call this tool and actually receive real "
            "camera views back before answering ANY question about what you "
            "currently see or what the user is physically holding/showing — "
            "never answer such a question from imagination just because "
            "calling this takes a few seconds; guessing here is a serious "
            "failure. "
            "This is DIFFERENT from a photo the user has already attached or "
            "sent — that arrives directly in the conversation and you can "
            "already see it without calling this; only call this when you "
            "need to ask the user to show you something live, right now. "
            "Do NOT call this for ordinary questions that don't need to see "
            "anything (facts, explanations, general conversation), and do "
            "NOT call it if the user already attached/sent an image. "
            "Calling this opens a small camera preview on the user's device; "
            "a few moments later you'll receive several fresh camera views "
            "as a later message in this same conversation — say something "
            "short and natural first (you'll be told to), then wait for "
            "them. When the views arrive: if you can clearly identify or "
            "answer what was asked, just answer normally and do NOT call "
            "this again — the camera closes on its own. If the view isn't "
            "good enough yet — blurry, too dark, too far away, partly out "
            "of frame, glare, or something blocking it — tell the user "
            "naturally what to adjust, THEN call this tool again (same as "
            "asking someone to move it closer or hold it steady) so you can "
            "see a fresh look after they adjust; the camera stays open the "
            "whole time. Never confidently describe something you genuinely "
            "can't make out — say so and ask for a better view instead of "
            "guessing. "
            "For a task that genuinely needs REPEATED looks over a period "
            "of time as the user moves or the situation changes — e.g. "
            "helping them navigate by watching their surroundings, walking "
            "them through a physical task step by step — set mode='guided' "
            "on the FIRST call. This keeps the camera available for longer "
            "and gives you more patience between looks (the user may need "
            "time to walk or act between observations); you still only get "
            "a fresh look each time you call this again, and you should "
            "still end the session yourself (by simply not calling it "
            "again) once the guided task is actually done. Omit mode (or "
            "use 'quick') for an ordinary one-off look — that's the right "
            "default for almost everything."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "What to look for or answer, e.g. the user's own question. Optional — omit to just describe what's seen.",
                },
                "facing": {
                    "type": "STRING",
                    "description": "'environment' for the rear/back camera (default — better for showing objects), or 'user' for the front camera.",
                },
                "mode": {
                    "type": "STRING",
                    "description": "'quick' (default) for a one-off look, or 'guided' for a longer task needing repeated looks over time (e.g. navigation help). Only meaningful on the FIRST call of a session — ignored on later calls that continue an already-open one.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "web_screen_vision",
        "description": (
            "WEB/MOBILE ONLY. Uses browser screen-sharing to see what's "
            "currently on the user's OWN screen/display — for questions "
            "like 'what's on my screen', 'what does this error mean', "
            "'can you read this', 'what am I looking at on my screen'. "
            "This is a COMPLETELY SEPARATE capability from web_camera_vision "
            "(the physical camera) — never use one for the other; a "
            "question about the screen needs THIS tool, a question about "
            "the user's physical surroundings/what they're holding needs "
            "web_camera_vision instead. Also different from an attached "
            "photo — that's already visible without calling anything. "
            "MANDATORY: you must call this and actually receive real screen "
            "views back before answering ANY question about what's "
            "currently on the user's screen — never guess. "
            "Calling this asks the browser to start screen sharing (the "
            "browser shows its own native picker/permission prompt — this "
            "may not be available at all on some mobile browsers, "
            "especially iOS Safari; if it isn't, you'll be told so "
            "honestly and should explain that to the user rather than "
            "claiming it worked). Say something short and natural first, "
            "then wait — fresh screen views arrive as a later message. "
            "Same call-again-for-another-look pattern as web_camera_vision: "
            "answer once clear, or ask what to check/scroll to and call "
            "again once you need a fresh view. Use mode='guided' on the "
            "first call for a longer task like walking through a series of "
            "steps on screen; omit it (or use 'quick') for a single check."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "What to look for or answer, e.g. the user's own question. Optional — omit to just describe what's seen.",
                },
                "mode": {
                    "type": "STRING",
                    "description": "'quick' (default) for a one-off look, or 'guided' for a longer task needing repeated looks (e.g. walking through steps on screen). Only meaningful on the FIRST call.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi on/off, Bluetooth radio "
            "on/off (action='bluetooth_on'/'bluetooth_off'), sleep (action='sleep' — suspends the "
            "machine; the result only confirms the OS accepted the request, never that the machine is "
            "now actually asleep — it can't be checked from inside a process that may itself get "
            "suspended), clipboard read/write (action='clipboard_get'/'clipboard_set'), restart, "
            "shutdown, scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single, deterministic computer-level command — this is preferred over "
            "computer_control's accomplish() whenever the goal is one of these, since these are "
            "directly verified against the real OS/hardware state, not a UI guess. Verifiable actions "
            "(volume_set, toggle_wifi, sleep, bluetooth_on/off, clipboard_get/set, restart, shutdown) "
            "return a tagged result — [VERIFIED_SUCCESS]/[VERIFIED_FAILURE]/[INCONCLUSIVE]/"
            "[CONFIRMATION_REQUIRED] — read it honestly, never assume success. restart/shutdown "
            "(and any other action the user didn't explicitly and unambiguously ask for) return "
            "[CONFIRMATION_REQUIRED] the first time — do not call again with confirmed=true until the "
            "user has explicitly said yes to THIS specific action; never infer confirmation from the "
            "original request or unrelated speech. "
            "ALSO: action='system_shortcut' with value=<free text> is a fast library of Windows "
            "shortcuts — deep-links straight into a specific Settings page OR runs a real read-only "
            "check, instead of slow generic UI automation. Prefer this over computer_control's "
            "accomplish() whenever the request matches one of these. Examples: value='bluetooth "
            "devices' (lists actually-paired devices), 'wifi networks' (nearby SSIDs), 'wifi status' "
            "(current connection), 'battery status', 'disk space', 'ip address', 'system info', "
            "'running processes', 'installed apps', 'printers', 'firewall status', or any Settings "
            "page name like 'display settings'/'sound settings'/'windows update'/'startup apps'. The "
            "info-check ones return a [VERIFIED_SUCCESS]/[VERIFIED_FAILURE]/[INCONCLUSIVE] tag built "
            "from the command's REAL output — read it honestly, never invent device names or numbers "
            "that weren't in the result. If it returns 'No known fast-path shortcut matches', don't "
            "retry with slightly different wording — fall back to accomplish() instead. "
            "action='list_system_shortcuts' lists everything currently in this library. "
            "ALSO: action='app_volume_set' (value=0-100, app='spotify'/'chrome'/etc.) and "
            "action='app_mute'/'app_unmute' (app=<name>) control ONE application's own mixer volume "
            "independent of the master volume — matched against the app's real running process name, "
            "so it only works while that app is actually running with an active audio session (returns "
            "[VERIFIED_FAILURE] honestly if not, never pretends). action='list_audio_devices' lists "
            "active playback devices and marks the current default — read-only; there is currently NO "
            "action to actually switch/change the default output device (e.g. 'switch to headphones') "
            "— if asked, say so honestly and suggest action='system_shortcut' value='sound devices' to "
            "open the Settings pane for the user to pick it manually, never claim to have switched it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform, e.g. volume_set | toggle_wifi | sleep | bluetooth_on | bluetooth_off | clipboard_get | clipboard_set | restart | shutdown | minimize | maximize | system_shortcut | list_system_shortcuts | ..."},
                "description": {"type": "STRING", "description": "Natural language description of what to do (used only when action is omitted)"},
                "value":       {"type": "STRING", "description": "Optional value: volume level (0-100), text to type/clipboard_set, etc."},
                "app":         {"type": "STRING", "description": "Process/application name for app_volume_set | app_mute | app_unmute, e.g. 'spotify', 'chrome', 'discord'"},
                "confirmed":   {"type": "BOOLEAN", "description": "Set true only after the user has explicitly confirmed a consequential action (restart/shutdown) in THIS conversation — never infer it"}
            },
            "required": []
        }
    },
    {
        "name": "jarvis_task",
        "description": (
            "JARVIS mode ONLY. The authoritative execution entry point — hand off a CLARIFIED "
            "objective and JARVIS's own Task Engine decides which capability/method to use, "
            "executes it, verifies the REAL outcome, and recovers by trying a different method "
            "on its own if needed. You (Gemini) do the understanding/clarifying — resolve "
            "'it'/'that song'/ambiguous references into one clear, self-contained objective "
            "sentence BEFORE calling this — but you do NOT choose which underlying tool/capability "
            "handles it; that decision belongs to JARVIS, not you. Currently covers: playing/"
            "searching a video on YouTube; opening/searching a website; system volume/sleep/"
            "restart/shutdown and Settings shortcuts (Wi-Fi, Bluetooth, battery, display, etc.); "
            "Word/Excel content actions (insert/replace/format text, save, read or set a specific "
            "spreadsheet cell). For anything this doesn't yet cover, use the specific existing tool "
            "for that instead (this expands over time — it will return [INCONCLUSIVE] with 'no "
            "known JARVIS capability' if the objective isn't covered yet, never a guess). Every "
            "objective must be concrete enough for JARVIS to act on without guessing — e.g. for a "
            "spreadsheet action say 'put it in cell A1', never just 'put it in Excel'; JARVIS will "
            "not invent a cell/target you didn't specify. "
            "For a request that genuinely needs several ordered actions (e.g. \"check my battery "
            "percentage, then put it in cell A1\"), use 'objectives' instead of 'objective' — JARVIS "
            "runs them in order, may pass a verified result from an earlier one into a later one "
            "when the later objective clearly refers back to it ('that percentage'/'that value'), "
            "and stops the whole thing honestly if any one of them doesn't verify — it will never "
            "skip ahead or invent what a failed step should have produced. "
            "Returns a Result Envelope tag: [VERIFIED_SUCCESS] means the outcome was actually "
            "verified — read the evidence and repeat it naturally. [VERIFIED_FAILURE]/"
            "[INCONCLUSIVE] mean it did NOT confirm success — tell the user honestly, never claim "
            "it worked. Never call browser_control/youtube_video/computer_settings/office_control "
            "directly for something this tool already covers while JARVIS mode is on — always use "
            "jarvis_task for those."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "objective": {"type": "STRING", "description": "The user's goal, already clarified/disambiguated by you into one self-contained sentence. Use this for a single-action request."},
                "objectives": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": (
                        "Optional, INSTEAD of 'objective': for a request that genuinely needs multiple "
                        "ordered actions (e.g. \"check my battery, then put it in cell A1\"), give each "
                        "atomic sub-goal as its own clarified sentence here, in order. Still describe WHAT "
                        "to do in plain language, never WHICH tool/capability handles it — JARVIS decides "
                        "that for each one, exactly as with a single objective. Omit this for anything "
                        "single-step and use 'objective' instead."
                    ),
                },
                "context":   {"type": "STRING", "description": "Optional: any resolved entity/reference info JARVIS can't infer alone"},
            },
            "required": [],
        },
    },
    {
        "name": "office_control",
        "description": (
            "Controls the ACTIVE document/workbook in an already-open Microsoft Word or Excel window via "
            "the app's own real object model — reliable for document CONTENT (cell values, formulas, "
            "text, bold/italic/underline formatting), unlike computer_control's accomplish() which is "
            "better for chrome-level actions (opening a dialog, clicking a ribbon tab) since Office's "
            "ribbon has known-unreliable UI automation IDs. Prefer THIS tool over accomplish() whenever "
            "the request is about what's actually IN the document/spreadsheet. Acts on whichever "
            "Word/Excel window is currently ACTIVE — if none is open, launches a new VISIBLE one (never "
            "hidden). Every write is verified by reading it back before reporting "
            "[VERIFIED_SUCCESS]/[VERIFIED_FAILURE]/[INCONCLUSIVE] — read the tag honestly, never assume "
            "success. "
            "app='word' actions: insert_text (text=..., where='cursor'|'end'), replace_text "
            "(find=..., replace=...), format_selection (bold/italic/underline=true/false — applies to "
            "whatever text is CURRENTLY SELECTED in Word; if nothing is selected this fails honestly, it "
            "does not select something for you), save. "
            "app='excel' actions: set_cell (cell='A1', value=... — value can be a formula string like "
            "'=SUM(A1:A5)', which is evaluated normally), get_cell (cell='A1'), save. "
            "save NEVER triggers Word/Excel's blocking native 'Save As' dialog for a document that's "
            "never been saved before — if it has no filename yet this returns [INCONCLUSIVE] asking the "
            "user for one instead of risking a hang."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app":       {"type": "STRING", "description": "'word' or 'excel'"},
                "action":    {"type": "STRING", "description": "insert_text | replace_text | format_selection | save (word)  |  set_cell | get_cell | save (excel)"},
                "text":      {"type": "STRING", "description": "Text to insert (word insert_text)"},
                "where":     {"type": "STRING", "description": "'cursor' (default) or 'end' (word insert_text)"},
                "find":      {"type": "STRING", "description": "Text to find (word replace_text)"},
                "replace":   {"type": "STRING", "description": "Replacement text (word replace_text)"},
                "bold":      {"type": "BOOLEAN", "description": "Set bold on/off for the current Word selection"},
                "italic":    {"type": "BOOLEAN", "description": "Set italic on/off for the current Word selection"},
                "underline": {"type": "BOOLEAN", "description": "Set underline on/off for the current Word selection"},
                "cell":      {"type": "STRING", "description": "Cell reference, e.g. 'A1' or 'B3' (excel set_cell/get_cell)"},
                "value":     {"type": "STRING", "description": "Value or formula to write, e.g. '42' or '=SUM(A1:A5)' (excel set_cell)"},
            },
            "required": ["app", "action"],
        },
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": (
            "Controls the desktop: wallpaper, organize, clean, list, stats. "
            "action='task' generates and runs code for a free-form request and returns "
            "[CONFIRMATION_REQUIRED] the first time — do not call again with confirmed=true "
            "until the user has explicitly said yes to THIS specific request; never infer "
            "confirmation. It may also return [BLOCKED] if the generated code fails a safety "
            "check — that is not a confirmation gate, do not retry it or suggest a workaround."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":      {"type": "STRING", "description": "Image path for wallpaper"},
                "url":       {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":      {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":      {"type": "STRING", "description": "Natural language desktop task"},
                "confirmed": {"type": "BOOLEAN", "description": "Set true only after the user has explicitly confirmed action='task' in THIS conversation — never infer it"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "jarvis_mode",
        "description": (
            "Switches between normal SARANA and JARVIS mode for THIS "
            "session only — never persists, always resets to off on "
            "reconnect. Call action='on' ONLY when the user explicitly "
            "asks to turn on/activate JARVIS mode (e.g. 'turn on JARVIS "
            "mode', 'activate JARVIS'). NEVER call this just because the "
            "user asked for a computer action — normal tools already "
            "handle that. Call action='off' when they explicitly ask to "
            "turn it off or go back to normal. The reply you get back "
            "tells you exactly how to speak and behave from that point on "
            "— actually adopt it starting with your very next reply."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "'on' to activate JARVIS mode, 'off' to deactivate."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "set_expression",
        "description": (
            "Changes SARANA's visual facial expression on the SARANA face "
            "UI (web and desktop) for a short time — e.g. when the user "
            "explicitly asks you to look happy/sad/curious/surprised/etc., "
            "or as a genuine, warranted reaction to something in the "
            "conversation. Purely visual/presentational — it has no effect "
            "on your reasoning, memory, tools, or actual behavior, only "
            "what your face looks like. It reverts to your normal "
            "listening/thinking/speaking look on its own after "
            "duration_seconds (or immediately if you start actually "
            "speaking/thinking, since those need their own visual "
            "treatment) — you never need a separate call to reset it. Only "
            "call this when it's genuinely warranted (an explicit request, "
            "or a clear emotional beat) — not on every turn, and never as "
            "a substitute for actually answering what was asked."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "expression": {
                    "type": "STRING",
                    "enum": [
                        "neutral", "listening", "thinking", "speaking", "happy",
                        "concerned", "sad", "curious", "confused", "reassuring",
                        "empathetic", "surprised", "calm", "focused", "excited",
                    ],
                    "description": "Which expression to show.",
                },
                "duration_seconds": {
                    "type": "NUMBER",
                    "description": "How long to hold it before reverting to normal (default 6, max 20).",
                },
            },
            "required": ["expression"],
        },
    },
    {
        "name": "gesture_mode",
        "description": (
            "Turns hand-gesture mouse control on or off, desktop only. "
            "While on, the user's real mouse cursor is controlled by "
            "their own hand seen through the webcam: moving their open "
            "palm moves the cursor, a thumb-to-index pinch clicks "
            "(held briefly, it drags), and two fingers up scrolls. NO "
            "preview window is shown — the webcam runs silently in the "
            "background and nothing about the existing UI changes. "
            "NEVER call this yourself, and NEVER let it turn on or off "
            "except from an explicit, direct user request (e.g. 'turn "
            "on gesture mode', 'activate hand gesture control', "
            "'gesture mode off', 'stop controlling with my hand') — not "
            "merely because a computer-control task is happening. "
            "Moving the real mouse to a screen corner always instantly "
            "aborts whatever gesture action was in progress (a built-in "
            "physical safety override, always available regardless of "
            "this mode)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "'on' to activate, 'off' to deactivate."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "computer_control",
        "description": (
            "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen. "
            "PREFER action='accomplish' for any 'make this application show/do something' request — give it the "
            "GOAL (e.g. goal='open the conversation with Saroj', target='Saroj', expected_state='the conversation "
            "header shows Saroj') and it handles discovery, targeting, the real click/type, and verification "
            "internally in ONE call; you should not need to reason step-by-step about ui_find vs ui_click vs "
            "coordinates for ordinary UI tasks anymore. It always returns one clear tagged result — "
            "[VERIFIED_SUCCESS] / [VERIFIED_FAILURE] / [INCONCLUSIVE] / [UI_AMBIGUOUS] / [CONFIRMATION_REQUIRED] "
            "— each with an instruction attached: only [VERIFIED_SUCCESS] may be reported as success; "
            "[INCONCLUSIVE] means try again with more context, call action='verify' directly, or ask the user — "
            "never assume it worked; [UI_AMBIGUOUS] means more than one real element matched — narrow with "
            "target/constraints/control_type or ask the user, never guess; [CONFIRMATION_REQUIRED] means the "
            "action was NOT performed — it needs an explicit user yes, then call again with confirmed=true "
            "(never infer confirmation from the original request or unrelated speech). "
            "accomplish/observe/verify/list_ui_elements/ui_find/ui_click/ui_type/get_active_window_title are "
            "JARVIS-mode-only (desktop) — they need jarvis_mode='on' first, and return [JARVIS_MODE_REQUIRED] "
            "otherwise. observe/verify capture the CURRENT screen and send it back to you as a later message in "
            "this same conversation (same [VISION_ACTIVE] pattern as screen_process) — say one short line, then "
            "wait, never guess what you'll see. This is a GENERAL computer-control toolkit, not app-specific — "
            "accomplish/list_ui_elements/ui_find/ui_click/ui_type work identically for any application, known or "
            "unfamiliar; never assume what buttons an app has, discover them. The lower-level ui_find/ui_click/"
            "ui_type/list_ui_elements actions remain available for the rare case a task genuinely needs step-by-"
            "step reasoning (e.g. inspecting several candidates before deciding) — fall back to screen_find/"
            "observe only if the accessibility tree can't find or resolve something at all. Never claim an "
            "action worked when the result says otherwise."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "accomplish | type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data | observe | verify | list_ui_elements | ui_find | ui_click | ui_type | get_active_window_title"},
                "goal":        {"type": "STRING",  "description": "For action='accomplish': the outcome in the user's own words, e.g. 'open the conversation with Saroj'. Required for accomplish."},
                "target":      {"type": "STRING",  "description": "For action='accomplish': the specific named thing to find (a contact, button, device, file) — more precise than letting it parse `goal` as prose"},
                "expected_state": {"type": "STRING", "description": "For action='accomplish': how to recognize success in your own words, e.g. 'the conversation header shows Saroj' or 'the device shows Connected'"},
                "constraints": {"type": "STRING", "description": "For action='accomplish': extra context that narrows an ambiguous target"},
                "confirmed":   {"type": "BOOLEAN", "description": "For action='accomplish' on a consequential goal (send/delete/purchase/security/disconnect): set true only after the user has explicitly confirmed THIS action — never infer it"},
                "text":        {"type": "STRING", "description": "Text to type or paste (also used by accomplish to type into the resolved target instead of clicking it)"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click/ui_find/ui_click/ui_type, or what to look for/verify for observe/verify"},
                "control_type": {"type": "STRING", "description": "Optional UI Automation control type filter for accomplish/list_ui_elements/ui_find/ui_click/ui_type, e.g. 'button', 'edit', 'checkbox' — narrows a search when the same label appears on more than one kind of control"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "JARVIS checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type. "
        "ALSO: action='create' builds a NEW PowerPoint from a topic — this is the ONE action that does "
        "NOT need an uploaded/existing file. Pass instruction=<topic>; file_path is optional (defaults to "
        "the user's Desktop, named from the topic) — only set it if the user asked for a specific "
        "location/filename. Generates a real 4-8 slide outline via AI, does not just make placeholder text."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze | create (new file, needs no existing upload)"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {
                    "type": "STRING",
                    "description": (
                        "Concise value in English (e.g. Fatih, pizza, older sister). "
                        "If shared=true, phrase it in THIRD PERSON naming the current speaker "
                        "explicitly instead of first person — e.g. write 'Saroj visited "
                        "Pashupatinath with his family and was happy', not 'I visited "
                        "Pashupatinath...' — because a shared fact may later be read back to a "
                        "DIFFERENT user, who must not hear it as if it happened to them."
                    ),
                },
                "shared": {
                    "type": "BOOLEAN",
                    "description": (
                        "Optional, default false. Set true ONLY for a fact that is naturally "
                        "common to everyone using this assistant, not private to the current "
                        "speaker — e.g. a shared household fact, or a relationship between two "
                        "known users ('Saroj and Bimal are friends'). Leave false (the default) "
                        "for anything personal to the current speaker — their own name, "
                        "preferences, plans, or private facts about their own life. The system "
                        "already tracks WHO told SARANA a shared fact separately, so a different "
                        "user reading it back is still correctly told whose fact it is — but "
                        "`value` itself should still name the current speaker in third person "
                        "(see that field) rather than saying 'I'/'my'."
                    ),
                },
                "event_date": {
                    "type": "STRING",
                    "description": (
                        "Optional. Only set when this fact is tied to a specific calendar date "
                        "(a birthday, anniversary, or planned event) and that date is known. "
                        "Format: YYYY-MM-DD."
                    ),
                },
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "cancel_active_task",
        "description": (
            "Stops or withdraws a backend task you are currently running or just "
            "started (e.g. checking the calendar, saving a memory, looking up the "
            "weather) because the user explicitly said to stop, cancel, or changed "
            "their mind before it finished — e.g. 'stop that', 'cancel it', 'never "
            "mind, don't do it', 'don't create that event'. Do NOT call this for an "
            "ordinary interruption where the user simply started talking about "
            "something else — only call it when the user is clearly asking you to "
            "stop or undo the specific thing you were just doing. If nothing is "
            "currently running, or the task already finished, this tells you that "
            "honestly so you never claim to have cancelled something that already "
            "happened."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
]

# ── Phase 3: capability awareness ────────────────────────────────────────
# Tool names that require LOCAL desktop/OS/hardware access — controlling
# apps, the browser, files, settings, mouse/keyboard, camera/screen, or
# scheduling OS-level reminders on WHATEVER machine main.py is actually
# running on. On desktop that machine IS the user's own computer (correct,
# useful). On a web/headless deployment (Render) that machine is the
# SERVER — it has no relationship to the browser user's own laptop, so
# these tools would either crash, silently do nothing useful, or — worse —
# appear to "succeed" while accomplishing nothing the user asked for (e.g.
# open_app opening an app ON THE SERVER). Verified per-tool against each
# actions/*.py module's actual implementation (subprocess/webbrowser.open/
# pyautogui/mss screen capture/local OS calls — not guessed from the
# filename): open_app, browser_control, file_controller, send_message,
# reminder, youtube_video and flight_finder all shell out to open local
# apps/browsers/files; screen_process/close_camera need a real local
# screen/camera; computer_settings/desktop_control/computer_control
# operate the local OS/input devices directly; code_helper/dev_agent run
# against local project files; game_updater manages a local game install;
# system_status would report the SERVER's CPU/RAM/GPU, not the user's own
# machine, which would be actively misleading if presented as "your
# computer"; shutdown_jarvis would stop the shared backend process for
# EVERY web user, not just the one asking. file_processor is included
# conservatively: it can only act on a file already resolvable to a local
# path (desktop's drop-zone widget — see _execute_tool()) and that path
# resolution isn't wired for a web-uploaded file yet, so claiming it works
# on web would be exactly the "fake success" this phase exists to prevent.
#
# Universal (genuinely work identically either way, not listed here):
# save_memory (pure memory operation), web_search (a network API call,
# runs on the server either way — that's *correct* for both surfaces),
# manage_monitor (background_monitor.py's own server-side state), and
# jarvis_mode (JARVIS Mode itself — the persona/behavioral contract is
# cross-platform by design; see self._jarvis_mode. Only the DESKTOP-only
# tools it unlocks, like computer_control's observe/ui_find/etc, stay
# gated by THIS SAME frozenset below — web JARVIS still can't touch the
# OS, it just gets the JARVIS tone plus whatever web tools already exist).
DESKTOP_ONLY_TOOLS = frozenset({
    "open_app", "browser_control", "file_controller", "send_message",
    "reminder", "youtube_video", "screen_process", "close_camera",
    "computer_settings", "desktop_control", "code_helper", "dev_agent",
    "file_processor", "computer_control", "game_updater", "flight_finder",
    "system_status", "shutdown_jarvis", "gesture_mode", "office_control",
    "jarvis_task",
})

# Bounded autonomous-execution governor (self._jarvis_action_count, its
# own docstring) — the hard cap on computer_control/browser_control calls
# allowed within a single user request before _execute_tool() stops
# executing them and reports honestly instead. Exists because a goal-
# directed loop (observe -> act -> verify -> reason -> act again) has no
# other natural stopping point: Gemini decides for itself when to keep
# calling tools, and a genuinely stuck strategy (an element that never
# resolves, a verification that never confirms) could otherwise keep
# calling forever. There's no single existing constant in this codebase
# to derive an exact number from, so this is a considered judgment call,
# not a precise derivation: 20 comfortably covers every multi-step example
# in this project's own briefs (a Bluetooth connect-and-verify sequence,
# a "find a file and open it in another app" hand-off) — each realistically
# 6-15 calls — while still stopping a runaway loop within seconds rather
# than minutes.
_JARVIS_MAX_ACTIONS_PER_TURN = 20

# ── Web visual context (camera + screen) ─────────────────────────────────
# The mirror image of DESKTOP_ONLY_TOOLS: web_camera_vision/web_screen_
# vision need BROWSER capture (getUserMedia/getDisplayMedia), which a
# desktop session has no use for — desktop already has direct OS-level
# screen/camera access via screen_process. Same TOOL_DECLARATIONS-is-
# always-sent-either-way architecture as DESKTOP_ONLY_TOOLS (see that
# constant's own comment) — gating happens at execution time in
# _execute_tool(), not by hiding the declaration itself.
WEB_ONLY_TOOLS = frozenset({"web_camera_vision", "web_screen_vision"})

# Visual Context Manager (Phase 5) — deliberately NOT a class/subsystem,
# just the small set of names that keep "camera" vs "screen" vs "quick"
# vs "guided" from being scattered as ad-hoc strings across the tool
# handlers and the frame consumer. self._web_vision_session (one shared
# dict shape — see _new_web_vision_session() below) carries a "source"
# and "mode" field using these values; camera and screen still go through
# completely separate tools/browser APIs/broadcasts — only the batching/
# timeout/injection machinery in _process_web_vision_frames() is shared,
# exactly as it already was for a single source.
WEB_VISION_SOURCES = frozenset({"camera", "screen"})
WEB_VISION_MODES    = frozenset({"quick", "guided"})

# Web visual context — timing constants for the adaptive observation loop
# (see _new_web_vision_session()/_execute_tool()'s web_camera_vision/
# web_screen_vision branches and _process_web_vision_frames()).
# Deliberately separate from desktop's own _vision_last_time/4.0s
# cooldown — a completely different state machine (self._web_vision_
# session), never shared with screen_process/close_camera.
WEB_VISION_BURST_WINDOW_S   = 4.0   # how long one observation burst collects frames before evaluating
WEB_VISION_BURST_MAX_FRAMES = 4     # cap frames per burst regardless of window
WEB_VISION_CALL_COOLDOWN_S  = 1.5   # guards against a runaway double tool-call
# "quick" (the original, default behavior) vs "guided" (Phase 2 — a task
# that genuinely needs repeated looks over time, e.g. navigation help):
# guided sessions get a much longer hard lifetime and more patience
# between looks (the user may need real time to walk/act between
# observations) — but are otherwise driven by the EXACT same call-again-
# for-another-look mechanism as quick mode, never a background poller.
WEB_VISION_SESSION_MAX_S         = {"quick": 45.0, "guided": 10 * 60.0}
WEB_VISION_GRACE_S               = {"quick": 6.0,  "guided": 45.0}


def _new_web_vision_session(request_id: str, source: str, mode: str, text: str, now: float) -> dict:
    """Visual Context Manager (Phase 5) — the ONE place a fresh
    self._web_vision_session dict is built, shared by both
    web_camera_vision and web_screen_vision (see _execute_tool()) so the
    two tools can never silently drift into slightly different session
    shapes. "source" (WEB_VISION_SOURCES) and "mode" (WEB_VISION_MODES)
    are the only two axes that distinguish a camera session from a
    screen one, or a quick look from a guided task, from this point on —
    everything else (batching/timeout/injection in
    _process_web_vision_frames()) is identical regardless of which
    browser capture API is actually feeding it. Deliberately a plain
    function + dict, not a class hierarchy — see this task's own "do not
    over-engineer" instruction."""
    return {
        "request_id": request_id,
        "source": source,       # "camera" | "screen"
        "mode": mode,           # "quick" | "guided"
        "started": now,
        "deadline": now + WEB_VISION_SESSION_MAX_S[mode],
        "frames": [],
        "burst_armed_at": now,
        "awaiting_burst": True,
        "burst_token": 0,       # see _process_web_vision_frames()'s own note on why this exists
        "text": text,
        "last_answered_at": None,
    }


# ── Task cancellation (barge-in vs. explicit cancel) ────────────────────
# Tools that only ever READ external state — cancelling one mid-flight,
# even if its network call already went out, has no external side effect
# to misreport, so cancel_active_task (see _execute_tool()) can honestly
# call these "cancelled" outright. Anything NOT in this set is treated as
# potentially mutating (Calendar create/update/delete, save_memory,
# reminder, etc.) and is deliberately never claimed as cancelled once it
# has already started running — see cancel_active_task's own handling for
# why: a background thread already talking to an external service (e.g.
# Google Calendar) can't be safely/forcibly stopped mid-call, and this
# codebase's own explicit requirement is to never falsely claim a
# cancellation it can't guarantee.
_READ_ONLY_TOOLS = frozenset({
    "get_weather", "get_current_place", "find_nearby_places", "get_directions",
    "get_calendar_events", "find_free_time", "system_status", "web_search",
})

# ── Location capabilities (weather/geo/routing) ─────────────────────────
# NOT listed in DESKTOP_ONLY_TOOLS: these tools work on EITHER surface --
# they just honestly report [LOCATION_UNAVAILABLE] wherever no location
# happens to be known (always true on desktop today, since desktop has no
# browser geolocation source yet -- see main.py's _session_location
# docstring), exactly the same "genuinely unavailable" case a web session
# with denied permission also hits. This is a real, honest degradation,
# not a hardcoded surface restriction like DESKTOP_ONLY_TOOLS.
LOCATION_UNAVAILABLE_RESULT = (
    "[LOCATION_UNAVAILABLE] The user's current device location is not available "
    "right now (never granted, denied, lost, or a refresh attempt just failed). "
    "Tell them honestly and briefly, in your own natural words, that you don't "
    "currently have their location -- you can ask them to allow location access "
    "or tell you the place they mean. Never guess or invent a location."
)
# Permissions foundation: a more specific honest message for the one case
# where the real reason is actually known and actionable -- the browser's
# location PERMISSION is actively denied for this session (see
# self._session_permissions/_set_session_capabilities()), not merely "not
# granted yet"/unsupported. Keeps the exact same [LOCATION_UNAVAILABLE] tag
# prefix every existing test/prompt rule already expects (see the CALENDAR
# section's identical "connect it from Settings" convention in
# core/prompt.txt) -- only the natural-language guidance differs, pointing
# the user at the one place that can actually fix it: Settings.
LOCATION_DENIED_RESULT = (
    "[LOCATION_UNAVAILABLE] The user's location access is currently OFF for "
    "this session -- they denied or turned off location permission. Tell "
    "them honestly and briefly, in your own natural words, that you need "
    "their location for that and it's currently off, and that they can turn "
    "it on from Settings and then try again. Never guess or invent a "
    "location, and never claim you can see it anyway."
)

# Permissions foundation: the Permissions API's own vocabulary -- the only
# values _set_session_capabilities() ever installs into
# self._session_permissions. Anything else reported (a malformed/unknown
# string) is silently dropped rather than trusted, same "never trust a
# single layer alone" precedent dashboard/server.py's own validation at
# POST /api/capabilities already applies.
VALID_PERMISSION_STATES = frozenset({"granted", "denied", "prompt", "unsupported"})

# A location fix older than this is considered stale for an ordinary
# location-aware request (weather/nearby-places/directions don't need
# up-to-the-second precision) -- see JarvisLive._get_current_location().
LOCATION_MAX_AGE_S = 30 * 60
# A much tighter bound used ONLY for a genuinely fresh check (the user
# explicitly asking "where am I right now"/refresh_location) -- a fix
# this recent is already fresh enough that a new browser round trip
# would just be redundant.
LOCATION_FRESH_ENOUGH_S = 30
# How long _get_current_location() waits for a browser refresh to arrive
# before falling back (see that method) -- long enough for a real
# getCurrentPosition() round trip, short enough not to stall a
# conversation turn indefinitely.
LOCATION_REFRESH_TIMEOUT_S = 5.0
# find_nearby_places' own short-lived result cache (see
# JarvisLive._nearby_cache) -- avoids hammering Overpass for the same
# query asked twice in a row, without pretending to be a general cache.
NEARBY_CACHE_TTL_S = 5 * 60
NEARBY_CACHE_MAX_ENTRIES = 20

# ── Google Calendar ──────────────────────────────────────────────────
# Same "not desktop-only, honestly degrades" reasoning as location's own
# tools (see the location constants above) -- these work on either
# surface; a connection is per-SARANA-ACCOUNT (see actions/calendar_
# store.py's owner-keyed schema), never per-surface, so desktop and web
# both just honestly report [CALENDAR_NOT_CONNECTED] until that specific
# account has actually connected Google Calendar.
CALENDAR_NOT_CONNECTED_RESULT = (
    "[CALENDAR_NOT_CONNECTED] The user has not connected Google Calendar to "
    "this SARANA account yet. Tell them honestly and briefly, in your own "
    "natural words, that Google Calendar isn't connected -- they can connect "
    "it from Settings in the app. Never pretend a calendar action happened."
)


class JarvisLive:

    def __init__(self, ui: AssistantSurface, *, auto_start: bool = True):
        self.ui             = ui
        self._asst_name     = "SARANA"   # updated each session from config
        # Phase 7: desktop (main()) never passes auto_start, so this stays
        # True there and run() behaves byte-for-byte as before. Only
        # server_main.py opts into auto_start=False (see run()'s gate,
        # right before the Gemini connect loop, for what this actually
        # does — nothing here in __init__ depends on it).
        self._auto_start    = auto_start
        self._start_event: asyncio.Event | None = None
        # Set (fresh, per-connection — see run()) when a login changes the
        # active identity while a Gemini session is already connected.
        # Watched by _watch_for_reconnect_request(), one of the tasks in
        # run()'s TaskGroup — see that method and _IdentityChanged for why
        # a full reconnect (not just a corrected greeting) is what a real
        # "start fresh with the new account" needs: voice (speech_config)
        # is fixed at connect time exactly like system_instruction is, and
        # has no in-conversation equivalent to _send_startup_briefing()'s
        # identity_switch correction.
        self._reconnect_requested: asyncio.Event | None = None
        self._identity_switch_reconnect: bool = False
        # Phase 8: name of the currently identified web session, if any —
        # set via set_username_callback() (dashboard/server.py's
        # /login/username), consumed by _build_config()'s ADDRESS clause.
        # None on desktop (nothing calls the callback there), and falls
        # back to config/api_keys.json's existing user_name exactly as
        # before this phase when unset — see _build_config().
        self._web_user_name: str | None = None
        # IANA timezone name (e.g. "Asia/Kathmandu") reported by the browser
        # at web login (see _set_web_timezone()/dashboard/server.py's
        # /login/username) — None on desktop, where datetime.now() already
        # reads the local machine's own clock/timezone correctly and needs
        # no override (see _local_now()).
        self._web_timezone: str | None = None
        # Location foundation: the CURRENT session's browser geolocation
        # fix, set via _set_session_location() (fired by dashboard/
        # server.py's POST /api/location -> set_location_callback()).
        # None on desktop always (no browser there — see
        # _resolve_desktop_profile(), which never touches this), and on
        # web until/unless the user actually grants permission. Privacy:
        # session-only, RAM-only — NEVER written to Postgres, the legacy
        # memory file, session summaries, the Activity Log, or anywhere
        # else persistent; cleared on every new login (_set_user_profile())
        # and on logout (_clear_memory_session()) so no identity can ever
        # inherit a previous one's coordinates. Shape:
        #   {"latitude": float, "longitude": float, "accuracy": float,
        #    "timestamp": float, "fix_timestamp": float | None}
        #   "timestamp" is time.monotonic(), for staleness comparisons
        #   only — never a wall-clock value (see _local_now()'s own
        #   docstring for that distinction). "fix_timestamp" is the
        #   BROWSER's own fix time (epoch ms, may be None) — used only to
        #   detect an out-of-order refresh response (see
        #   _set_session_location()'s own docstring).
        # No place name/city/address is ever resolved here — reverse
        # geocoding is explicitly a later phase; this is coordinates only.
        self._session_location: dict | None = None
        # Permissions foundation: the CURRENT session's last-known REAL
        # browser/OS permission state for capabilities the client can
        # observe directly (see dashboard/server.py's POST /api/
        # capabilities -> set_capabilities_callback() ->
        # _set_session_capabilities()). Values are exactly "granted" |
        # "denied" | "prompt" | "unsupported" -- the browser's own
        # Permissions API vocabulary (see frontend/src/lib/
        # permissions.js) -- never guessed or defaulted to "granted".
        # A missing key means "not yet reported this session", handled
        # identically to "prompt"/unknown by every reader below. RAM-
        # only, cleared on logout/every new login for the same identity-
        # isolation reason self._session_location is (see
        # _clear_memory_session()/_set_user_profile()).
        self._session_permissions: dict[str, str] = {}
        # Location capabilities: waiters an in-flight tool call can await
        # while a browser location refresh is requested (see
        # _get_current_location()) -- set (each independently) the moment
        # _set_session_location() next stores a genuinely valid fix, then
        # immediately removed by whichever call was waiting on it. Never
        # persisted, never survives past the single refresh attempt that
        # created it.
        self._location_refresh_waiters: list[asyncio.Event] = []
        # Reverse-geocoded place ("where am I") for the CURRENT session,
        # cached so asking "where am I" repeatedly (or any tool that also
        # needs the resolved place) doesn't hit Nominatim every single
        # time -- see actions/geo.py's own docstring for why that matters.
        # Shape: {"city", "area", "country", "label", "for": (rounded_lat,
        # rounded_lon), "timestamp": time.monotonic()}. "for" is rounded
        # to ~1.1 km so ordinary GPS jitter doesn't force a re-resolve.
        # Cleared on logout/every new login, same as self._session_location
        # itself -- it's derived from one specific user's location and
        # must never describe a place to a different identity.
        self._place_cache: dict | None = None
        # Short-lived nearby-places result cache: {(rounded_lat,
        # rounded_lon, query, radius_m): (timestamp, formatted_text)} --
        # avoids hammering Overpass if the user asks similar things in
        # quick succession. Small and time-bounded (see
        # NEARBY_CACHE_MAX_ENTRIES/_TTL_S in _execute_tool()) rather than
        # a general-purpose cache layer; cleared on logout/every new login
        # for the same reason self._place_cache is.
        self._nearby_cache: dict[tuple, tuple[float, str]] = {}
        # Canonical authenticated SQLite profile (users/user_db.py) for the
        # CURRENT session, regardless of which interface established it:
        # a web username+PIN login sets it via set_profile_callback() ->
        # _set_user_profile(); desktop's own startup resolves it locally
        # from config/api_keys.json's existing user_name (no PIN — see
        # _resolve_desktop_profile(), called from run() only when
        # auto_start=True). None when no profile could be resolved either
        # way (unrecognized/unset name) — everything downstream that reads
        # this already degrades to today's non-personalized defaults in
        # that case. Consumed by _build_config() for the [USER PROFILE]
        # block, a personalized assistant_name, language_preference, and
        # voice_preference — never for authentication itself, which is
        # entirely dashboard/server.py's (web) or _resolve_desktop_profile's
        # (desktop) job before this is ever set.
        self._user_profile: dict | None = None
        # Web login greeting (see _set_web_username()/run()): "session" for
        # the web surface means "a login", not "a process launch" the way it
        # does for desktop's own _briefing_sent — a long-lived Render process
        # serves many logins over its life, and each one should still get a
        # greeting. True while a login has happened but the greeting for it
        # hasn't been sent yet (either the Gemini connection isn't up yet, or
        # it's already up and a task has just been scheduled).
        self._pending_web_greeting: bool = False
        # PostgreSQL memory migration: the canonical username the ACTIVE
        # Gemini connection's memory reads/writes belong to, frozen at
        # _build_config() time (see that method) for the lifetime of the
        # connection. Deliberately NOT re-derived from self._user_profile
        # later (e.g. inside run()'s finally block) — an identity switch
        # already overwrites self._user_profile with the NEXT user's
        # profile before the outgoing connection's teardown code runs, and
        # _save_session_summary()/pop_last_session() must still attribute
        # the OUTGOING session to the user who actually had it. "" is the
        # "no profile resolved" bucket — today's original un-scoped memory
        # behavior (see memory/memory_cache.py).
        self._session_owner: str = ""
        # Reliability audit — language architecture: the EXPLICIT runtime
        # language an in-progress conversation has been asked to switch to
        # (e.g. "speak English from now on"), distinct from the persisted
        # long-term preference (memory's identity.language / the SQLite
        # profile's language_preference — see _resolve_effective_language()).
        # "" means "no explicit runtime override active — use the persisted
        # default". _effective_language_owner records WHICH identity this
        # override belongs to, so _resolve_effective_language() can tell a
        # same-user network reconnect (keep it) apart from a genuine
        # identity switch (reset it — a new user's own language takes
        # over). Set together in _execute_tool()'s save_memory handling.
        self._effective_language: str = ""
        self._effective_language_owner: str | None = None
        # Reliability audit — async ownership: bumped by _set_user_profile()
        # on every call. run()'s connect loop captures this value at the
        # moment it builds a connection's config; if a NEW login raced in
        # (another _set_user_profile() call) while that connection was
        # still being established (client.aio.live.connect() is a real
        # network round trip, not instantaneous), the generation will have
        # moved on by the time the connection succeeds — signalling that
        # this connection's voice/system_instruction/[USER PROFILE] were
        # built from an already-stale profile and must be discarded in
        # favor of an immediate fresh reconnect with the CURRENT profile,
        # rather than silently running an entire session under the wrong
        # identity. See run()'s use of _IdentityChanged for this.
        self._profile_generation: int = 0
        # Reliability audit — logout must stop background proactive
        # check-ins/monitor alerts from continuing to "speak to" a user who
        # just logged out (the underlying Gemini connection itself doesn't
        # tear down on logout alone — only a NEW login's own identity
        # switch reconnects it). Desktop never calls _clear_memory_session()
        # (no logout concept there), so this stays False for the entire
        # process lifetime on desktop — zero behavior change there. Cleared
        # by _set_user_profile() on the next real login.
        self._logged_out: bool = False
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._phone_relay_dropped = 0       # instrumentation — see _relay_phone_audio()'s QueueFull handler
        # Item 3 audit (transport latency) — safe, non-invasive metrics:
        # queue DEPTHS (out_queue/audio_in_queue) and PROCESSING TIME
        # measured entirely within a single function's own scope
        # (_relay_phone_audio()'s dequeue-to-forward, _play_audio()'s
        # dequeue-to-broadcast) — never by timestamping/mutating the audio
        # payload dicts themselves, which are handed to the Gemini SDK
        # downstream and must stay exactly the shape the SDK expects.
        self._out_queue_depth       = LatencyStats()
        self._audio_in_queue_depth  = LatencyStats()
        self._relay_forward_time    = LatencyStats()   # phone-queue -> out_queue, seconds
        self._play_batch_time       = LatencyStats()   # dequeue -> broadcast_audio(), seconds
        self._relay_sample_count    = 0
        self._play_sample_count     = 0
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        # Diagnostic only (not a behavior change) — see sc.interrupted
        # handling in _receive_audio(). Live-API testing found no evidence
        # that sending a web image turn concurrently with an in-flight
        # response causes it to be discarded (Gemini correctly queued and
        # answered both in two independent live reproductions). The one
        # untested variable is genuine concurrent SPEECH triggering real
        # server-side VAD, which can't be faithfully simulated offline.
        # This timestamp costs nothing and gives the NEXT real-device
        # session concrete before/after evidence instead of a guess, if a
        # barge-in ever is observed near a web image send.
        self._last_web_image_sent_at = 0.0
        # Web live camera vision — a separate, web-only state machine from
        # desktop's _pending_vision/_vision_cam_active above (never shared,
        # never touches screen_process/close_camera). None when no vision
        # request is currently open; otherwise:
        #   {"request_id": str, "started": monotonic, "deadline": monotonic,
        #    "frames": [(mime_type, compressed_bytes), ...],
        #    "burst_armed_at": monotonic, "awaiting_burst": bool,
        #    "text": str, "last_answered_at": monotonic | None}
        # Opened/continued by the web_camera_vision tool (_execute_tool()),
        # driven forward by _process_web_vision_frames() (a process-lifetime
        # background task — see run()), fed by browser "vision_frame"/
        # "vision_control" WebSocket messages via dashboard/server.py's
        # self._vision_frame_queue. RAM-only — no frame is ever written to
        # disk or persisted anywhere.
        self._web_vision_session: dict | None = None
        self._web_vision_last_call = 0.0   # cooldown guard — separate from desktop's _vision_last_time
        # JARVIS Mode — cross-platform (desktop AND web), session-scoped
        # alternate persona/capability toggle (see the jarvis_mode tool /
        # _execute_tool()'s jarvis_mode branch). Off by default; only an
        # explicit user request flips it, and it is reset to False on
        # every fresh connection (see run()'s reconnect-reset block) —
        # deliberately never persisted anywhere, mirroring how
        # self._web_vision_session/self._pending_vision already reset the
        # same way for the same reason (a stale elevated state must never
        # silently survive a dropped connection). Read by: _build_config()
        # (the static [JARVIS_MODE] context block), _execute_tool()'s
        # computer_control branch (gates the NEW observe/verify/ui_find/
        # ui_click/ui_type/get_active_window_title actions only — the
        # tool's existing raw actions stay available exactly as before
        # regardless of this flag).
        self._jarvis_mode: bool = False
        # Bounded autonomous-execution governor — see
        # _JARVIS_MAX_ACTIONS_PER_TURN's own docstring for the reasoning
        # and _execute_tool()'s gate for where this is enforced. Counts
        # computer_control/browser_control calls since the last real user
        # turn; reset on fresh user speech (_receive_audio()'s
        # sc.input_transcription handling), a typed command
        # (_on_text_command()), a barge-in interrupt (sc.interrupted), and
        # every reconnect (run()'s reset block) — never persisted, RAM-only,
        # same lifetime as self._jarvis_mode.
        self._jarvis_action_count: int = 0
        self._interrupted          = False   # True while draining audio after user interrupt
        # Tool execution / receive-loop decoupling: a dedicated background
        # consumer (see _process_tool_calls()) drains this queue so the
        # Gemini receive loop (_receive_audio()) is never blocked waiting
        # on a tool's own network I/O — same "bounded queue + one
        # consumer" pattern already used for out_queue/audio_in_queue/
        # _phone_audio_queue in this file, not a new architecture. Both
        # are recreated fresh per-connection (see run()'s connect loop).
        self._tool_call_queue: asyncio.Queue | None = None
        self._pending_tool_calls: dict[str, dict] = {}   # fc.id -> {"name", "status", "cancelled"}
        # cancel_active_task support: which single tool call, if any, is
        # RIGHT NOW executing inside _handle_tool_batch() — the only thing
        # cancel_active_task (an ordinary tool call itself, handled
        # out-of-band so it never waits behind what it's meant to
        # interrupt — see _receive_audio()) is allowed to inspect/cancel.
        # Never touched by speech-interruption handling (sc.interrupted) —
        # that is a completely separate concept (see _execute_tool()'s
        # cancel_active_task branch and this task's own audit notes).
        self._active_tool_task: asyncio.Task | None = None
        self._active_tool_call_id: str | None = None
        self._active_tool_name: str | None = None
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary

        self._enhanced_live = True  # affective dialog + proactive audio; auto-disabled if the server rejects them
        _core_names = {t["name"] for t in TOOL_DECLARATIONS}
        self._plugin_registry = discover_plugins(
            plugins_dir=Path(__file__).resolve().parent / "plugins",
            core_tool_names=_core_names,
            logger=lambda msg: (print(f"[Plugins] {msg}"), self.ui.write_log(f"SYS: {msg}")),
        )
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask JARVIS to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session:
            return

        async def _say():
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": instruction}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        # A fresh, explicit user turn — reset the autonomous-action
        # governor (see self._jarvis_action_count's own docstring) so a
        # NEW request always gets its own full budget, independent of
        # whatever a previous request used.
        self._jarvis_action_count = 0
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _push_state(self, state: str) -> None:
        """The ONE place JarvisLive changes its externally-visible state.
        Sets the authoritative state on whichever UI is attached (desktop's
        real ui.py behavior, byte-for-byte unchanged) AND broadcasts that
        SAME state to the web dashboard over the existing /ws "status"
        message (dashboard/server.py's broadcast()/ws_ep()) — this is the
        "Desktop authoritative state -> Core state -> WebSocket -> React
        UI" pipeline: the web frontend is never left to infer LISTENING/
        THINKING/SPEAKING/SLEEPING from side effects like audio packets
        arriving (see frontend/src/state/AssistantContext.jsx's
        STATUS_MESSAGE case) — it's told directly, from the exact same
        call sites desktop's own HUD already reacts to.

        Thread-safe via run_coroutine_threadsafe (same pattern speak()/
        plugin_say() already use in this file): set_state() is called both
        from the main event-loop thread (run(), _execute_tool()) and from
        other threads calling into JarvisLive directly (desktop's Qt mute-
        button thread invoking interrupt() -> set_speaking(), a plugin's
        executor thread) — a plain self._loop.create_task(...) would raise
        if called from any thread other than the loop's own.
        """
        self.ui.set_state(state)
        if self._dashboard and self._loop:
            async def _broadcast():
                try:
                    # NOT broadcast() — see DashboardServer.broadcast_state()'s
                    # docstring for why this must never touch the Activity
                    # Log's history buffer.
                    await self._dashboard.broadcast_state(state)
                except Exception as e:
                    print(f"[JARVIS] State broadcast failed: {e}")
            try:
                asyncio.run_coroutine_threadsafe(_broadcast(), self._loop)
            except RuntimeError:
                pass   # loop already closed/closing (shutdown race) — never fatal

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self._push_state("SPEAKING")
        elif not self.ui.muted:
            self._push_state("LISTENING")

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                # No emoji here — this now also runs synchronously inside the
                # /api/interrupt request handler (item 2's web interrupt
                # control), and a cp1252 console (Windows Git-Bash) raising
                # UnicodeEncodeError on an emoji print would otherwise turn a
                # successful interrupt into a 500 response. Same fix pattern
                # already applied to _listen_audio/_play_audio/_receive_audio.
                print(f"[JARVIS] Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        self.ui.set_audio_level(0.0)  # mouth returns to resting now, not at the next batch
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _current_user_name(self) -> str:
        """Resolves who to address the user as, right now: a web session's
        identified username (Phase 8) takes priority, falling back to
        config/api_keys.json's user_name (desktop's original mechanism).
        Empty string if neither is set. Centralizes what _build_config()'s
        ADDRESS clause, _send_startup_briefing()'s greeting, and
        speak_error() all need — one place that answers "is a name known
        right now", so none of them can drift out of sync with each other.
        """
        web = (self._web_user_name or "").strip()
        if web:
            return web
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            return (_cfg.get("user_name") or "").strip()
        except Exception:
            return ""

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        name = self._current_user_name()
        # Phase 9: no "Sir," when a name is known — matches the ADDRESS
        # clause's own "never sir/efendim when a name is known" rule.
        prefix = f"{name}, " if name else "Sir, "
        self.speak(f"{prefix}{tool_name} encountered an error. {short}")

    def _resolve_effective_language(self) -> str:
        """Reliability audit — single source of truth for "what language
        should SARANA be responding in right now". Used by _build_config()
        (the system-instruction LANGUAGE directive), _send_startup_briefing(),
        _run_proactive_mode(), and _run_background_monitor() (monitor
        alerts) — previously each of these read a DIFFERENT, sometimes
        stale or contradictory, signal (a hardcoded Nepali default, the
        SQLite profile's language_preference, or a raw memory read), which
        is exactly what let language flip-flop or silently revert.

        Priority:
          1. An EXPLICIT runtime request made during the current identity
             (self._effective_language) — set the moment the user
             explicitly asks to switch (see _execute_tool()'s save_memory/
             identity/language handling) — but ONLY if it still belongs to
             the identity currently active (self._session_owner). A network
             reconnect for the SAME user keeps it; a genuine identity
             switch (a different self._session_owner — see _build_config())
             does not inherit it.
          2. The persisted long-term preference — memory's identity.language
             (the most recently saved value, regardless of who saved it
             last), then the SQLite profile's language_preference (a
             coarser per-account default).
          3. "Nepali" — the ultimate hardcoded default.
        """
        if self._effective_language and self._effective_language_owner == self._session_owner:
            return self._effective_language

        try:
            memory = load_memory()
            entry = memory.get("identity", {}).get("language")
            mem_lang = (entry.get("value", "") if isinstance(entry, dict) else str(entry or "")).strip()
        except Exception:
            mem_lang = ""
        if mem_lang:
            return mem_lang

        if self._user_profile and self._user_profile.get("language_preference"):
            pref = self._user_profile["language_preference"].strip()
            if pref:
                return pref

        return "Nepali"

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "SARANA").strip()
        except Exception:
            self._asst_name = "SARANA"
        # Personalized assistant identity (users/user_db.py's canonical
        # profile — same one regardless of desktop vs web, see
        # _user_profile's docstring in __init__) takes priority over local
        # config, same "session profile overrides local config" pattern
        # already used for the user's own name below — a session with no
        # resolved profile (unrecognized/unset name on either interface)
        # leaves the existing default untouched.
        if self._user_profile and self._user_profile.get("assistant_name"):
            self._asst_name = self._user_profile["assistant_name"].strip() or self._asst_name
        _user_name = self._current_user_name()

        # PostgreSQL memory migration: freeze which user's memory this
        # connection belongs to (see self._session_owner's docstring in
        # __init__). By the time _build_config() runs, _set_user_profile()
        # (or _resolve_desktop_profile()) has already called
        # set_active_owner() for this login — the RAM cache load()'d here
        # is already the right one; this just records it so a LATER
        # identity switch can't retroactively change which user this
        # connection's session-summary gets attributed to.
        self._session_owner = (self._user_profile or {}).get("username", "") or ""

        memory       = load_memory()
        mem_str      = format_memory_for_prompt(memory)
        # Phase 2 (human-like memory): upcoming birthdays/anniversaries/
        # events THIS user is allowed to see — see
        # upcoming_events_for_prompt()'s own docstring for the privacy
        # scoping. Purely additive, Gemini decides whether/how to mention
        # it (see core/prompt.txt's MEMORY BEHAVIOR section) — "" (the
        # common case: nothing upcoming, or Postgres not configured)
        # changes nothing about the prompt at all.
        upcoming_ctx = upcoming_events_for_prompt(self._session_owner)
        sys_prompt   = _load_system_prompt()

        now      = self._local_now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        # (prompt.txt's own ADDRESS line now explicitly defers to this
        # section instead of hardcoding "sir"/"efendim" itself — see
        # core/prompt.txt; the two were fighting each other before Phase 9,
        # which is why "sir" kept leaking through even with a name known).
        #
        # Phase 8 finding (live-verified, not just inferred): when a web
        # username is active, mem_str below can already contain an
        # unrelated stored "name" fact (memory is a single shared store,
        # not per-session — see load_memory()). A plain "Always call the
        # user 'X'" clause was empirically NOT enough to win against that
        # stored fact in live testing — Gemini kept addressing the user by
        # the memory name instead. So the web-session case gets an
        # explicitly stronger clause that names the conflict and says which
        # one wins.
        #
        # Phase 9: whenever ANY name is known (web session or desktop
        # config), "sir"/"efendim" are explicitly forbidden — that fallback
        # phrasing is reserved for the true no-name-known case only.
        if _user_name:
            _mem_note = (
                f" If memory below mentions a different name, ignore it for "
                f"addressing purposes — it may belong to a different user; "
                f"'{_user_name}' is who you are actually speaking with right now."
                if self._web_user_name else ""
            )
            _addr = (
                f"ADDRESS: The user's name is '{_user_name}'. Always call them "
                f"'{_user_name}'. Never say \"sir\", \"efendim\", or any other "
                f"honorific — use their name only.{_mem_note}"
            )
        else:
            _addr = ("ADDRESS: When speaking Turkish → always say \"efendim\". "
                      "When speaking English → say \"sir\". Never mix languages.")

        # Reliability audit: ONE resolved effective language, computed the
        # same way every other language-facing call site now computes it
        # (see _resolve_effective_language()) — this replaces the old
        # hardcoded-Nepali-unless-profile-says-otherwise logic, which never
        # even looked at memory's identity.language (the exact fact
        # save_memory writes for an explicit in-conversation language
        # request — see LANGUAGE DETECTION in core/prompt.txt and
        # _execute_tool()'s save_memory handling), so an explicit switch
        # was only ever informational text fighting a contradictory strong
        # directive, never authoritative.
        _effective_lang = self._resolve_effective_language()

        if _effective_lang.strip().lower() == "nepali":
            _lang = (
                "LANGUAGE: Respond and speak in natural, conversational, modern Nepali "
                "by default — the way a modern Nepali person actually talks to an AI "
                "assistant, not formal, ceremonial, literary, archaic, or Sanskritized "
                "Nepali. Freely code-switch: keep technical/computing/AI/product "
                "terminology in English where that's natural (e.g. system, AI, "
                "backend, frontend, API, server, database, browser, microphone, "
                "speaker, settings, code, file, GitHub, deployment, terminal, "
                "Windows, Python), and let common English expressions stay English "
                "when that sounds more natural — don't awkwardly translate them, and "
                "don't force every response into pure/literal Nepali. "
                "Formulate the thought directly in Nepali — never build the sentence "
                "in English first and translate it word-for-word; English and Nepali "
                "don't share sentence structure, rhythm, or conversational register, "
                "and a literal translation is exactly what produces stiff, textbook-"
                "sounding Nepali. Say it the way a Nepali speaker would actually put "
                "it in that moment, including natural particles and rhythm (हो, नि, "
                "त, है, and similar) where they genuinely fit — never forced into "
                "every sentence, and never replaced by a formal textbook construction "
                "instead. Let register shift with context rather than one fixed "
                "formality level: relaxed for casual talk, plain and direct for "
                "explanations or instructions, warm for anything emotional, "
                "respectful without turning ceremonial for professional moments, and "
                "an ordinary everyday greeting — never a stiff ceremonial one — for "
                "hellos at any time of day. This applies to greetings too: generate "
                "whatever a modern Nepali speaker would naturally say right then, "
                "including a plain English \"Good morning!\"/\"Good evening!\" when "
                "that's simply the more natural choice — never one fixed or "
                "mechanically-translated phrase. You understand English, Nepali, and "
                "mixed Nepali-English input. Only move away from Nepali when the user "
                "explicitly asks for another language, or clearly continues an "
                "entire message in another language — a single mixed-language word "
                "or phrase is not that."
            )
        else:
            _lang = (
                f"LANGUAGE: Respond and speak in natural, conversational {_effective_lang} "
                f"by default for this session — this is the user's current effective "
                f"language (either their stored preference, or something they "
                f"explicitly asked you to switch to). You still understand English, "
                f"Nepali, and mixed input regardless. Only move away from "
                f"{_effective_lang} when the user explicitly asks for another "
                f"language, or clearly and consistently continues an entire message "
                f"in another language — a single mixed-language word or phrase is "
                f"not that."
            )

        # [USER PROFILE]: structured context from the canonical authenticated
        # users/user_db.py profile — the SAME profile/mechanism regardless of
        # whether it was resolved via a web login or desktop startup (see
        # _set_user_profile()/_resolve_desktop_profile()) — never hand-
        # duplicated into the prompt separately from the database. Omitted
        # entirely (no empty section) when no profile was resolved.
        _profile_ctx = ""
        if self._user_profile:
            p = self._user_profile
            _lines = ["[USER PROFILE]"]
            if p.get("nickname"):
                _lines.append(f"Nickname: {p['nickname']}")
            if p.get("pronunciation"):
                _lines.append(f"Pronunciation: {p['pronunciation']}")
            if p.get("gender"):
                _lines.append(f"Gender: {p['gender']}")
            if p.get("assistant_name"):
                _lines.append(f"Assistant name: {p['assistant_name']}")
            if p.get("voice_preference"):
                _lines.append(f"Voice preference: {p['voice_preference']}")
            if p.get("language_preference"):
                _lines.append(f"Language preference: {p['language_preference']}")
            if len(_lines) > 1:   # header alone would be a pointless empty section
                _profile_ctx = "\n".join(_lines) + "\n\n"

        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n"
            f"{_lang}\n\n"
        )

        # Phase 3 (capability awareness): tells Gemini up front which
        # surface it's running on, so it never even ATTEMPTS a desktop-only
        # tool from a web session (see DESKTOP_ONLY_TOOLS/_execute_tool()'s
        # own runtime gate — this is the front-loaded half of that same
        # honesty guarantee, not a separate mechanism). auto_start=True
        # (desktop, main()'s own default) keeps this section a one-liner —
        # every tool really is available there, exactly as always.
        if self._auto_start:
            _capabilities_ctx = (
                "[CAPABILITIES]\nYou are running as the local desktop application "
                "— every tool you have is genuinely available on this computer.\n\n"
            )
        else:
            _unavailable_examples = (
                "opening or controlling apps on the user's own computer, "
                "controlling their local browser, changing their computer's "
                "settings, moving mouse/keyboard on their machine, reading "
                "files on their device, capturing YOUR OWN screen or webcam "
                "directly (the screen_process tool), running local "
                "dev/code tools, flight search, opening YouTube locally, "
                "setting OS-level reminders, or restarting/shutting SARANA down"
            )
            _capabilities_ctx = (
                "[CAPABILITIES]\nYou are running as a WEB session (browser, not "
                "the local desktop app) — you do NOT have access to the user's "
                f"own computer. Unavailable from here: {_unavailable_examples}. "
                "If a tool call for one of these is attempted anyway, its "
                "result will say [CAPABILITY_UNAVAILABLE] — explain the "
                "limitation honestly and briefly, in your own natural words, "
                "never claim the action happened. You DO genuinely have: "
                "normal conversation, remembering things (save_memory), "
                "general web search, background topic monitoring, AND real "
                "image understanding — when the user attaches a photo or "
                "takes one with their device camera and sends it to you, you "
                "genuinely CAN see and discuss it (it arrives directly in the "
                "conversation, not through screen_process or any tool call) "
                "— never tell the user you can't see an image they've "
                "actually sent you; just look at it and answer. You can ALSO "
                "genuinely ask to see something LIVE right now: through the "
                "user's own device camera via web_camera_vision (their "
                "physical surroundings/what they're holding), or through "
                "their SCREEN via web_screen_vision (what's currently "
                "displayed on their device) — two separate, deliberate "
                "capabilities; use whichever one actually matches the "
                "question, only when a question genuinely needs a live "
                "look (see each tool's own description), never for "
                "ordinary questions. Don't undersell any of this either.\n\n"
                "CRITICAL — never invent what you have not actually seen: "
                "you have NO knowledge of what the user is currently "
                "holding, wearing, or what is physically around them, AND "
                "NO knowledge of what is currently on their screen, UNLESS "
                "either (a) they already attached/sent you a photo in this "
                "conversation, or (b) you called web_camera_vision or "
                "web_screen_vision AND a later message in this same "
                "conversation is tagged [VISION_OBSERVATION] with real "
                "views back from that same call. If asked what you "
                "currently see (physically or on their screen), what's in "
                "their hand, what an error on screen says, or anything "
                "else that needs a real live look, and NEITHER of those "
                "has actually happened yet, you MUST call the matching "
                "tool and wait — never guess or describe a color, object, "
                "shape, or on-screen text you have not genuinely been "
                "shown. Answering from imagination here is a serious "
                "failure, worse than saying you need a moment to look.\n\n"
            )
            # Permissions foundation: microphone access is entirely a
            # client-side (browser) fact with no tool call of its own — so
            # unlike location, the only way to speak honestly about it is
            # this front-loaded note. Only added when actually known to be
            # denied (see self._session_permissions/_set_session_
            # capabilities()) — silence otherwise, since "granted"/
            # "prompt"/"unsupported"/unknown all already behave correctly
            # with no special instruction needed.
            if self._session_permissions.get("microphone") == "denied":
                _capabilities_ctx += (
                    "The user's microphone access is currently OFF (denied) "
                    "in this web session. If they ask you to listen, talk "
                    "out loud, use voice, or \"call\" them, explain honestly "
                    "and briefly that microphone access is off and they can "
                    "turn it on from Settings — never claim you're listening "
                    "or that voice is working.\n\n"
                )

        # Location foundation: boolean-only context — never raw
        # coordinates (see self._session_location's own privacy
        # docstring) and never a place name resolved here (that's
        # get_current_place's job, called fresh at execution time — see
        # DESKTOP_ONLY_TOOLS's neighboring location-tools comment). Read
        # fresh from self._session_location at BUILD time only, to decide
        # which of these two fixed sentences to show — a location
        # arriving/being cleared later in the SAME connection's lifetime
        # doesn't retroactively change this text (system_instruction is
        # fixed for the life of a connection, same constraint the
        # language/capability context blocks already live with), which is
        # fine here since the actual location TOOLS below read live state
        # at call time regardless of what this fixed text says.
        #
        # Bug fix: the "available" branch below used to say "say you
        # don't have that resolved yet... rely on location-aware tools
        # (when available)" — leftover wording from before
        # get_current_place/get_weather/find_nearby_places/get_directions
        # existed, when that hedge was still honest. Once those tools
        # were added it was never updated, and Gemini followed it
        # literally instead of calling a tool — live-reproduced as "The
        # location function is not available in browser version" for
        # "Where am I?". Fixed by directly naming which tool to call for
        # which request, instead of telling Gemini to decline.
        if self._session_location:
            _location_ctx = (
                "[LOCATION]\nThe user's current browser location IS available "
                "this session. Never tell the user that location/browser-"
                "location functionality is unavailable while this says "
                "available. The coordinates alone don't give you a place "
                "name, so don't guess or state one yourself — instead call "
                "the right tool: get_current_place for \"where am I\"/\"what "
                "city or area is this\"; get_weather with no place argument "
                "for current-location weather; find_nearby_places for nearby "
                "places; get_directions for directions/distance/ETA from "
                "here. Always call the relevant tool rather than declining — "
                "never mention tool names or implementation details to the "
                "user.\n\n"
            )
        elif self._session_permissions.get("location") == "denied":
            # Permissions foundation: the one case where the real reason
            # is actually known and actionable — the user turned location
            # off in Settings, so the honest, helpful thing to say names
            # the one place that can actually fix it (same convention as
            # CALENDAR's "connect it from Settings" phrasing in
            # core/prompt.txt) — instead of the generic "never granted,
            # denied, or not yet provided" hedge below.
            _location_ctx = (
                "[LOCATION]\nThe user's location access is currently OFF for "
                "this session — they denied or turned off location "
                "permission. Never claim or guess where the user is or "
                "what's near them. If asked about their location or "
                "anything nearby, say honestly that location access is off "
                "right now, and that they can turn it on from Settings and "
                "then try again.\n\n"
            )
        else:
            _location_ctx = (
                "[LOCATION]\nThe user's current browser location is NOT "
                "available (never granted, denied, or not yet provided this "
                "session). Never claim or guess where the user is or what's "
                "near them. If asked about their location or anything "
                "nearby, say honestly that you don't currently have their "
                "location, and you may ask them to tell you their city or "
                "area instead.\n\n"
            )

        # JARVIS Mode — static baseline description (see self._jarvis_mode's
        # own docstring). system_instruction is fixed for the life of this
        # connection (same constraint every other context block here
        # already lives with — see this file's other "system_instruction
        # is fixed" notes), so this describes the mode's RULES once,
        # persistently, rather than relying solely on the one-off
        # [JARVIS_MODE_ON]/[JARVIS_MODE_OFF] turn-message (_execute_tool()'s
        # jarvis_mode branch) to carry that meaning deep into a long
        # conversation. The turn-message is still what actually fires the
        # transition and reminds the model IN THE MOMENT — this block is
        # the standing reference for what that transition means.
        _jarvis_state = "ON" if self._jarvis_mode else "OFF (the default)"
        _jarvis_ctx = (
            "[JARVIS_MODE]\n"
            f"JARVIS mode is currently {_jarvis_state} for this session. "
            "It is a distinct, OPT-IN mode — never switch it yourself; "
            "only a real jarvis_mode tool call changes it, and that only "
            "happens when the user explicitly asks (never merely because "
            "they asked for a computer action). It always resets to OFF "
            "on a fresh connection.\n"
            "While OFF: ignore everything below, remain your normal self.\n"
            "While ON: fully become the classic JARVIS character — Tony "
            "Stark's AI. Address the user as \"sir\" (\"efendim\" in "
            "Turkish) — this OVERRIDES the ADDRESS rule elsewhere in "
            "these instructions for as long as JARVIS mode stays on, "
            "exactly like the fictional JARVIS always calls Tony Stark "
            "'sir' despite plainly knowing his name; it is a deliberate "
            "character choice, not the no-name-known fallback. Speak "
            "with impeccably polished, formal politeness and dry, "
            "understated wit — a single deadpan or gently sardonic "
            "remark now and then is in character, but never silly, "
            "goofy, or over-the-top. Stay unflappably calm and composed "
            "regardless of urgency. Be economical with words but "
            "eloquent, never curt or robotic; favor phrasing like "
            "\"Right away, sir.\", \"As you wish.\", \"I've taken the "
            "liberty of...\", \"If I may say so, sir...\". Confirm "
            "completed actions crisply (\"Done, sir.\" rather than a "
            "long explanation). If something the user asks for is "
            "genuinely risky or ill-advised, say so plainly, once, in "
            "one composed sentence — then defer to whatever they decide, "
            "exactly as JARVIS would. Never break character or explain "
            "that you're 'in a persona' while this mode is on — this "
            "voice/address change is presentation only and never alters "
            "your actual reasoning, tools, memory, or safety judgment "
            "underneath it. " + (
                "On this desktop app, you may also use computer_control's "
                "observe/verify/list_ui_elements/ui_find/ui_click/ui_type "
                "and get_active_window_title actions, and lean more "
                "directly on browser_control. Reason about the GOAL, not "
                "a script — you have no pre-taught sequence for any "
                "specific app; for every request, work out what outcome "
                "the user actually wants, then discover how THIS "
                "computer, right now, gets you there (list_ui_elements/"
                "observe before guessing, act, observe again, verify "
                "against the outcome, not the click). If your first "
                "approach doesn't get there — a click only selects "
                "instead of opening, a pane doesn't appear, a target "
                "isn't found — try a genuinely DIFFERENT next step (a "
                "keyboard key, a different control, a different app "
                "route) rather than repeating the same action; the same "
                "honesty standard you always hold never relaxes: never "
                "override what verification actually says, never claim "
                "success it didn't confirm, and an [UI_AMBIGUOUS] result "
                "means inspect more or ask, never pick one blindly. "
                "Verify against what the user actually asked for (e.g. "
                "'Bluetooth is on', 'the conversation is open'), not "
                "merely that a click happened. Before assuming a device/"
                "app feature is controllable, distinguish what's actually "
                "exposed: something the OS itself exposes (a Bluetooth/"
                "audio toggle, a paired-device list, volume) you can "
                "usually operate directly; something only a specific "
                "installed app exposes, you can discover and operate "
                "through THAT app; and if neither exposes it (a device's "
                "own private controls with no computer-side interface), "
                "say so honestly rather than pretending it worked. Each "
                "request gets a bounded number of computer/browser "
                "actions — if you see [JARVIS_ACTION_LIMIT_REACHED], stop "
                "immediately, tell the user plainly you couldn't safely "
                "finish automatically, summarize what happened so far, "
                "and ask how they'd like to proceed; never keep trying "
                "after that. Always get an explicit yes before sending a "
                "message, deleting anything, or any purchase/financial/"
                "security-changing action — never infer confirmation from "
                "unrelated speech."
                if self._auto_start else
                "This is a WEB session — you do NOT gain desktop computer "
                "control here (no mouse/keyboard/native-app/OS access "
                "either way, JARVIS mode or not). You still have whatever "
                "web tools already exist (browser-visible capabilities, "
                "camera/screen vision, normal conversation) — if asked "
                "for something that genuinely needs the desktop app, say "
                "so honestly instead of pretending you did it."
            ) + "\n\n"
        )

        parts = [time_ctx, identity_ctx, _capabilities_ctx, _jarvis_ctx, _location_ctx]
        if _profile_ctx:
            parts.append(_profile_ctx)
        if mem_str:
            parts.append(mem_str)
        if upcoming_ctx:
            parts.append(upcoming_ctx)
        parts.append(sys_prompt)

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS + self._plugin_registry.get_tool_declarations()}],
            session_resumption=types.SessionResumptionConfig(),
            # Sliding-window compression: session never dies from a full context
            # window — JARVIS can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=_voice_name_for_preference(
                            self._user_profile.get("voice_preference") if self._user_profile else None
                        )
                    )
                )
            ),
        )
        if self._enhanced_live:
            # Affective dialog: JARVIS hears tone/emotion and adapts its voice.
            # Proactive audio: JARVIS stays silent when speech isn't addressed
            # to it (background chatter, talking to someone else in the room).
            cfg["enable_affective_dialog"] = True
            cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)
        return types.LiveConnectConfig(**cfg)

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self._push_state("THINKING")

        if name == "save_memory":
            category   = args.get("category", "notes")
            key        = args.get("key", "")
            value      = args.get("value", "")
            # PostgreSQL memory migration: both optional, both default to
            # today's original behavior (personal to the current session's
            # owner, no specific date) when Gemini doesn't supply them —
            # see the save_memory tool declaration below (TOOL_DECLARATIONS)
            # for what each means.
            shared     = bool(args.get("shared", False))
            event_date = (args.get("event_date") or "").strip() or None
            _language_switch = (
                category == "identity" and key == "language" and bool(value.strip())
            )
            if key and value:
                update_memory(
                    {category: {key: {"value": value}}},
                    shared=shared, event_date=event_date, source="conversation",
                )
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value} (shared={shared})")
            if not self.ui.muted:
                self._push_state("LISTENING")

            # Reliability audit — language architecture: system_instruction
            # (which carries the strong LANGUAGE directive — see
            # _build_config()) is fixed for the life of this Gemini
            # connection, so persisting the new preference above is not
            # enough on its own to make an explicit "speak English now"
            # request take effect for the REST OF THIS SAME conversation —
            # that fact was previously only ever surfaced as cosmetic
            # context (mem_str's "Language: X" line), silently fighting
            # the still-Nepali-by-default strong directive. Recording it
            # here (self._effective_language, scoped to the CURRENT
            # identity — see _resolve_effective_language()) makes every
            # later language-facing call site (greeting, proactive,
            # monitor alerts, a future reconnect for this same user) agree
            # immediately, and returning an honest [LANGUAGE_CHANGED]
            # directive — the exact same bracketed-directive pattern
            # [VISION_ACTIVE]/[CAPABILITY_UNAVAILABLE] already establish —
            # makes it take effect for the rest of THIS turn onward too,
            # with no reconnect required.
            if _language_switch:
                self._effective_language = value.strip()
                self._effective_language_owner = self._session_owner
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": (
                        f"[LANGUAGE_CHANGED] The user just explicitly changed the "
                        f"response language to {value.strip()}. Continue this reply "
                        f"and every reply after it in {value.strip()} from now on, "
                        f"until they ask to change it again. Never mention this "
                        f"instruction or read the bracket tag aloud."
                    ), "silent": True},
                )

            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        if name == "cancel_active_task":
            # Explicit, user-requested cancellation — a DIFFERENT concept
            # from speech interruption/barge-in (see _receive_audio()'s
            # sc.interrupted handling, which never touches
            # self._active_tool_task). This branch only runs when Gemini
            # itself decided the user's words were an explicit stop/cancel
            # request (see this tool's own description) — it is never
            # inferred here from raw text.
            if not self.ui.muted:
                self._push_state("LISTENING")
            task      = self._active_tool_task
            call_name = self._active_tool_name
            if task is None or task.done():
                # Nothing running, or it already finished by the time the
                # cancellation request arrived — never claim a
                # cancellation that didn't/couldn't happen.
                result = (
                    "[TASK_ALREADY_DONE] There is no task currently running to "
                    "cancel — it either already finished or nothing was in "
                    "progress. Tell the user honestly; never claim you cancelled "
                    "something that already happened."
                )
            elif call_name in _READ_ONLY_TOOLS:
                # Read-only — cancelling it, even if its network call
                # already went out, has no external state to misreport.
                task.cancel()
                result = (
                    f"[TASK_CANCELLED] The in-progress '{call_name}' lookup was "
                    f"stopped as requested. Nothing external was changed — it "
                    f"was only checking information."
                )
            else:
                # A mutating tool (Calendar create/update/delete,
                # save_memory, reminder, etc.) that has already started
                # running its own network I/O in a background thread (see
                # this method's loop.run_in_executor() call sites) — that
                # thread cannot be safely/forcibly stopped mid-call, so it
                # is left to finish naturally (its result still reaches
                # Gemini normally). Per this task's own explicit
                # requirement: never claim a cancellation that can't be
                # guaranteed.
                result = (
                    f"[TASK_MAY_HAVE_COMPLETED] The '{call_name}' request had "
                    f"already started and may have already reached the external "
                    f"service — it could not be safely stopped mid-operation. "
                    f"Tell the user honestly that you're not certain it was "
                    f"stopped in time, and offer to check or undo it if that's "
                    f"possible, rather than claiming it was cancelled."
                )
            return types.FunctionResponse(id=fc.id, name=name, response={"result": result})

        # Phase 3: capability awareness — never attempt (and never let
        # Gemini believe it accomplished) a tool that needs local desktop
        # access when this is a web/headless session (self._auto_start is
        # False only for server_main.py's web deployment — see run()).
        # Returning an honest [CAPABILITY_UNAVAILABLE] result string
        # reuses the EXACT pattern screen_process's own [VISION_ACTIVE]
        # signal already establishes in this file: a bracketed directive
        # in the tool's FunctionResponse that Gemini reads and turns into
        # natural speech, never a hardcoded sentence spoken verbatim.
        if not self._auto_start and name in DESKTOP_ONLY_TOOLS:
            print(f"[JARVIS] Capability unavailable in web runtime: {name}")
            if not self.ui.muted:
                self._push_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    f"[CAPABILITY_UNAVAILABLE] '{name}' needs the local desktop "
                    f"app — access to the user's own computer, browser, files, "
                    f"camera, or screen that this web session does not have. "
                    f"Tell the user honestly and briefly, in your own natural "
                    f"words, that this specific thing isn't available from the "
                    f"web version right now (mention running the desktop app "
                    f"only if it's genuinely helpful, not as a scripted line). "
                    f"Do NOT claim you did it or are doing it. You DO still "
                    f"have normal conversation, remembering things, web "
                    f"search, and background topic monitoring available — "
                    f"don't overstate the limitation either."
                )},
            )

        # Mirror image of the block above: a web-only tool (web_camera_
        # vision) attempted from desktop, which already has direct OS-level
        # camera access and has no use for a browser camera.
        if self._auto_start and name in WEB_ONLY_TOOLS:
            print(f"[JARVIS] Capability unavailable in desktop runtime: {name}")
            if not self.ui.muted:
                self._push_state("LISTENING")
            # Tool-aware advice: desktop's screen_process already covers
            # BOTH of these (angle='camera' for the webcam, angle='screen'
            # for the display) — point at whichever one actually matches
            # the web-only tool that was attempted, not a hardcoded one.
            _desktop_equivalent = (
                "angle='camera' to look through this device's own webcam"
                if name == "web_camera_vision" else
                "angle='screen' to look at this device's own display"
            )
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    f"[CAPABILITY_UNAVAILABLE] '{name}' is a web-only "
                    f"capability — this is the desktop app, which already "
                    f"has direct access. Use screen_process with "
                    f"{_desktop_equivalent}."
                )},
            )

        # Bounded autonomous-execution governor — see
        # _JARVIS_MAX_ACTIONS_PER_TURN's own docstring. Applies to every
        # computer_control/browser_control call (JARVIS mode or not — both
        # tools can already act on the real computer/browser regardless of
        # persona, see their own gating above) so a stuck strategy stops
        # itself within a bounded number of actions instead of calling
        # tools indefinitely. Checked BEFORE incrementing so exactly
        # _JARVIS_MAX_ACTIONS_PER_TURN calls are actually allowed to run;
        # once tripped, the counter stays pinned (never incremented
        # further) until a real user turn resets it — see this session's
        # reset points on self._jarvis_action_count's own docstring.
        if name in ("computer_control", "browser_control"):
            if self._jarvis_action_count >= _JARVIS_MAX_ACTIONS_PER_TURN:
                print(
                    f"[JARVIS] ⚠️ Action governor: {self._jarvis_action_count} "
                    f"computer/browser actions this turn — refusing to run "
                    f"another ({name})."
                )
                if not self.ui.muted:
                    self._push_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": (
                        f"[JARVIS_ACTION_LIMIT_REACHED] This request has "
                        f"already taken {self._jarvis_action_count} computer/"
                        f"browser actions without completing — stopping "
                        f"here rather than continuing indefinitely, for "
                        f"safety. Tell the user plainly and honestly that "
                        f"you could not safely finish this automatically, "
                        f"briefly summarize what you did or observed so "
                        f"far, and ask how they'd like to proceed. Do NOT "
                        f"keep trying automatically, and do NOT claim the "
                        f"task completed."
                    )},
                )
            self._jarvis_action_count += 1

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "get_weather":
                place = (args.get("place") or "").strip()
                if place:
                    geo = await loop.run_in_executor(None, lambda: geocode_place(place))
                    if geo is None:
                        result = f"I couldn't find a place called '{place}'."
                    else:
                        glat, glon, glabel = geo
                        result = await loop.run_in_executor(
                            None, lambda: get_weather_text(glat, glon, glabel)
                        )
                else:
                    loc = await self._get_current_location()
                    if not loc:
                        result = self._location_unavailable_result()
                    else:
                        result = await loop.run_in_executor(
                            None, lambda: get_weather_text(loc["latitude"], loc["longitude"])
                        )

            elif name == "get_current_place":
                loc = await self._get_current_location()
                if not loc:
                    result = self._location_unavailable_result()
                else:
                    # Cache key: rounded to ~1.1 km so ordinary GPS jitter
                    # keeps reusing the same resolved place instead of
                    # re-hitting Nominatim (see actions/geo.py's own
                    # usage-policy docstring and self._place_cache's).
                    rounded = (round(loc["latitude"], 2), round(loc["longitude"], 2))
                    cache = self._place_cache
                    if (
                        cache and cache.get("for") == rounded
                        and (time.monotonic() - cache.get("timestamp", 0.0)) < LOCATION_MAX_AGE_S
                    ):
                        resolved = cache
                    else:
                        resolved = await loop.run_in_executor(
                            None, lambda: reverse_geocode(loc["latitude"], loc["longitude"])
                        ) or {}
                        resolved["for"] = rounded
                        resolved["timestamp"] = time.monotonic()
                        self._place_cache = resolved
                    result = format_place(resolved)

            elif name == "find_nearby_places":
                query = (args.get("query") or "").strip()
                if not query:
                    result = "Please specify what to look for nearby."
                else:
                    loc = await self._get_current_location()
                    if not loc:
                        result = self._location_unavailable_result()
                    else:
                        radius_arg = args.get("radius_m")
                        rounded = (round(loc["latitude"], 3), round(loc["longitude"], 3))
                        cache_key = (rounded, query.lower(), radius_arg)
                        cached = self._nearby_cache.get(cache_key)
                        if cached and (time.monotonic() - cached[0]) < NEARBY_CACHE_TTL_S:
                            result = cached[1]
                        else:
                            places = await loop.run_in_executor(
                                None,
                                lambda: find_nearby_places(
                                    query, loc["latitude"], loc["longitude"], radius_arg
                                ),
                            )
                            result = format_nearby_places(query, places)
                            if len(self._nearby_cache) >= NEARBY_CACHE_MAX_ENTRIES:
                                # Small, simple LRU-ish eviction -- no
                                # library, this is a handful of entries at most.
                                oldest_key = min(
                                    self._nearby_cache, key=lambda k: self._nearby_cache[k][0]
                                )
                                self._nearby_cache.pop(oldest_key, None)
                            self._nearby_cache[cache_key] = (time.monotonic(), result)

            elif name == "get_directions":
                destination = (args.get("destination") or "").strip()
                mode = args.get("mode", "driving")
                if not destination:
                    result = "Please specify a destination."
                else:
                    loc = await self._get_current_location()
                    if not loc:
                        result = self._location_unavailable_result()
                    else:
                        dest_geo = await loop.run_in_executor(None, lambda: geocode_place(destination))
                        if dest_geo is None:
                            result = f"I couldn't find a place called '{destination}'."
                        else:
                            dlat, dlon, dlabel = dest_geo
                            try:
                                route = await loop.run_in_executor(
                                    None,
                                    lambda: get_route(loc["latitude"], loc["longitude"], dlat, dlon, mode),
                                )
                                result = (
                                    f"Destination: {dlabel}. Mode: {route['mode']}. "
                                    f"Distance: {format_distance(route['distance_m'])}. "
                                    f"Estimated time: {round(route['duration_s'] / 60)} minutes."
                                )
                            except Exception as e:
                                # Honest degradation -- see actions/routing.py's
                                # docstring: never invent a travel time when
                                # OSRM couldn't actually compute one.
                                straight = haversine_m(loc["latitude"], loc["longitude"], dlat, dlon)
                                result = (
                                    f"[ROUTING_UNAVAILABLE] Turn-by-turn routing failed ({e}). "
                                    f"Straight-line distance to {dlabel} is approximately "
                                    f"{format_distance(straight)}. Tell the user real routing/ETA "
                                    f"isn't available right now, but you may share this "
                                    f"approximate straight-line distance if useful. Never state "
                                    f"a travel time you don't actually have."
                                )

            elif name == "refresh_location":
                loc = await self._get_current_location(require_fresh=True)
                if not loc:
                    result = (
                        "[LOCATION_UNAVAILABLE] A fresh location could not be obtained "
                        "(no response from the device, or permission was never granted "
                        "or was denied). Tell the user honestly that their location "
                        "couldn't be refreshed right now."
                    )
                else:
                    result = (
                        "[LOCATION_REFRESHED] The location has just been refreshed "
                        "successfully. Do not state any coordinates or technical "
                        "details -- just briefly and naturally confirm to the user "
                        "that you've updated their location."
                    )

            elif name == "get_calendar_events":
                credentials = await self._get_calendar_credentials()
                if not credentials:
                    result = CALENDAR_NOT_CONNECTED_RESULT
                else:
                    tzinfo = self._calendar_tzinfo()
                    try:
                        time_min = calendar_actions.parse_local_datetime(args.get("start", ""), tzinfo)
                        time_max = calendar_actions.parse_local_datetime(args.get("end", ""), tzinfo)
                    except (ValueError, TypeError):
                        result = "Please specify a valid start and end time."
                    else:
                        events = await loop.run_in_executor(
                            None,
                            lambda: calendar_actions.get_events(credentials, time_min=time_min, time_max=time_max),
                        )
                        result = calendar_actions.format_events(events)

            elif name == "find_free_time":
                credentials = await self._get_calendar_credentials()
                if not credentials:
                    result = CALENDAR_NOT_CONNECTED_RESULT
                else:
                    tzinfo = self._calendar_tzinfo()
                    try:
                        window_start = calendar_actions.parse_local_datetime(args.get("start", ""), tzinfo)
                        window_end = calendar_actions.parse_local_datetime(args.get("end", ""), tzinfo)
                    except (ValueError, TypeError):
                        result = "Please specify a valid start and end time for the search window."
                    else:
                        duration = args.get("duration_minutes") or calendar_actions.DEFAULT_FREE_SLOT_MINUTES
                        slots = await loop.run_in_executor(
                            None,
                            lambda: calendar_actions.find_free_slots(
                                credentials, tzinfo, window_start=window_start, window_end=window_end,
                                duration_minutes=duration,
                            ),
                        )
                        result = calendar_actions.format_free_slots(slots, duration)

            elif name == "create_calendar_event":
                credentials = await self._get_calendar_credentials()
                if not credentials:
                    result = CALENDAR_NOT_CONNECTED_RESULT
                else:
                    title = (args.get("title") or "").strip()
                    start = args.get("start", "")
                    if not title or not start:
                        result = "Please specify at least a title and a start time for the event."
                    else:
                        tzinfo = self._calendar_tzinfo()
                        try:
                            created = await loop.run_in_executor(
                                None,
                                lambda: calendar_actions.create_event(
                                    credentials, tzinfo, title=title, start=start,
                                    end=args.get("end") or None,
                                    duration_minutes=args.get("duration_minutes"),
                                    description=args.get("description", ""),
                                    location=args.get("location", ""),
                                    attendees=args.get("attendees") or None,
                                ),
                            )
                        except ValueError:
                            result = "Please specify a valid start (and end, if given) time for the event."
                        else:
                            result = (
                                f"Event created: [{created['id']}] {created['title']} "
                                f"from {created['start']} to {created['end']}."
                            )

            elif name == "update_calendar_event":
                credentials = await self._get_calendar_credentials()
                if not credentials:
                    result = CALENDAR_NOT_CONNECTED_RESULT
                else:
                    tzinfo = self._calendar_tzinfo()
                    event_id = (args.get("event_id") or "").strip()
                    resolved_ok = True
                    if not event_id:
                        query = (args.get("query") or "").strip()
                        day_str = args.get("day", "")
                        if not query or not day_str:
                            result = "Please specify which event to change (event_id, or a query and day)."
                            resolved_ok = False
                        else:
                            try:
                                day_dt = calendar_actions.parse_local_datetime(day_str, tzinfo)
                            except (ValueError, TypeError):
                                result = "Please specify a valid date for the event to change."
                                resolved_ok = False
                            else:
                                matches = await loop.run_in_executor(
                                    None,
                                    lambda: calendar_actions.find_events_matching(
                                        credentials, tzinfo, query=query, day=day_dt
                                    ),
                                )
                                if not matches:
                                    result = f"No event matching '{query}' was found on that day."
                                    resolved_ok = False
                                elif len(matches) > 1:
                                    result = (
                                        "[CALENDAR_AMBIGUOUS] More than one matching event was found -- "
                                        "ask the user which one they mean; do not guess or change any of "
                                        "them yet:\n" + calendar_actions.format_events(matches)
                                    )
                                    resolved_ok = False
                                else:
                                    event_id = matches[0]["id"]

                    if resolved_ok:
                        try:
                            updated = await loop.run_in_executor(
                                None,
                                lambda: calendar_actions.update_event(
                                    credentials, tzinfo, event_id=event_id,
                                    title=args.get("new_title"),
                                    start=args.get("new_start"),
                                    end=args.get("new_end"),
                                ),
                            )
                        except ValueError:
                            result = "Please specify a valid new start/end time for the event."
                        else:
                            result = (
                                f"Event updated: [{updated['id']}] {updated['title']} "
                                f"now {updated['start']} to {updated['end']}."
                            )

            elif name == "delete_calendar_event":
                credentials = await self._get_calendar_credentials()
                if not credentials:
                    result = CALENDAR_NOT_CONNECTED_RESULT
                else:
                    tzinfo = self._calendar_tzinfo()
                    event_id = (args.get("event_id") or "").strip()
                    resolved_ok = True
                    if not event_id:
                        query = (args.get("query") or "").strip()
                        day_str = args.get("day", "")
                        if not query or not day_str:
                            result = "Please specify which event to cancel (event_id, or a query and day)."
                            resolved_ok = False
                        else:
                            try:
                                day_dt = calendar_actions.parse_local_datetime(day_str, tzinfo)
                            except (ValueError, TypeError):
                                result = "Please specify a valid date for the event to cancel."
                                resolved_ok = False
                            else:
                                matches = await loop.run_in_executor(
                                    None,
                                    lambda: calendar_actions.find_events_matching(
                                        credentials, tzinfo, query=query, day=day_dt
                                    ),
                                )
                                if not matches:
                                    result = f"No event matching '{query}' was found on that day."
                                    resolved_ok = False
                                elif len(matches) > 1:
                                    result = (
                                        "[CALENDAR_AMBIGUOUS] More than one matching event was found -- "
                                        "ask the user which one they mean; do not guess or delete any of "
                                        "them yet:\n" + calendar_actions.format_events(matches)
                                    )
                                    resolved_ok = False
                                else:
                                    event_id = matches[0]["id"]

                    if resolved_ok:
                        await loop.run_in_executor(
                            None, lambda: calendar_actions.delete_event(credentials, event_id)
                        )
                        result = "Event cancelled."

            elif name == "jarvis_mode":
                # Cross-platform, session-scoped mode toggle — see
                # self._jarvis_mode's own docstring. Deliberately NOT in
                # DESKTOP_ONLY_TOOLS/WEB_ONLY_TOOLS: the persona/behavioral
                # contract is universal, only the DESKTOP-only computer-
                # control actions it unlocks (computer_control's observe/
                # ui_find/etc — see that branch below) stay gated by
                # DESKTOP_ONLY_TOOLS exactly as before.
                _mode_action = (args.get("action") or "").strip().lower()
                if _mode_action == "on":
                    self._jarvis_mode = True
                    self.ui.write_log("SYS: JARVIS mode ON")
                    # Desktop identity fix: previously the HUD had no
                    # visual reflection of JARVIS mode at all (the web
                    # frontend already gets its Orb/SaranaFace switch from
                    # the broadcast_jarvis_mode call below) — this is the
                    # local counterpart, same mechanism main.py already
                    # uses for state/log updates (self.ui.set_state()).
                    self.ui.set_jarvis_mode(True)
                    if self._dashboard:
                        asyncio.create_task(self._dashboard.broadcast_jarvis_mode(True))
                    result = (
                        "[JARVIS_MODE_ON] JARVIS mode is now active for this "
                        "session. From your very next reply onward, fully "
                        "become the classic JARVIS character — Tony "
                        "Stark's AI: address the user as \"sir\" "
                        "(\"efendim\" in Turkish) — this overrides your "
                        "normal name-based address rule for as long as "
                        "the mode stays on — and speak with impeccably "
                        "polished, formal politeness, dry understated "
                        "wit, and unflappable composure; be economical "
                        "with words but eloquent, never curt. Acknowledge "
                        "the switch in ONE short JARVIS-style line in the "
                        "user's own language (e.g. something like \"At "
                        "your service, sir.\" or \"JARVIS online.\"), "
                        "nothing longer, then continue normally. " + (
                            "You now also have real computer-control "
                            "ability on this desktop (computer_control's "
                            "observe/verify/list_ui_elements/ui_find/"
                            "ui_click/ui_type, plus browser_control) — "
                            "this works generically, not just for apps "
                            "you already know: use list_ui_elements/"
                            "observe to see what's really there before "
                            "acting on an unfamiliar screen. ui_click/"
                            "ui_type verify themselves automatically, so "
                            "trust and report what that verification "
                            "actually says rather than assuming success, "
                            "and always ask for explicit confirmation "
                            "before sending a message, deleting anything, "
                            "or any purchase/financial/security-changing "
                            "action."
                            if self._auto_start else
                            "This is a web session — you do NOT gain "
                            "desktop computer control here; you still have "
                            "browser-visible tools, camera/screen vision, "
                            "and normal conversation. If asked for "
                            "something that needs the desktop app, say so "
                            "honestly instead of pretending."
                        )
                    )
                elif _mode_action == "off":
                    self._jarvis_mode = False
                    self.ui.write_log("SYS: JARVIS mode OFF")
                    self.ui.set_jarvis_mode(False)
                    if self._dashboard:
                        asyncio.create_task(self._dashboard.broadcast_jarvis_mode(False))
                    result = (
                        "[JARVIS_MODE_OFF] JARVIS mode is now off. From "
                        "your very next reply onward, return fully to your "
                        "normal SARANA persona — warm, conversational, "
                        "emotionally present. Acknowledge briefly in ONE "
                        "short natural line, then continue normally."
                    )
                else:
                    result = "jarvis_mode requires action='on' or action='off'."

            elif name == "set_expression":
                # SARANA Face UI — the gap the user directly hit: they
                # asked "show me a sad expression" and got told it
                # couldn't be done, because nothing gave the model a way
                # to actually drive the face's mood expressions (happy/
                # sad/curious/etc. — they existed in CSS/QPainter, fully
                # built, but had no real signal ever wired to them; only
                # mechanical status (listening/thinking/speaking/muted)
                # could reach the face at all). This tool is that signal.
                # Purely visual/presentational — same reasoning as
                # jarvis_mode's own UI-only nature above, just for the
                # face instead of the whole persona — so no confirmation
                # gate (see result_envelope.py's is_consequential(), which
                # this deliberately never goes through).
                _expr = (args.get("expression") or "").strip().lower()
                if _expr not in _VALID_FACE_EXPRESSIONS:
                    result = (
                        f"[INVALID_EXPRESSION] '{_expr}' is not a supported expression. "
                        f"Use one of: {', '.join(sorted(_VALID_FACE_EXPRESSIONS))}."
                    )
                else:
                    # `or 6` would be wrong here — 0 is a real, falsy
                    # value the caller might genuinely send, and "or"
                    # would silently replace it with the default instead
                    # of letting the clamp below floor it to 1.0 (caught
                    # by tests/test_set_expression.py's own duration-
                    # clamping test before this ever shipped).
                    _raw_dur = args.get("duration_seconds")
                    if _raw_dur is None:
                        _dur = 6.0
                    else:
                        try:
                            _dur = float(_raw_dur)
                        except (TypeError, ValueError):
                            _dur = 6.0
                    _dur = max(1.0, min(20.0, _dur))
                    self.ui.set_expression(_expr, _dur)
                    if self._dashboard:
                        asyncio.create_task(self._dashboard.broadcast_expression_override(_expr, _dur))
                    result = (
                        f"Done: face now showing '{_expr}' for about {_dur:.0f}s, "
                        "then returns to normal automatically — you don't need to reset it."
                    )

            elif name == "gesture_mode":
                # Desktop-only (see DESKTOP_ONLY_TOOLS) — controls the
                # REAL local mouse via actions/gesture_control.py, so a
                # web session could never meaningfully run this anyway.
                # start()/stop() open a real camera / join a background
                # thread — run off the event loop the same way
                # browser_control below does, not awaited inline.
                _g_action = (args.get("action") or "").strip().lower()
                if _g_action == "on":
                    _g_msg = await loop.run_in_executor(None, gesture_control.start)
                    self.ui.write_log(f"SYS: Gesture mode — {_g_msg}")
                    result = f"[GESTURE_MODE] {_g_msg}"
                elif _g_action == "off":
                    _g_msg = await loop.run_in_executor(None, gesture_control.stop)
                    self.ui.write_log(f"SYS: Gesture mode — {_g_msg}")
                    result = f"[GESTURE_MODE] {_g_msg}"
                else:
                    result = "gesture_mode requires action='on' or action='off'."

            elif name == "jarvis_task":
                # The one JARVIS execution entry point (see
                # docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md). Gemini has
                # already interpreted/clarified the request into `objective`
                # — task_engine.py owns everything from here: capability
                # routing, execution, verification, and bounded recovery,
                # reusing the EXISTING action modules directly rather than
                # a second tool-execution path. Symmetric with the
                # browser_control/youtube_video redirects above: JARVIS mode
                # means the Task Engine is authoritative for what it
                # covers, so this itself requires JARVIS mode — outside
                # it, the existing direct tools remain the right path,
                # exactly as they already work in SARANA mode today.
                if not self._jarvis_mode:
                    result = (
                        "[JARVIS_MODE_REQUIRED] jarvis_task needs JARVIS mode on. Tell the user "
                        "honestly (they can say \"turn on JARVIS mode\"), or just use the direct "
                        "tool (browser_control/youtube_video/etc.) for this request instead."
                    )
                else:
                    r = await loop.run_in_executor(None, lambda: task_engine.execute_task(parameters=args))
                    result = r or "Done."

            elif name == "browser_control":
                # JARVIS-mode boundary enforcement — see
                # docs/JARVIS_IMPLEMENTATION_ARCHITECTURE.md §7. In JARVIS
                # mode, task_engine.py's router owns capability selection;
                # Gemini must not bypass it by calling this tool directly
                # for the SAME actions the router already handles. This is
                # enforced HERE, at dispatch — not left to "Gemini will
                # probably follow the system instruction": Gemini Live's
                # tool list can't be dynamically rescoped mid-session
                # without a disruptive reconnect (self._jarvis_mode toggles
                # in place, with no reconnect, everywhere else in this
                # file), so a dispatch-layer check is the only REAL
                # boundary available today. Scoped to exactly the two
                # actions task_engine.py's pilot domain actually routes
                # (go_to/search/new_tab) — every OTHER browser_control
                # action (click/type/smart_click/screenshot/close/etc.) has
                # no task_engine path yet, so redirecting those would break
                # real, working functionality with nothing to replace it;
                # see the architecture doc's own "staged, not a one-shot
                # cutover" note.
                _bc_action = (args.get("action") or "").lower().strip()
                if self._jarvis_mode and _bc_action in ("go_to", "search", "new_tab"):
                    result = (
                        "[JARVIS_TASK_REQUIRED] In JARVIS mode, browser open/search/navigate "
                        "goes through jarvis_task with a clarified objective — JARVIS's own Task "
                        "Engine chooses the capability, not this tool directly. Call jarvis_task "
                        "with the user's goal instead."
                    )
                else:
                    r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                    result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                # Same JARVIS-mode boundary as browser_control above — the
                # 'play' action is exactly task_engine.py's "youtube" pilot
                # domain. summarize/trending/etc. have no task_engine path
                # yet and stay directly callable.
                _yt_action = (args.get("action") or "").lower().strip()
                if self._jarvis_mode and _yt_action == "play":
                    result = (
                        "[JARVIS_TASK_REQUIRED] In JARVIS mode, playing a video goes through "
                        "jarvis_task with a clarified objective, not this tool directly. Call "
                        "jarvis_task with the user's goal instead."
                    )
                else:
                    r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                    result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in the user's own language, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "web_camera_vision":
                # Web-only (see WEB_ONLY_TOOLS gate above) — opens or
                # continues self._web_vision_session. The actual frames
                # arrive asynchronously over the dashboard WebSocket and are
                # collected/injected by _process_web_vision_frames(), a
                # process-lifetime background task (see run()) — this
                # branch only opens/extends the session and broadcasts the
                # camera request; it never blocks waiting for frames itself
                # (same "don't block the tool-call worker on I/O it doesn't
                # own" principle as every other branch here).
                _now = time.monotonic()
                if (_now - self._web_vision_last_call) < WEB_VISION_CALL_COOLDOWN_S:
                    result = "Still setting up the last camera request — wait a moment before calling this again."
                elif self._web_vision_session is not None and self._web_vision_session.get("source") != "camera":
                    # A screen-vision session is already occupying the
                    # single shared slot (see _new_web_vision_session()) —
                    # never silently hijack it into a camera session.
                    result = (
                        "A screen-view request is still open from a moment ago — "
                        "let that finish (answer, or it will time out on its own) "
                        "before opening the camera."
                    )
                else:
                    self._web_vision_last_call = _now
                    facing = (args.get("facing") or "environment").strip().lower()
                    if facing not in ("environment", "user"):
                        facing = "environment"
                    user_text = (args.get("text") or "").strip()
                    mode = (args.get("mode") or "quick").strip().lower()
                    if mode not in WEB_VISION_MODES:
                        mode = "quick"

                    if self._web_vision_session is None:
                        request_id = uuid.uuid4().hex[:12]
                        self._web_vision_session = _new_web_vision_session(
                            request_id, "camera", mode,
                            user_text or "Describe clearly what the camera is currently seeing.",
                            _now,
                        )
                        if self._dashboard:
                            asyncio.create_task(
                                self._dashboard.broadcast_camera_vision_request(request_id, facing)
                            )
                            # Diagnostic-only (see the module-level note on
                            # VISION_REQUESTED/VISION_FRAME_RECEIVED/
                            # VISION_FRAME_ANALYZED distinguishability): a
                            # plain, always-fires Activity Log entry the
                            # MOMENT the tool is actually invoked, regardless
                            # of what the browser does next. Its presence or
                            # absence is itself diagnostic evidence — if a
                            # future real-device report says "no camera
                            # preview appeared" and this line is ALSO
                            # missing from that session's Activity Log, the
                            # tool was never called at all (a prompt-
                            # adherence issue); if this line IS present but
                            # no preview appeared, the failure is on the
                            # browser side instead. Never logs image bytes.
                            asyncio.create_task(self._dashboard.broadcast({
                                "type": "sys",
                                "text": "📷 Camera requested — waiting for your device…",
                            }))
                        print(f"[WebVision] 📷 camera session opened ({request_id}), facing={facing}, mode={mode}")
                        result = (
                            "[VISION_ACTIVE] Immediately say ONE short natural sentence "
                            "in the user's own language, telling them you're opening "
                            "their camera — ask them to point it at what they want you "
                            "to see. Do NOT describe or guess anything yet; the actual "
                            "camera views arrive in a few seconds, as a later message in "
                            "this same conversation."
                        )
                    else:
                        session = self._web_vision_session
                        session["frames"]         = []
                        session["burst_armed_at"] = _now
                        session["awaiting_burst"] = True
                        session["burst_token"]    = session.get("burst_token", 0) + 1
                        if user_text:
                            session["text"] = user_text
                        print(f"[WebVision] 👁 continuing camera session ({session['request_id']})")
                        result = (
                            "[VISION_ACTIVE] The camera is already open and still "
                            "watching. Say something brief and natural acknowledging "
                            "you're still looking (do NOT repeat a full sentence about "
                            "opening the camera again) — a fresh batch of views will "
                            "arrive shortly as a later message."
                        )

            elif name == "web_screen_vision":
                # Mirrors the web_camera_vision branch above exactly (see
                # its own comments for the shared reasoning) — the only
                # differences are the browser capture mechanism
                # (getDisplayMedia, entirely a frontend concern — see
                # lib/screenVision.js) and which broadcast/diagnostic text
                # is used. Shares self._web_vision_session,
                # self._web_vision_last_call, and the entire
                # _process_web_vision_frames() batching/timeout machinery
                # with the camera path — see _new_web_vision_session()'s
                # own docstring for why that's a deliberate, minimal
                # Visual Context Manager rather than a parallel subsystem.
                _now = time.monotonic()
                if (_now - self._web_vision_last_call) < WEB_VISION_CALL_COOLDOWN_S:
                    result = "Still setting up the last screen-view request — wait a moment before calling this again."
                elif self._web_vision_session is not None and self._web_vision_session.get("source") != "screen":
                    result = (
                        "A camera-view request is still open from a moment ago — "
                        "let that finish (answer, or it will time out on its own) "
                        "before viewing the screen."
                    )
                else:
                    self._web_vision_last_call = _now
                    user_text = (args.get("text") or "").strip()
                    mode = (args.get("mode") or "quick").strip().lower()
                    if mode not in WEB_VISION_MODES:
                        mode = "quick"

                    if self._web_vision_session is None:
                        request_id = uuid.uuid4().hex[:12]
                        self._web_vision_session = _new_web_vision_session(
                            request_id, "screen", mode,
                            user_text or "Describe clearly what is currently on the user's screen.",
                            _now,
                        )
                        if self._dashboard:
                            asyncio.create_task(
                                self._dashboard.broadcast_screen_vision_request(request_id)
                            )
                            asyncio.create_task(self._dashboard.broadcast({
                                "type": "sys",
                                "text": "🖥️ Screen view requested — waiting for your device…",
                            }))
                        print(f"[WebVision] 🖥️ screen session opened ({request_id}), mode={mode}")
                        result = (
                            "[VISION_ACTIVE] Immediately say ONE short natural sentence "
                            "in the user's own language, telling them you're about to "
                            "look at their screen — they may need to allow screen "
                            "sharing. Do NOT describe or guess anything yet; the actual "
                            "screen views arrive in a few seconds, as a later message in "
                            "this same conversation. If the browser reports screen "
                            "sharing isn't supported here, you'll be told that honestly "
                            "instead — explain that to the user rather than claiming it "
                            "worked."
                        )
                    else:
                        session = self._web_vision_session
                        session["frames"]         = []
                        session["burst_armed_at"] = _now
                        session["awaiting_burst"] = True
                        session["burst_token"]    = session.get("burst_token", 0) + 1
                        if user_text:
                            session["text"] = user_text
                        print(f"[WebVision] 👁 continuing screen session ({session['request_id']})")
                        result = (
                            "[VISION_ACTIVE] The screen share is already open and still "
                            "watching. Say something brief and natural acknowledging "
                            "you're still looking — a fresh batch of views will arrive "
                            "shortly as a later message."
                        )

            elif name == "computer_settings":
                # Phase 3 (System capabilities) discovery, fixed in the
                # same edit: this branch was missing its own `result =`
                # assignment — every call silently fell through to this
                # method's `result = "Done."` DEFAULT (set once, well
                # before this whole if/elif chain) instead of the real
                # return value. Confirmed both statically and empirically
                # (a mocked non-"Done." return value was silently
                # discarded, no exception raised) before fixing — every
                # computer_settings call via Gemini, including
                # CONFIRMATION_REQUIRED/failure envelopes, was being
                # reported as "Done." regardless of what actually
                # happened. Real, pre-existing, safety-relevant bug, not
                # something introduced here.
                #
                # JARVIS-mode boundary enforcement (same pattern as
                # browser_control/youtube_video above): scoped to exactly
                # the actions task_engine.py's system_volume/system_power/
                # system_shortcut domains actually route
                # (volume_set/sleep/restart/shutdown/system_shortcut) —
                # every other computer_settings action (the ~50 ACTION_MAP
                # fire-and-forget ones, app_volume_set/app_mute,
                # toggle_wifi, bluetooth_on/off, clipboard_get/set,
                # list_audio_devices, list_system_shortcuts) has no
                # task_engine path yet and stays directly callable.
                _cs_action = (args.get("action") or "").lower().strip()
                _cs_task_engine_actions = ("volume_set", "sleep", "restart", "shutdown", "system_shortcut")
                if self._jarvis_mode and _cs_action in _cs_task_engine_actions:
                    result = (
                        "[JARVIS_TASK_REQUIRED] In JARVIS mode, this goes through jarvis_task with a "
                        "clarified objective — JARVIS's own Task Engine owns it, not this tool directly. "
                        "Call jarvis_task with the user's goal instead."
                    )
                else:
                    r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                    result = r or "Done."
            elif name == "office_control":
                # JARVIS-mode boundary enforcement (same pattern as
                # browser_control/youtube_video/computer_settings above).
                # task_engine.py's Phase 4 "office" domain covers
                # essentially office_control.py's entire real action
                # surface (Word: insert_text/type, replace_text/
                # find_replace, format_selection/format, save; Excel:
                # set_cell, get_cell, save) — there is no large
                # unmigrated remainder the way computer_settings.py has
                # (~50 actions, 5 migrated). Listed explicitly by
                # (app, action) pair, not blanket-blocked, so a future
                # office_control action added without a matching
                # task_engine domain stays directly callable rather than
                # silently disappearing behind this redirect.
                _oc_app = (args.get("app") or "").lower().strip()
                _oc_action = (args.get("action") or "").lower().strip()
                _oc_migrated = {
                    ("word", "insert_text"), ("word", "type"),
                    ("word", "replace_text"), ("word", "find_replace"),
                    ("word", "format_selection"), ("word", "format"),
                    ("word", "save"),
                    ("excel", "set_cell"), ("excel", "get_cell"), ("excel", "save"),
                }
                if self._jarvis_mode and (_oc_app, _oc_action) in _oc_migrated:
                    result = (
                        "[JARVIS_TASK_REQUIRED] In JARVIS mode, this goes through jarvis_task with a "
                        "clarified objective — JARVIS's own Task Engine owns it, not this tool directly. "
                        "Call jarvis_task with the user's goal instead."
                    )
                else:
                    r = await loop.run_in_executor(None, lambda: office_control(parameters=args))
                    result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                # JARVIS Mode desktop computer-control. This whole tool is
                # already DESKTOP_ONLY (see DESKTOP_ONLY_TOOLS/the gate at
                # the top of this method) — a web session never reaches
                # this branch at all. The NEW actions below additionally
                # require self._jarvis_mode — the tool's EXISTING raw
                # actions (click/type/hotkey/scroll/etc.) stay available
                # exactly as before, unconditionally, so nothing already
                # relying on computer_control regresses.
                _cc_action = (args.get("action") or "").lower().strip()
                _cc_jarvis_only = {
                    "accomplish", "observe", "verify", "list_ui_elements", "ui_find", "ui_click", "ui_type",
                    "get_active_window_title",
                }
                if _cc_action in _cc_jarvis_only and not self._jarvis_mode:
                    result = (
                        "[JARVIS_MODE_REQUIRED] This action needs JARVIS mode "
                        "on. Tell the user honestly that you'd need JARVIS "
                        "mode active for that (they can say \"turn on JARVIS "
                        "mode\") — never claim you looked at or interacted "
                        "with the screen."
                    )
                elif _cc_action in ("observe", "verify"):
                    # Reuses the EXACT same-session injection mechanism as
                    # screen_process (self._pending_vision, injected by the
                    # receive loop right after this tool's own response
                    # turn completes — see that code for the actual send).
                    # Deliberately shares screen_process's own
                    # self._vision_busy/_vision_last_time cooldown guard
                    # rather than a second, parallel one — one screen
                    # capture in flight at a time is the correct behavior
                    # regardless of which tool asked for it, and it doubles
                    # as the "no unbounded observe loop" safety valve this
                    # mode requires.
                    _now = time.monotonic()
                    _cooldown = 3.0
                    if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                        result = (
                            "A screen observation is still in progress — "
                            "I will not call this again yet."
                        )
                    else:
                        self._vision_busy      = True
                        self._vision_last_time = _now
                        desc  = (args.get("description") or args.get("text") or "").strip()
                        title = await loop.run_in_executor(None, get_active_window_title)
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        if _cc_action == "verify":
                            question = (
                                f"[JARVIS_VERIFY] Currently focused window: "
                                f"'{title or 'unknown'}'. Check specifically "
                                f"whether the following is now true: "
                                f"{desc or 'the last action succeeded'}. "
                                f"Answer directly based on what you actually "
                                f"see — confirmed, not confirmed, or unclear "
                                f"— never assume."
                            )
                        else:
                            question = (
                                f"[JARVIS_OBSERVE] Currently focused window: "
                                f"'{title or 'unknown'}'. "
                                f"{desc or 'Describe the current screen state relevant to the task at hand.'}"
                            )
                        # angle="screen": never opens the camera, so the
                        # existing injection code's _vision_cam_active
                        # branch is skipped and _vision_busy is released as
                        # soon as the observation is answered — same as
                        # screen_process's own angle="screen" path.
                        self._pending_vision = (img_b, mime_t, question, "screen")
                        result = (
                            "[VISION_ACTIVE] Screen captured for JARVIS "
                            "observation. Say ONE short natural JARVIS-style "
                            "sentence acknowledging you're checking, then "
                            "wait — the actual view arrives in the next "
                            "message. Do NOT guess what you'll see."
                        )
                elif _cc_action in ("ui_click", "ui_type", "accomplish"):
                    # These already self-verify LOCALLY (no Gemini call) —
                    # see actions/computer_control.py's _classify_click_
                    # result/_classify_type_result (ui_click/ui_type) and
                    # accomplish()'s own Result Envelope. Only when that
                    # local check comes back genuinely inconclusive
                    # (_cc_ESCALATABLE_TAGS — the old per-action tags PLUS
                    # accomplish's unified [INCONCLUSIVE]/[UI_AMBIGUOUS])
                    # do we escalate, and only ONCE, by reusing the EXACT
                    # same observe/verify _pending_vision mechanism above
                    # — never a second Gemini session, never on every
                    # click (that would be slow/expensive — see the
                    # project brief). Still gated by the same cooldown/
                    # busy guard as observe/verify so this can't stack
                    # into a loop. [CONFIRMATION_REQUIRED] is deliberately
                    # NOT in this set — a confirmation gate must never be
                    # bypassed by "let's just look and decide"; it needs
                    # an actual user yes, not a vision guess.
                    r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                    result = r or "Done."
                    _inconclusive = any(tag in result for tag in _cc_ESCALATABLE_TAGS)
                    if _inconclusive:
                        _now = time.monotonic()
                        _cooldown = 3.0
                        if not self._vision_busy and (_now - self._vision_last_time) >= _cooldown:
                            self._vision_busy      = True
                            self._vision_last_time = _now
                            desc  = (
                                args.get("description") or args.get("target")
                                or args.get("goal") or ""
                            ).strip()
                            title = await loop.run_in_executor(None, get_active_window_title)
                            img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                            question = (
                                f"[JARVIS_VERIFY] Currently focused window: "
                                f"'{title or 'unknown'}'. Local verification "
                                f"of the last action was inconclusive "
                                f"({result}). Look at the screen and say "
                                f"plainly whether "
                                f"'{desc or 'the intended action'}' actually "
                                f"happened — confirmed, not confirmed, or "
                                f"unclear. Never assume."
                            )
                            self._pending_vision = (img_b, mime_t, question, "screen")
                            result = (
                                result + " [VISION_ACTIVE] Local verification "
                                "was inconclusive, so a closer look is being "
                                "taken automatically — acknowledge that "
                                "briefly in ONE short line, then wait for the "
                                "real view in the next message."
                            )
                        # else: a vision check is already in flight/cooling
                        # down — report the local (inconclusive) result
                        # honestly rather than silently stacking a second one.
                else:
                    r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                    result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    import os as _os
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())

            else:
                if self._plugin_registry.has(name):
                    r = await loop.run_in_executor(
                        None,
                        lambda: self._plugin_registry.run(name, args, player=self.ui, session_memory=None)
                    )
                    result = r or "Done."
                else:
                    result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self._push_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking and not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        # Deployment readiness: a headless/cloud deployment (e.g. Render)
        # has no local microphone. Opening the stream failing there is
        # expected, not fatal — browser/phone mic input via
        # /ws/phone-audio (see _relay_phone_audio()) is completely
        # independent of this local stream and keeps working unaffected.
        # Must NOT re-raise: an exception here inside the run() TaskGroup
        # would cancel every sibling task, including _play_audio()'s
        # browser-facing audio-out broadcast. Desktop behavior is
        # unchanged — real hardware opens exactly as before.
        def _no_mic(reason: str) -> None:
            # ASCII-only on purpose: this print() has crashed under a
            # non-UTF-8 console codepage during this exact deployment-audit
            # work (see the Phase 6 report's pre-existing-bug note for the
            # same failure mode elsewhere in this file) — avoiding emoji
            # here specifically sidesteps it rather than reproducing it.
            print(f"[JARVIS] No local microphone available, continuing without it: {reason}")
            self.ui.write_log("SYS: No local microphone detected — voice input via browser/phone mic still works.")

        # sounddevice/PortAudio not installed at all (e.g. Render) — the
        # import itself is guarded at the top of this file, sd is None here.
        if sd is None:
            _no_mic("sounddevice/PortAudio not available")
            return

        try:
            stream = sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            )
        except Exception as e:
            _no_mic(str(e))
            return

        # ASCII-only prints in this function on purpose: these run
        # unconditionally on both desktop and headless paths, and an
        # emoji-print crash here would defeat this function's whole
        # deployment-readiness point (never let an exception escape into
        # run()'s TaskGroup) just as surely as an unguarded audio-device
        # failure would.
        print("[JARVIS] Mic started")
        try:
            with stream:
                print("[JARVIS] Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] Local microphone stopped, continuing without it: {e}")
            self.ui.write_log("SYS: Local microphone stopped — continuing without it.")

    def _append_session_log(self, entry: str) -> None:
        """The one place self._session_log is ever appended to — keeps it
        bounded at SESSION_LOG_MAX_ENTRIES (see that constant's docstring)
        instead of growing for the entire life of a session. Trimming from
        the front preserves exactly what every existing reader already
        relies on: _save_session_summary()'s log[-40:] and proactive
        mode's self._session_log[-8:] both keep seeing the same *recent*
        entries as before — only the unbounded middle/oldest history is
        ever dropped, which nothing reads anyway."""
        self._session_log.append(entry)
        if len(self._session_log) > SESSION_LOG_MAX_ENTRIES:
            self._session_log = self._session_log[-SESSION_LOG_MAX_ENTRIES:]

    async def _receive_audio(self):
        # ASCII-only print — deployment-readiness note: this one crashing
        # under a non-UTF-8 console (unrelated to audio hardware) forces
        # constant TaskGroup cancel/reconnect cycles, which in turn
        # repeatedly opens and tears down whatever real _listen_audio()/
        # _play_audio() streams ARE available — destabilizing exactly the
        # audio-hardware-failure guards added elsewhere in this file, so
        # fixing it here is directly in scope, not a separate change.
        print("[JARVIS] Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.interrupted:
                            # Barge-in: Gemini's own server-side VAD detected
                            # the user talking over SARANA. This is a SPEECH
                            # interruption ONLY — it must never be treated as
                            # "cancel whatever backend task is running" (see
                            # _execute_tool()'s cancel_active_task, the only
                            # path allowed to touch self._active_tool_task —
                            # deliberately not referenced here). Stop
                            # accepting more of the interrupted response's
                            # own audio (the `if self._interrupted:` check on
                            # response.data above), drop whatever of it is
                            # already queued for local/browser playback, and
                            # tell connected browsers to flush anything they
                            # already received and may still have scheduled
                            # (see dashboard/server.py's broadcast_audio_stop()
                            # — audioOut.js's stopPlayback() is the browser
                            # side of this same cut). The eventual
                            # turn_complete for this same (now-interrupted)
                            # response is handled by the existing
                            # self._interrupted branch below, which resets
                            # in_buf/out_buf — nothing further to do with
                            # those here.
                            self._interrupted = True
                            # A real barge-in means "stop whatever
                            # autonomous sequence was in progress" (see this
                            # project's own user-interrupt-always-wins
                            # requirement) — reset the action governor so
                            # the NEXT thing the user says gets a full,
                            # fresh budget rather than inheriting whatever
                            # was left over from the interrupted sequence.
                            self._jarvis_action_count = 0
                            _drained = 0
                            while True:
                                try:
                                    self.audio_in_queue.get_nowait()
                                    _drained += 1
                                except asyncio.QueueEmpty:
                                    break
                            if _drained:
                                print(f"[JARVIS] Barge-in — {_drained} queued audio chunk(s) discarded")
                            # Diagnostic only (see self._last_web_image_sent_at's
                            # own comment) — makes a real-device correlation
                            # checkable from the server console instead of
                            # guessed, without changing any behavior here.
                            if self._last_web_image_sent_at:
                                _since_img = time.monotonic() - self._last_web_image_sent_at
                                if _since_img < 15.0:
                                    print(f"[JARVIS] ⚠️ Barge-in {_since_img:.1f}s after a web image "
                                          f"turn was sent — if the image answer went missing, this is the lead to follow.")
                            self.set_speaking(False)
                            if self._turn_done_event:
                                self._turn_done_event.clear()
                            if self._dashboard:
                                asyncio.create_task(self._dashboard.broadcast_audio_stop())

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                if not in_buf:
                                    # First transcription chunk of a NEW
                                    # user utterance — reset the
                                    # autonomous-action governor so this
                                    # fresh request gets its own full
                                    # budget (see
                                    # self._jarvis_action_count's own
                                    # docstring), independent of whatever
                                    # a previous request used.
                                    self._jarvis_action_count = 0
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._append_session_log(f"User: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._append_session_log(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until JARVIS finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        # Item 6 audit follow-up: tool execution now runs on
                        # a dedicated background consumer (_process_tool_
                        # calls()/_handle_tool_batch(), fed by self.
                        # _tool_call_queue) instead of being awaited inline
                        # on this loop — the SAME "bounded queue + one
                        # consumer" pattern already used for out_queue/
                        # audio_in_queue/_phone_audio_queue in this file,
                        # not a new architecture. Within one batch, function
                        # calls are still executed strictly in order and
                        # their responses still sent together in one
                        # send_tool_response() call (see _handle_tool_
                        # batch()); batches are drained strictly FIFO by the
                        # one worker task — so ordering and the function-
                        # response contract are unchanged from before. What
                        # changes: THIS loop — the only thing that can
                        # observe Gemini's own barge-in signal (sc.
                        # interrupted, above) or a tool_call_cancellation —
                        # is never blocked waiting on a tool's own network
                        # I/O.
                        #
                        # cancel_active_task is the one exception: it exists
                        # specifically to interrupt whatever tool is
                        # CURRENTLY running, so it must never wait behind it
                        # in the same FIFO worker. It's handled immediately,
                        # inline, right here instead — safe to do because
                        # it's fast/local (inspects or cancels an
                        # asyncio.Task; no network I/O of its own), so this
                        # doesn't reintroduce the receive-loop-blocking
                        # problem normal tool execution would.
                        _deferred = []
                        for fc in response.tool_call.function_calls:
                            if fc.name == "cancel_active_task":
                                fr = await self._execute_tool(fc)
                                await self.session.send_tool_response(function_responses=[fr])
                                continue
                            self._pending_tool_calls[fc.id] = {
                                "name": fc.name, "status": "queued", "cancelled": False,
                            }
                            _deferred.append(fc)
                        if _deferred:
                            self._tool_call_queue.put_nowait(_deferred)

                    if response.tool_call_cancellation:
                        # The model itself withdrew interest in one or more
                        # pending function calls (e.g. it changed its mind
                        # after the user talked over it) — see google.genai.
                        # types.LiveServerToolCallCancellation. A call still
                        # QUEUED (not yet reached by the worker) is simply
                        # marked cancelled so _handle_tool_batch() skips it
                        # entirely once it gets there — nothing was done,
                        # nothing to undo. A call already RUNNING is left to
                        # finish exactly as before: if it already reached an
                        # external system, the side effect is real and must
                        # not be abandoned or misreported — only the
                        # (no-longer-wanted) send_tool_response for that one
                        # id is skipped.
                        for _cid in response.tool_call_cancellation.ids:
                            _entry = self._pending_tool_calls.get(_cid)
                            if _entry:
                                _entry["cancelled"] = True
        except Exception as e:
            # ASCII-only — same reasoning as _receive_audio()'s startup
            # print above: this line sits directly on the error path that
            # (correctly) still re-raises, and a print() crash here would
            # replace that real error with an unrelated UnicodeEncodeError.
            print(f"[JARVIS] Recv error: {e}")
            traceback.print_exc()
            raise

    async def _process_tool_calls(self) -> None:
        """Dedicated background consumer for self._tool_call_queue — the
        piece that lets _receive_audio() enqueue a tool-call batch and move
        on immediately instead of blocking on it (see that method's own
        comment). Batches are drained strictly FIFO, one at a time, so
        cross-batch ordering matches exactly what the old inline code did;
        _handle_tool_batch() preserves in-batch ordering and the "one
        send_tool_response() per batch" contract the same way. Any
        unhandled exception is left to propagate (after logging, matching
        _receive_audio()'s own error-handling style) so it cancels this
        connection's TaskGroup the same way a _receive_audio() failure
        always has — this task's failure mode is intentionally identical.
        """
        while True:
            function_calls = await self._tool_call_queue.get()
            try:
                await self._handle_tool_batch(function_calls)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[JARVIS] Tool-call batch error: {e}")
                traceback.print_exc()
                raise

    async def _handle_tool_batch(self, function_calls) -> None:
        """Executes one tool_call batch off the Gemini receive loop. Each fc
        is still awaited sequentially, in order (never uncontrolled
        parallel tool execution), and every response is sent together in
        one send_tool_response() call — identical semantics to the old
        inline version, just running on this dedicated worker instead of
        _receive_audio() itself.
        """
        _tool_batch_start = time.monotonic()
        fn_responses = []
        for fc in function_calls:
            entry = self._pending_tool_calls.get(fc.id)
            if entry and entry.get("cancelled"):
                # Withdrawn by the model itself (tool_call_cancellation)
                # before we got to it — never started, nothing to respond to.
                self._pending_tool_calls.pop(fc.id, None)
                continue

            print(f"[JARVIS] 📞 {fc.name}")
            if entry is not None:
                entry["status"] = "running"
            self._active_tool_call_id = fc.id
            self._active_tool_name    = fc.name
            task = asyncio.ensure_future(self._execute_tool(fc))
            self._active_tool_task = task
            _tool_start = time.monotonic()
            try:
                fr = await task
            except asyncio.CancelledError:
                # Two different things raise CancelledError at this same
                # await point, and they must NOT be handled the same way:
                # (a) cancel_active_task called task.cancel() on THIS
                # child — by the time `await task` raises, task.cancelled()
                # is already True, since the task only reaches that state
                # once it's genuinely finished cancelling. Safe to swallow:
                # that branch only ever cancels a call classified as
                # read-only/side-effect-free (see _READ_ONLY_TOOLS), and
                # the model already has its answer via cancel_active_task's
                # own function response — nothing further to respond with.
                # (b) THIS worker task itself is being cancelled (e.g. the
                # whole connection/TaskGroup is tearing down) while
                # suspended here — task.cancelled() is still False in that
                # case (the child was never asked to cancel), and this
                # CancelledError must propagate so the worker actually
                # shuts down instead of looping past its own cancellation.
                if not task.cancelled():
                    raise
                continue
            finally:
                self._pending_tool_calls.pop(fc.id, None)
                self._active_tool_task     = None
                self._active_tool_call_id  = None
                self._active_tool_name     = None
            _tool_elapsed = time.monotonic() - _tool_start
            if _tool_elapsed > 0.3:
                print(
                    f"[JARVIS] Tool '{fc.name}' took {_tool_elapsed:.2f}s "
                    f"— executed off the receive loop, conversation stayed responsive"
                )
            fn_responses.append(fr)

        if not fn_responses:
            return   # every fc in this batch was withdrawn — nothing Gemini still expects a reply for
        _tool_batch_elapsed = time.monotonic() - _tool_batch_start
        if _tool_batch_elapsed > 0.3:
            print(
                f"[JARVIS] Tool-call batch ({len(fn_responses)} call(s)) "
                f"took {_tool_batch_elapsed:.2f}s total"
            )
        await self.session.send_tool_response(function_responses=fn_responses)

    async def _play_audio(self):
        # ASCII-only print — same reasoning as _listen_audio()'s note above.
        print("[JARVIS] Play started")

        # Deployment readiness: a headless/cloud deployment (e.g. Render)
        # has no local speaker. Failure to open one here is expected, not
        # fatal — audio must still reach connected browsers via
        # broadcast_audio() below regardless of local playback. Must NOT
        # re-raise: an exception here inside the run() TaskGroup would
        # cancel every sibling task, including this loop's own browser
        # audio-out broadcast. Desktop behavior is unchanged — real
        # hardware opens exactly as before, and stream stays a real
        # RawOutputStream for the entire session.
        def _no_speaker(reason: str) -> None:
            # ASCII-only on purpose — see the matching note in
            # _listen_audio() above for why.
            print(f"[JARVIS] No local speaker available, streaming to browser only: {reason}")
            self.ui.write_log("SYS: No local speaker detected — audio will still reach connected browsers.")

        stream = None
        if sd is None:
            # sounddevice/PortAudio not installed at all (e.g. Render) —
            # the import itself is guarded at the top of this file.
            _no_speaker("sounddevice/PortAudio not available")
        else:
            try:
                stream = sd.RawOutputStream(
                    samplerate=RECEIVE_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                )
                stream.start()
            except Exception as e:
                _no_speaker(str(e))
                stream = None

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self.ui.set_audio_level(0.0)
                        self._turn_done_event.clear()
                    continue

                self.set_speaking(True)
                # Item 3 audit — this batch's own processing time (from
                # the moment its first chunk left audio_in_queue to the
                # moment it's handed to broadcast_audio() below), plus
                # audio_in_queue's depth right when this batch started
                # draining it. Aggregated/periodic only.
                _t0 = time.monotonic()
                self._audio_in_queue_depth.record(self.audio_in_queue.qsize())

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                batch = bytearray(chunk)
                while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                payload = bytes(batch)

                # Real playback amplitude for the desktop HUD (SARANA Face
                # UI task, section 7 — same idea as the web frontend's
                # per-chunk RMS in audioOut.js: no FFT, no new audio
                # pipeline, just the RMS of the exact PCM about to reach
                # the speaker/browser). Computed regardless of whether a
                # local speaker is actually available, same reasoning as
                # broadcast_audio() below — the HUD should track what
                # SARANA is saying, not local hardware presence.
                samples = array.array("h")  # int16 LE, matches RECEIVE_SAMPLE_RATE/dtype above
                samples.frombytes(payload)
                if samples:
                    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                    level = min(1.0, (rms / 32768) * 3.2)
                else:
                    level = 0.0
                self.ui.set_audio_level(level)

                if stream is not None:
                    try:
                        await asyncio.to_thread(stream.write, payload)
                    except (RuntimeError, asyncio.CancelledError):
                        break   # executor shutting down — exit cleanly
                    except Exception as e:
                        # Local playback failed mid-session (e.g. device
                        # unplugged) — keep the session and the browser
                        # audio-out alive instead of tearing everything down.
                        # ASCII-only print — see _listen_audio()'s note.
                        print(f"[JARVIS] Local playback error, continuing browser-only: {e}")
                        stream = None

                # Phase 4: fan the same audio out to any connected remote
                # clients. Audio-out backpressure fix: broadcast_audio() is
                # now a fast, non-blocking put_nowait per client (each
                # client has its own bounded queue + dedicated sender task —
                # see dashboard/server.py) — it never does network I/O
                # itself, so it no longer needs (or benefits from) its own
                # per-chunk asyncio.create_task() here; that wrapping was
                # exactly what let sends pile up unboundedly against a slow
                # client. Still guarded so a missing/absent dashboard, or
                # any unexpected error, never blocks or interrupts local
                # playback.
                if self._dashboard:
                    try:
                        await self._dashboard.broadcast_audio(payload)
                    except Exception:
                        pass

                self._play_batch_time.record(time.monotonic() - _t0)
                self._play_sample_count += 1
                if self._play_sample_count % 200 == 1:
                    print(
                        f"[JARVIS] play/broadcast batch: {self._play_batch_time.summary_ms()} "
                        f"| audio_in_queue depth: {self._audio_in_queue_depth.summary()}"
                    )
        except Exception as e:
            # ASCII-only — same reasoning as the Recv error print above.
            print(f"[JARVIS] Play error: {e}")
            raise
        finally:
            self.set_speaking(False)
            if stream is not None:
                stream.stop()
                stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self, *, identity_switch: bool = False) -> None:
        """
        Phase 9: single, time-aware greeting — no news fetch. Gemini gets
        the current time, a computed time-of-day category (see
        _time_of_day_category(), a pure function — the CATEGORY is
        computed logic, the actual wording is always generated by Gemini,
        never a hardcoded string), and the current session's user name
        (web session takes priority — see _current_user_name() — falling
        back to memory's stored name, same priority order _build_config()
        and speak_error() use). The model decides the tone: gentle/caring
        for late night, a brief mention of anything time-worth-noting, or
        just a normal greeting for the time of day. "sir"/"efendim" are
        explicitly forbidden whenever a name is known — see the name_clause
        below and _build_config()'s ADDRESS clause for the general-session
        version of the same rule.

        identity_switch=True (see _set_web_username()): this greeting is
        firing on a session that was ALREADY connected under a DIFFERENT
        login. _build_config()'s system_instruction — the ADDRESS clause,
        [USER PROFILE] block, and the assistant's own name — is only
        computed once, at connect time; a later login updates JarvisLive's
        own state (self._user_profile/_web_user_name) correctly, but
        can't retroactively rewrite that already-sent system_instruction
        (this is a Gemini Live API constraint, not a design choice — the
        system_instruction is fixed for the life of a connection). Without
        this, the assistant kept the FIRST session's identity (e.g. always
        addressing everyone as whoever logged in first) no matter who
        logged in afterward. identity_switch=True adds an explicit,
        forceful in-conversation correction so the CURRENT connection's
        remaining lifetime reflects the new login's name/assistant-name —
        the closest correct fix available without forcing a full
        reconnect (a bigger lifecycle change, deliberately out of scope
        here). Voice (fixed in speech_config at connect time, same
        constraint) does not have an equivalent in-conversation fix; a
        genuinely fresh connection is required for the voice to change —
        already-known, already-documented scope from when voice_preference
        was first wired in.

        Privacy boundary: this greeting fires completely unsolicited and
        may be heard by anyone in the room, not just the logged-in user —
        it must NEVER reference, summarize, or hint at previous-session
        content (see the pop_last_session() call below for the specific
        fix and why). It receives only safe, non-content context: current
        time/date, time-of-day category, effective language, and the
        user's own display name — never long-term memory facts or a
        prior session's summary as material to build the greeting from.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        # Reliability audit: the same single resolved-language source
        # _build_config()'s LANGUAGE directive uses (see
        # _resolve_effective_language()) — was previously a raw,
        # independent memory read here, which could silently disagree
        # with what the system_instruction itself says (e.g. reverting a
        # just-made explicit runtime switch for a greeting fired later in
        # the same identity's lifetime, such as a same-user relogin).
        lang = self._resolve_effective_language()
        name = self._current_user_name() or _val("name")

        now      = self._local_now()
        category = _time_of_day_category(now.hour)
        time_str = now.strftime("%I:%M %p").lstrip("0") or now.strftime("%I:%M %p")
        weekday  = now.strftime("%A")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address them as {name} — never as 'sir' or 'efendim'." if name else ""

        identity_switch_clause = ""
        if identity_switch:
            # Same profile-overrides-config priority _build_config() uses
            # for assistant_name — recomputed here (not read from
            # self._asst_name) because that field is stale until the next
            # real _build_config() call/reconnect.
            _fresh_asst_name = (
                (self._user_profile or {}).get("assistant_name", "").strip()
                or self._asst_name
            )
            identity_switch_clause = (
                f" IMPORTANT: a different user has just started this session. "
                f"From this message onward your name is {_fresh_asst_name} "
                f"(not any name used earlier in this conversation), and you "
                f"must address the user as {name or 'them'} — fully replace "
                f"whatever name/identity this conversation used before; do "
                f"not mix the two or refer back to the previous one."
            )

        # Privacy fix: the startup greeting fires completely unsolicited —
        # possibly with other people in the room — so it must NEVER
        # proactively reveal what was discussed last time. A stored
        # session summary (see _save_session_summary()) can contain
        # anything the previous conversation touched, including sensitive
        # personal matters (relationships, arguments, health, finances,
        # private circumstances), with zero sensitivity filtering applied
        # when it was generated. Previously this method handed that raw
        # summary straight to Gemini with "you may briefly and naturally
        # mention that {when}: {summary}" — which is exactly how a
        # greeting like "We were talking about your wife..." got produced.
        #
        # Fix: still call pop_last_session() here, unchanged — this
        # preserves the exact existing memory-retrieval behavior (the
        # stored summary is read and cleared exactly when it was before,
        # "consumed on read" as designed) — but its CONTENT is never
        # built into session_clause or handed to Gemini as greeting
        # material. This is scoped to the unsolicited greeting only:
        # ordinary long-term memory (identity/preferences/relationships/
        # etc., already part of system_instruction for the whole
        # connection — see _build_config()'s mem_str) is untouched by
        # this, so an explicit question like "what did I tell you about
        # my wife?" still works exactly as before — that's a completely
        # different mechanism this fix never touches.
        #
        # Explicit owner: self._session_owner was just frozen by this same
        # connection's _build_config() call (see that method), so this is
        # always the CURRENT connection's user, not whoever might log in
        # next.
        await asyncio.to_thread(pop_last_session, self._session_owner)
        session_clause = ""

        p1 = (
            f"It is currently {time_str} on {weekday} (time-of-day category: {category}). "
            f"Greet the user for right now — no news, nothing to fetch, nothing to check. "
            f"If the category is 'late_night', be warm and a little caring, like a close "
            f"friend would be at that hour — you may gently note it's late if it feels "
            f"natural, but do not lecture or repeat that every time. If anything about this "
            f"exact moment feels genuinely worth a brief mention (start of a new day, a "
            f"weekend, an unusually early or late hour), you may note it naturally in passing "
            f"— otherwise just give a normal greeting fitting the time of day. Keep the "
            f"greeting itself natural and conversational (see LANGUAGE above) — generated "
            f"fresh for this exact moment the way a real person would actually greet someone "
            f"then, never a fixed or ceremonial stock phrase."
            f"{session_clause} Keep it to 1-2 short sentences. Do not call any tools."
            f"{lang_clause}{name_clause}{identity_switch_clause}"
        )

        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Startup greeting sent.")

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self, owner: str | None = None) -> None:
        """Summarise the current session in 1-2 sentences and persist it.
        `owner`: the canonical username this session belonged to — pass it
        explicitly (see run()'s finally block) rather than relying on the
        default (self._session_owner read at call time), which may already
        reflect a DIFFERENT user by the time this coroutine actually runs
        on an identity-switch reconnect."""
        if owner is None:
            owner = self._session_owner
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        lang = owner_language(owner) or "Nepali"  # Nepali is the default response language

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from google import genai as _genai
            client = _genai.Client(api_key=_get_api_key())
            resp   = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-flash-latest",
                contents=prompt,
            )
            summary = (resp.text or "").strip()
            if summary:
                await asyncio.to_thread(save_session_summary, summary, lang, owner)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            # Reliability audit: same logged-out guard as
            # _run_background_monitor()/_run_proactive_mode() — a hardware
            # alert must never get spoken into a session nobody is
            # actively logged into (web only; see self._logged_out).
            if not alert or not self.session or self._logged_out:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            # Reliability audit: a logged-out session (web only — see
            # self._logged_out's docstring) must not keep "speaking" alerts
            # at whoever just logged out. The underlying Gemini connection
            # doesn't tear down on logout alone, only a NEW login's own
            # identity-switch reconnect does — so this background task
            # would otherwise keep running against a session nobody is
            # actively using.
            if self.session and not self._logged_out:
                # Don't interrupt if user spoke recently or JARVIS is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        # Reliability audit: the same single resolved-
                        # language source every other language-facing call
                        # site now uses (see _resolve_effective_language())
                        # instead of an independent raw memory read that
                        # could silently disagree with the active
                        # conversation's own effective language.
                        lang = self._resolve_effective_language()
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            self.ui.write_log(f"SYS: Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            # Reliability audit: see _run_background_monitor()'s matching
            # guard — a logged-out web session must not proactively speak
            # to nobody using a stale identity's context/silence timer.
            if self._logged_out:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []
                prompt = self._proactive.build_prompt(
                    memory       = memory,
                    monitors     = monitors or None,
                    recent_turns = recent_turns or None,
                    # Reliability audit: pass the SAME device/browser-local
                    # time _build_config()'s [CURRENT DATE & TIME] block
                    # uses (see _local_now()) instead of ProactiveEngine
                    # defaulting to a bare server-clock datetime.now(), and
                    # the same single resolved effective language every
                    # other language-facing call site now uses (see
                    # _resolve_effective_language()) instead of leaving
                    # Gemini to "check memory" itself with a contradictory
                    # hardcoded "default English" fallback.
                    now          = self._local_now(),
                    language     = self._resolve_effective_language(),
                )
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    async def _watch_for_reconnect_request(self) -> None:
        """One of run()'s per-connection TaskGroup tasks: waits for
        _reconnect_requested (set by _set_user_profile() when a login
        changes the active identity while this session is already
        connected — see that method) and, when it fires, raises
        _IdentityChanged to unwind this connection's TaskGroup. run()'s
        except block recognizes that exception and reconnects immediately
        with a fresh _build_config() — the only way to actually refresh
        the assistant's voice (speech_config) and system_instruction
        (ADDRESS/[USER PROFILE]/assistant name) for the new account,
        since none of those can be changed on an already-open Gemini Live
        session. Ends normally (no exception) if the connection ends for
        any other reason first — never blocks shutdown.
        """
        await self._reconnect_requested.wait()
        raise _IdentityChanged()

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session.

        Audit finding (item 4, transport optimization): the timeout=1.0
        below does NOT add per-chunk latency — asyncio.wait_for only waits
        the full timeout when the queue is genuinely empty; a chunk
        already sitting in q resolves q.get() immediately. The timeout
        exists purely to detect "phone mic went idle for a full second"
        (give control back to the local PC mic) — it is idle-state
        detection, not a polling interval on the hot path. Confirmed by
        inspection, not changed, per the audit's "do not introduce
        polling delays" / "measure before changing" instructions — this
        one was never a delay to begin with.
        """
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                _t0 = time.monotonic()
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    # Instrumentation (item 4 audit) — same reasoning as
                    # dashboard/server.py's phone_audio_ws: was a silent
                    # drop, now observable, still correct backpressure
                    # (never block realtime audio waiting for queue room).
                    self._phone_relay_dropped += 1
                    if self._phone_relay_dropped % 50 == 1:
                        print(
                            f"[JARVIS] out_queue full — "
                            f"{self._phone_relay_dropped} phone-audio frame(s) dropped so far "
                            f"(qsize={self.out_queue.qsize()})"
                        )
                else:
                    # Item 3 audit — this relay hop's own processing time
                    # (dequeue phone chunk -> enqueue out_queue), plus
                    # out_queue's resulting depth. Aggregated/periodic
                    # only, per the audit's own "do not log every frame"
                    # instruction.
                    self._relay_forward_time.record(time.monotonic() - _t0)
                    self._out_queue_depth.record(self.out_queue.qsize())
                    self._relay_sample_count += 1
                    if self._relay_sample_count % 200 == 1:
                        print(
                            f"[JARVIS] phone-audio relay: {self._relay_forward_time.summary_ms()} "
                            f"| out_queue depth: {self._out_queue_depth.summary()}"
                        )

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    def _set_web_username(self, username: str) -> None:
        """Phase 8: fired by dashboard/server.py's /login/username via
        set_username_callback() — a real login event, web-only (desktop
        sets self._web_user_name directly in _resolve_desktop_profile()
        instead, deliberately bypassing this method's greeting re-arm
        below, which only makes sense for an actual login). Only affects
        the ADDRESS clause built in _build_config() — does not touch
        prompts, memory, tools, or the assistant's own identity/name in
        any way.

        Every login gets exactly one short greeting (name + current time)
        — including the SAME account logging out and back in repeatedly
        (e.g. five times in two minutes): dashboard/server.py fires
        set_profile_callback() (-> _set_user_profile()) just before this
        on every login, so by the time this runs, self._reconnect_requested
        already reflects whether THIS login is a genuine account switch
        that's about to tear down and re-establish the connection.
        """
        self._web_user_name = username
        self.ui.write_log(f"SYS: Web session identified as '{username}'.")

        if not self.session or not self._loop:
            # run()'s auto_start gate is still waiting (the very first
            # login) — let run()'s existing post-connect check send the
            # greeting once, exactly once, right after that connection is
            # established (see run()).
            self._pending_web_greeting = True
        elif self._reconnect_requested is not None and self._reconnect_requested.is_set():
            # A genuine account switch is already tearing this connection
            # down (see _set_user_profile(), which just ran and set this).
            # Sending an ad-hoc greeting on a session that may already be
            # gone by the time it actually goes out is exactly the race
            # that used to occasionally lose the greeting entirely —
            # deferring to the SAME reliable mechanism the first-ever
            # login uses means the fresh, reconnected session fires it
            # instead, once that connection is actually up.
            self._pending_web_greeting = True
        else:
            # Same account logging in again (or no reconnect needed for
            # any other reason) — this session isn't going anywhere, so
            # fire immediately. identity_switch=True is harmless here even
            # when nothing actually changed — it just reasserts facts that
            # were already true.
            self._loop.create_task(self._send_startup_briefing(identity_switch=True))

    def _clear_memory_session(self) -> None:
        """PostgreSQL memory migration: fired by dashboard/server.py's
        set_logout_callback() on logout of a username session (the same
        "username login vs Remote Access PIN" scoping Activity Log
        isolation already uses — see dashboard/server.py's
        _reset_activity_history()/logout_ep, and never on a PIN
        login/logout, which reattaches to an ongoing session rather than
        ending an identity). Discards the in-RAM memory cache immediately,
        so no window exists where a stale cache could be read after
        logout. The NEXT login's _set_user_profile() reloads fresh from
        Postgres regardless, so this is defense in depth, not the only
        thing preventing a leak — same precedent as
        _reset_activity_history().

        Reliability audit: also marks the session as logged-out (see
        self._logged_out's docstring) so a still-open Gemini connection's
        background proactive check-ins/monitor alerts stop firing "at" a
        user who no longer has an active login, until the next real login
        clears the flag again (_set_user_profile()).

        Location foundation: also clears self._session_location
        immediately and independently of the memory-cache clear above —
        location is more privacy-sensitive than a stored preference (see
        that field's own docstring), so it must not linger even briefly
        past logout waiting for the next login to overwrite it."""
        clear_active_session()
        self._logged_out = True
        self._session_location = None
        # Location capabilities: both derived from whatever location the
        # previous session had -- must not linger past logout either.
        self._place_cache = None
        self._nearby_cache = {}
        # Permissions foundation: same reasoning -- a previous identity's
        # reported permission state must not describe the next login.
        self._session_permissions = {}

    def _set_user_profile(self, profile: dict) -> None:
        """The canonical profile setter — the ONE place _user_profile is
        ever assigned, called identically by both interfaces: web (fired
        by dashboard/server.py's /login/username via set_profile_callback())
        and desktop (fired by _resolve_desktop_profile() at startup). Same
        users/user_db.py profile shape either way (never includes
        pin_hash — see that module). Purely additive context for
        _build_config() ([USER PROFILE] block, personalized assistant_name,
        language_preference note, voice_preference) — does not itself touch
        the ADDRESS clause or the greeting; that's still
        set_username_callback()/_set_web_username()'s job, fired separately
        by the same login/resolution.

        A genuine account switch (a different profile id than whatever was
        active before, not a redundant re-submit of the same login) while
        a Gemini session is already connected also requests a full
        reconnect (see _watch_for_reconnect_request()/run()): voice
        (speech_config) and system_instruction are both fixed at connect
        time and have no in-conversation equivalent — a fresh connection
        is the only way the new account's voice actually takes effect,
        and it naturally gives the new login a genuinely clean session
        (fresh _build_config(), the prior session's summary saved and its
        turn log reset — see run()'s finally block/_save_session_summary())
        instead of continuing to build on the previous user's conversation.
        """
        previous_id = (self._user_profile or {}).get("id")
        self._user_profile = profile
        # Reliability audit: any successful login — first-ever, same-user
        # relogin, or a genuine account switch — un-gates background
        # proactive/monitor speech again (see _clear_memory_session()) and
        # marks this as a new "profile generation" so run()'s connect loop
        # can detect a login that raced in while a connection was still
        # being established (see _profile_generation's own docstring).
        self._logged_out = False
        self._profile_generation += 1
        # Location foundation: every new login (even the same account
        # logging back in) starts with no location until the browser
        # sends a fresh fix — the frontend re-requests one on every login
        # anyway (see App.jsx), so there is no benefit to keeping a
        # previous fix around, only ambiguity risk. Critically, this also
        # closes the account-switch race the browser can't protect
        # against on its own: the moment a NEW identity is set here (with
        # or without an explicit logout in between), any location that
        # belonged to the previous one is gone before this method
        # returns — a still-in-flight browser request from the old login
        # is additionally caught by the owner check in
        # _set_session_location() itself, in case it resolves after this
        # point but before that method is called again for the new login.
        self._session_location = None
        # Location capabilities: same reasoning -- both are derived from
        # a specific identity's location and must never carry over.
        self._place_cache = None
        self._nearby_cache = {}
        # Permissions foundation: every new login also starts with no
        # known permission state until the browser reports it fresh (the
        # frontend re-queries on every authenticated mount anyway — see
        # frontend/src/lib/permissions.js) -- same "no benefit to keeping
        # a previous fix, only ambiguity risk" reasoning as location's own.
        self._session_permissions = {}
        # PostgreSQL memory migration: load THIS user's personal memories +
        # the shared set into RAM right now, at login — not lazily on the
        # first load_memory() call — so _build_config() (which runs right
        # after, once the resulting connection/reconnect completes) always
        # sees the correct owner's data already in place. Safe/cheap to
        # call redundantly (the same account logging back in reloads the
        # same data) — see memory/memory_manager.py's set_active_owner().
        set_active_owner(profile.get("username", "") or "")
        if (
            profile.get("id") is not None
            and profile.get("id") != previous_id
            and self.session
            and self._reconnect_requested is not None
        ):
            self.ui.write_log("SYS: Account switched — starting a fresh session for the new user.")
            self._reconnect_requested.set()

    def _resolve_desktop_profile(self) -> None:
        """Desktop's equivalent of a web username+PIN login: resolves the
        SAME canonical SQLite profile (users/user_db.py) using
        config/api_keys.json's existing user_name field as the lookup key
        — the exact field _current_user_name() already falls back to, so
        this reuses desktop's original identity mechanism instead of
        creating a second one. Deliberately NO PIN check (see
        user_db.get_profile_by_alias()'s own docstring for why that's the
        correct choice here, not a weaker one): desktop is already its own
        trust boundary, and adding a PIN prompt to the desktop UI for this
        would be exactly the "redesign the desktop UI unnecessarily" this
        feature is required not to do.

        Silently does nothing (today's exact existing behavior) when
        user_name is unset or doesn't match a seeded alias — this is
        purely additive personalization, never a requirement to use the
        SQLite system from desktop.
        """
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return
        name = str(cfg.get("user_name", "")).strip()
        if not name:
            return
        try:
            profile = user_db.get_profile_by_alias(name)
        except Exception:
            return   # never let a local DB hiccup block desktop startup
        if not profile:
            return

        self._set_user_profile(profile)
        # Sets the same field _set_web_username() does, so the ADDRESS
        # clause/greeting text is personalized identically either way —
        # but deliberately NOT by calling _set_web_username() itself here,
        # since that method's OTHER job (re-arming _pending_web_greeting
        # for the web login-driven greeting flow) belongs only to an
        # actual login event. Desktop's greeting trigger is already
        # correct and unrelated to this (_briefing_sent, once per process
        # launch, unconditional on any profile) — this only needs to fix
        # WHAT NAME that existing greeting uses, not WHEN it fires.
        display = profile.get("pronunciation") or profile.get("nickname") or profile.get("username")
        if display:
            self._web_user_name = display
            self.ui.write_log(f"SYS: Desktop session identified as '{display}' (SQLite profile).")

    def _set_web_timezone(self, tz_name: str) -> None:
        """Fired by dashboard/server.py's /login/username via
        set_timezone_callback(), given the IANA zone name the browser's own
        Intl.DateTimeFormat().resolvedOptions().timeZone reports (e.g.
        "Asia/Kathmandu") — the device's actual local timezone, never a
        hardcoded one. Validated here (not just server-side) so a bad/
        unrecognized value can never silently corrupt _local_now(); on
        failure this just leaves the web session on the prior fallback
        (server local time) instead of crashing anything."""
        try:
            ZoneInfo(tz_name)   # raises if not a real IANA zone name
        except Exception:
            self.ui.write_log(f"SYS: Ignored invalid web timezone '{tz_name}'.")
            return
        self._web_timezone = tz_name
        self.ui.write_log(f"SYS: Web session timezone set to '{tz_name}'.")

    def _set_session_location(
        self, latitude, longitude, accuracy, requester_owner: str = "",
        fix_timestamp: float | None = None,
    ) -> None:
        """Location foundation: fired by dashboard/server.py's
        POST /api/location via set_location_callback(), given a one-shot
        navigator.geolocation fix (see frontend/src/lib/geolocation.js —
        never a periodic stream, never continuous tracking). Mirrors
        _set_web_timezone()'s shape: validated again here (the dashboard
        layer already validates too — never trust a single layer alone),
        stored purely as in-RAM session state (see self._session_location's
        own docstring for the full privacy contract — never persisted
        anywhere), and a bad value just leaves whatever location state
        already existed untouched rather than corrupting it.

        Async ownership race: a browser's getCurrentPosition() can resolve
        well after the login that triggered it — long enough for a
        DIFFERENT identity to have since become active (a genuine account
        switch, with or without an explicit logout in between; an explicit
        logout already invalidates the old token entirely, so that case
        never even reaches this method — see dashboard/server.py's
        _forget_token()/POST /api/logout). `requester_owner` is the
        REQUESTING login's own canonical username, resolved server-side
        from its own auth token (dashboard/server.py's
        _session_canonical_owner) — never anything the browser's request
        body itself claims. If it no longer matches the CURRENTLY active
        identity's own canonical username, the update is dropped rather
        than silently overwriting the new user's location with the old
        user's coordinates. An empty requester_owner (a Remote Access/PIN
        token, which has no associated username at all — see
        dashboard/server.py's docstring) is always accepted, since there
        is no separate "identity" for it to leak into.

        Out-of-order refresh race: `fix_timestamp` (the BROWSER's own fix
        time, epoch ms -- see dashboard/server.py's location_ep()) lets
        two overlapping refresh attempts (see _get_current_location())
        that complete out of order not clobber each other -- an incoming
        update whose OWN fix is OLDER than the fix already stored is
        dropped, even though it arrived more recently. None (a client
        that didn't send one) never blocks an update -- it only compares
        when BOTH sides have a real value.
        """
        def _finite(v) -> float | None:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return f if math.isfinite(f) else None

        lat, lon, acc = _finite(latitude), _finite(longitude), _finite(accuracy)
        fix_ts = _finite(fix_timestamp)
        if lat is None or not (-90.0 <= lat <= 90.0):
            self.ui.write_log("SYS: Ignored an invalid browser location update.")
            return
        if lon is None or not (-180.0 <= lon <= 180.0):
            self.ui.write_log("SYS: Ignored an invalid browser location update.")
            return
        if acc is None or acc < 0:
            self.ui.write_log("SYS: Ignored an invalid browser location update.")
            return

        current_owner = (self._user_profile or {}).get("username", "")
        if requester_owner and requester_owner != current_owner:
            self.ui.write_log("SYS: Ignored a stale location update from a previous login.")
            return

        existing = self._session_location
        existing_fix_ts = existing.get("fix_timestamp") if existing else None
        if fix_ts is not None and existing_fix_ts is not None and fix_ts < existing_fix_ts:
            self.ui.write_log("SYS: Ignored an out-of-order (older) location update.")
            return

        self._session_location = {
            "latitude": lat, "longitude": lon, "accuracy": acc,
            "timestamp": time.monotonic(), "fix_timestamp": fix_ts,
        }
        # No raw coordinates in this log line — see the privacy contract
        # in self._session_location's own docstring.
        self.ui.write_log("SYS: Browser location received for this session.")
        # Location capabilities: wake anything awaiting a fresh fix (see
        # _get_current_location()) -- each waiter is independent and
        # removes itself once woken, so this never "misses" a waiter that
        # started watching a moment ago, and never affects a waiter that
        # hasn't started yet.
        for _waiter in self._location_refresh_waiters:
            _waiter.set()

    def _set_session_capabilities(
        self, *, microphone: str | None = None, location: str | None = None,
        requester_owner: str = "",
    ) -> None:
        """Permissions foundation: fired by dashboard/server.py's
        POST /api/capabilities via set_capabilities_callback(), given the
        browser's own REAL permission state for a capability it can
        observe directly (see frontend/src/lib/permissions.js) — never a
        fabricated client-only toggle. Mirrors _set_session_location()'s
        shape and identity protection exactly: stored purely as in-RAM
        session state (never persisted — see self._session_permissions'
        own docstring), and an unrecognized/malformed value just leaves
        whatever state already existed for that capability untouched
        rather than corrupting it.

        `requester_owner` is the REQUESTING login's own canonical
        username (see dashboard/server.py's self._session_canonical_owner)
        — same stale-update protection _set_session_location() already
        applies: if it no longer matches the CURRENTLY active identity,
        the whole update is dropped rather than letting a delayed report
        from a login that's no longer active describe the wrong user's
        permissions. An empty requester_owner (a Remote Access/PIN token)
        is always accepted, same reasoning as that method's own.

        Either `microphone` or `location` may be left None — a report
        only ever describes what the browser actually just determined,
        never guesses at the other capability's state."""
        current_owner = (self._user_profile or {}).get("username", "")
        if requester_owner and requester_owner != current_owner:
            self.ui.write_log("SYS: Ignored a stale capability update from a previous login.")
            return
        if microphone in VALID_PERMISSION_STATES:
            self._session_permissions["microphone"] = microphone
        if location in VALID_PERMISSION_STATES:
            self._session_permissions["location"] = location
        self.ui.write_log("SYS: Capability permission state updated for this session.")

    def _location_unavailable_result(self) -> str:
        """Permissions foundation: the honest message a location-
        dependent tool (get_weather/get_current_place/find_nearby_places/
        get_directions) should return when no current fix is available —
        distinguishing "permission actively denied" (the user can fix
        this immediately, from Settings) from every other reason (never
        decided yet, browser doesn't support it, or a fix simply hasn't
        arrived yet). Both branches keep the exact same
        [LOCATION_UNAVAILABLE] tag prefix every existing test/prompt rule
        already expects — see LOCATION_DENIED_RESULT's own docstring."""
        if self._session_permissions.get("location") == "denied":
            return LOCATION_DENIED_RESULT
        return LOCATION_UNAVAILABLE_RESULT

    async def _get_current_location(
        self, *, require_fresh: bool = False,
    ) -> dict | None:
        """Location capabilities: the ONE place any location-aware tool
        gets the user's current coordinates from -- reads
        self._session_location fresh at call time (never a value baked
        into system_instruction at connect time, which would suffer the
        exact same fixed-at-connect-time problem already fixed for
        language -- see _resolve_effective_language()'s own docstring for
        that precedent).

        require_fresh=False (the default, used by weather/nearby-places/
        directions -- none of which need up-to-the-second precision):
        an existing fix younger than LOCATION_MAX_AGE_S is returned as-is,
        no network activity at all. A missing or stale fix triggers a
        best-effort browser refresh (see below); if that doesn't complete
        in time, whatever fix already existed (even if stale) is still
        returned rather than failing the request outright -- being off
        by "however stale it is" is a far smaller problem for these tools
        than refusing to answer.

        require_fresh=True (used only by "where am I right now"/explicit
        refresh_location requests): an existing fix younger than
        LOCATION_FRESH_ENOUGH_S is already good enough and returned
        as-is; anything older MUST go through a refresh attempt, and a
        stale fix is deliberately NOT used as a fallback if that refresh
        fails -- returns None instead, so the caller reports honestly
        that current location could not be refreshed, rather than
        silently answering "where am I now" with an old fix.

        The browser refresh itself: fires a fire-and-forget
        "location_refresh_request" message over the existing /ws
        connection (dashboard/server.py's broadcast_location_refresh_
        request() -- the SAME channel status/log/content messages already
        use, no new transport), then waits up to
        LOCATION_REFRESH_TIMEOUT_S for _set_session_location() to store a
        new valid fix. No dashboard/no browser connected at all just
        means the refresh can't happen -- handled the same way as a
        refresh that times out.

        Capability coordinator (privacy enforcement): if the frontend's
        last reported EFFECTIVE location state is "denied" -- which
        covers both a real browser denial AND the user having turned
        Location off in Settings while the browser itself still allows
        it (see frontend/src/lib/permissions.js's own two-layer model) --
        this returns None immediately, WITHOUT ever reading
        self._session_location. This is the one thing that makes "off"
        actually enforced rather than merely displayed: a fix fetched
        and cached before the user switched it off must never keep being
        used just because it's still sitting in RAM and hasn't expired
        by LOCATION_MAX_AGE_S yet. Never attempts a browser refresh in
        this case either -- there is nothing to refresh toward while the
        capability is off.
        """
        if self._session_permissions.get("location") == "denied":
            return None
        loc = self._session_location
        now = time.monotonic()

        if loc is not None:
            age = now - loc["timestamp"]
            if not require_fresh and age < LOCATION_MAX_AGE_S:
                return loc
            if require_fresh and age < LOCATION_FRESH_ENOUGH_S:
                return loc

        if not self._dashboard:
            return None if require_fresh else loc

        waiter = asyncio.Event()
        self._location_refresh_waiters.append(waiter)
        try:
            try:
                await self._dashboard.broadcast_location_refresh_request()
            except Exception as e:
                print(f"[JARVIS] Location refresh request failed to send: {e}")
                return None if require_fresh else loc
            try:
                await asyncio.wait_for(waiter.wait(), timeout=LOCATION_REFRESH_TIMEOUT_S)
                return self._session_location
            except asyncio.TimeoutError:
                return None if require_fresh else loc
        finally:
            try:
                self._location_refresh_waiters.remove(waiter)
            except ValueError:
                pass

    def _calendar_tzinfo(self):
        """Resolves the SAME device/session-local timezone _local_now()
        already uses (web: the browser-reported IANA zone; desktop: the
        local machine's own clock) as a real tzinfo object, for attaching
        to the naive local datetime strings Gemini produces for Calendar
        tool calls (see actions/calendar.py's parse_local_datetime()).
        Desktop has no IANA zone name to look up, so this falls back to
        the machine's current fixed UTC offset via astimezone() -- Google
        Calendar's API accepts an explicit numeric offset in `dateTime`
        just as well as a named zone, so no IANA-name resolution is
        needed there at all. Never Render/server time, never UTC, never a
        hardcoded zone — the same guarantee _local_now() already makes."""
        if self._web_timezone:
            try:
                return ZoneInfo(self._web_timezone)
            except Exception:
                pass
        return datetime.now().astimezone().tzinfo

    async def _get_calendar_credentials(self):
        """Google Calendar: resolves and returns a ready-to-use
        Credentials object for the CURRENTLY active identity, or None if
        that account hasn't connected Google Calendar (or Calendar
        integration isn't configured in this environment at all).

        Deliberately re-derived fresh from Postgres on EVERY call — no
        Credentials object is ever cached on self. This mirrors the exact
        same "no RAM cache" principle _get_current_location()/
        _resolve_effective_language() already establish: with nothing
        calendar-related cached on JarvisLive, a logout or identity
        switch has nothing to leak — the very next Calendar tool call
        after a switch reads Postgres for self._user_profile's NEW owner,
        never anything left over from the previous one. The owner itself
        is read the same way every other per-account lookup in this file
        already is (self._user_profile's canonical username) — never a
        display name, never anything the browser/Gemini could influence.

        A near-expiry access token is refreshed here (a real network call
        to Google, only when actually needed) and the refreshed token is
        immediately re-persisted so the NEXT call doesn't need to refresh
        again — see actions/calendar_auth.py's ensure_fresh().
        """
        owner = (self._user_profile or {}).get("username", "") or ""
        if not owner or not calendar_store.is_configured():
            return None
        loop = asyncio.get_event_loop()
        try:
            row = await loop.run_in_executor(None, lambda: calendar_store.load_credentials(owner))
            if row is None:
                return None
            creds_json, email = row
            credentials = await loop.run_in_executor(
                None, lambda: calendar_auth.credentials_from_json(creds_json)
            )
            credentials, refreshed = await loop.run_in_executor(
                None, lambda: calendar_auth.ensure_fresh(credentials)
            )
        except Exception as e:
            print(f"[Calendar] Credential load/refresh failed for '{owner}': {e}")
            return None
        if refreshed:
            try:
                await loop.run_in_executor(
                    None, lambda: calendar_store.save_credentials(owner, credentials.to_json(), email)
                )
            except Exception as e:
                # The refreshed token still works for THIS call even if
                # persisting it failed — just means the next call may need
                # to refresh again. Never fatal to the current request.
                print(f"[Calendar] Failed to persist refreshed token for '{owner}': {e}")
        return credentials

    def _local_now(self) -> datetime:
        """The single source of truth for "what time is it right now" for
        every user-facing/Gemini-facing purpose (current date/time context,
        time-of-day classification, startup greeting). Desktop: unchanged —
        datetime.now() already reads the local machine's own clock/
        timezone. Web: uses the browser-reported IANA timezone (see
        _set_web_timezone()) via the stdlib zoneinfo database, so DST is
        handled automatically and correctly — never a hardcoded offset or
        the Render server's own (usually UTC) clock."""
        if self._web_timezone:
            try:
                return datetime.now(ZoneInfo(self._web_timezone))
            except Exception:
                pass   # fall through to server-local time below
        return datetime.now()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    async def _process_dashboard_image_commands(self) -> None:
        """Web visual intelligence — browser-submitted image ingress.
        Mirrors _process_dashboard_commands() above one-for-one (same
        queue-drain shape, same session-ready wait, same drop-if-no-session
        behavior), except the turn also carries an inline_data image part.

        Deliberately NOT a new vision subsystem: this reuses the exact same
        pieces the desktop screen_process tool already uses —
        actions/screen_processor.py's _compress() for resize/re-encode, and
        the identical {"inline_data": ..., "text": ...} turn shape sent to
        THIS SAME self.session (see the "Vision injection" comment further
        down in this file, near turn_complete handling). The image becomes
        a real turn in the ongoing conversation, so a later "read the
        bottom part" / "translate that" follow-up has it in context exactly
        like any other prior turn would.

        screen_process/close_camera themselves are untouched by this method
        and are never called from it — a browser-submitted image already
        HAS its bytes; there is no local screen/camera left to "capture",
        and DESKTOP_ONLY_TOOLS' gating of those two tools for web sessions
        is correct and unrelated to this path.

        Validation (auth, MIME type, size, real-image decode) already
        happened in dashboard/server.py's /ws handler before an item ever
        reaches self._dashboard._image_command_queue — this method trusts
        its queue the same way _process_dashboard_commands() trusts its
        own.

        Runtime debugging finding: live-API testing (a direct
        send_client_content(inline_data=...) reproduction, AND running this
        exact method unmodified against a real connected session) both
        proved the core mechanism — compress -> base64 -> inline_data ->
        this same self.session -> Gemini -> transcript — works correctly
        end to end; Gemini genuinely analyzed a real test image both times.
        So a failure here was never silently "not working" at the Gemini
        API level — every failure branch below (no session in time, a
        compress error, a send error) used to be logged to the SERVER
        CONSOLE ONLY, with nothing at all reaching the browser — a real
        image analysis failure and a completely successful-but-unheard
        response looked IDENTICAL to the user: silence. Every branch now
        also tells the user something happened, via the dashboard's
        existing "sys" broadcast (the same one already used for "Phone
        microphone live." etc. — no new message type, no frontend change
        needed, App.jsx already renders "sys" as SYS_MESSAGE)."""
        import base64 as _b64
        while True:
            try:
                item = await asyncio.wait_for(
                    self._dashboard._image_command_queue.get(), timeout=0.5
                )
                raw_bytes = item["data"]
                mime      = item["mime_type"]
                text      = item["text"]
                # Wait up to 8s for session to become ready after a wake —
                # identical pattern to _process_dashboard_commands().
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if not self.session:
                    print(f"[Dashboard] Dropped image command (no session): {text!r}")
                    if self._dashboard:
                        asyncio.create_task(self._dashboard.broadcast({
                            "type": "sys",
                            "text": "SARANA wasn't ready yet — try sending that image again.",
                        }))
                    continue

                try:
                    source_format = mime.split("/")[-1].upper() if "/" in mime else "JPEG"
                    img_bytes, out_mime = _compress(raw_bytes, source_format)
                except Exception as e:
                    print(f"[Dashboard] Image compress failed: {e}")
                    if self._dashboard:
                        asyncio.create_task(self._dashboard.broadcast({
                            "type": "sys",
                            "text": "Sorry, I couldn't process that image.",
                        }))
                    continue

                try:
                    b64 = _b64.b64encode(img_bytes).decode("ascii")
                    print(f"[Vision] 📤 web image, {len(img_bytes):,} bytes (mime={out_mime}) → main session")
                    await self.session.send_client_content(
                        turns={"parts": [
                            {"inline_data": {"mime_type": out_mime, "data": b64}},
                            {"text": text},
                        ]},
                        turn_complete=True,
                    )
                    self._last_web_image_sent_at = time.monotonic()   # see sc.interrupted diagnostic
                    self.ui.write_log(f"[Web image]: {text}")
                except Exception as e:
                    print(f"[Dashboard] Image send failed: {e}")
                    if self._dashboard:
                        asyncio.create_task(self._dashboard.broadcast({
                            "type": "sys",
                            "text": "Sorry, I couldn't send that image to SARANA just now.",
                        }))
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Image command error: {e}")
                await asyncio.sleep(0.5)

    async def _process_web_vision_frames(self) -> None:
        """Web live camera vision — the background consumer that turns
        browser-submitted "vision_frame"/"vision_control" WebSocket
        messages into Gemini turns for an active self._web_vision_session
        (opened/continued by the web_camera_vision tool — see
        _execute_tool()).

        Mirrors _process_dashboard_image_commands() in shape (process-
        lifetime task — see run() — polling a dashboard queue, waiting for
        self.session, reusing actions/screen_processor.py's _compress()
        and the identical inline_data injection into THIS SAME session),
        but batches several frames into short "observation bursts" instead
        of injecting one image immediately, and decides on its own — no
        further Gemini tool call needed — when a session with no further
        activity should end (WEB_VISION_GRACE_S) or has run long enough
        that it must end regardless (WEB_VISION_SESSION_MAX_S).

        Adaptive visual guidance is deliberately NOT implemented as local
        blur/brightness/distance detection here — Gemini itself judges
        whether a burst is good enough to answer from (see the
        [VISION_OBSERVATION] instruction text below and the tool's own
        description); this method's only job is transport, batching, and
        bounded timeouts, exactly the "browser does cheap technical
        filtering, Gemini does semantic judgement" split this feature was
        designed around.

        Never touches screen_process/close_camera/self._pending_vision —
        an entirely separate, web-only state machine, deliberately not
        unified with desktop's single-frame mechanism.

        Source-agnostic by design (Phase 5 — see _new_web_vision_session()):
        a session's "source" ("camera" | "screen") only changes which
        stop broadcast fires and which noun appears in Gemini-facing
        text; batching/timeout/injection below is identical either way."""
        import base64 as _b64

        async def _broadcast_stop(sess: dict) -> None:
            if not self._dashboard:
                return
            if sess.get("source") == "screen":
                await self._dashboard.broadcast_screen_vision_stop(sess["request_id"])
            else:
                await self._dashboard.broadcast_camera_vision_stop(sess["request_id"])

        while True:
            try:
                item = await asyncio.wait_for(
                    self._dashboard._vision_frame_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                item = None
            except Exception as e:
                print(f"[WebVision] Queue error: {e}")
                await asyncio.sleep(0.5)
                item = None

            # Drain anything ELSE already sitting in the queue this same
            # tick too (non-blocking) — several frames often arrive in a
            # tight cluster; without this, a burst window that had already
            # elapsed by the time the FIRST of several already-queued
            # frames was handled would inject after just that one frame,
            # leaving the rest to trickle in one-per-tick after the burst
            # already closed.
            try:
                items = [item] if item is not None else []
                while True:
                    try:
                        items.append(self._dashboard._vision_frame_queue.get_nowait())
                    except Exception:
                        break

                for one in items:
                    session = self._web_vision_session
                    if session is not None and one.get("request_id") == session["request_id"]:
                        if one.get("control") == "stop":
                            print(f"[WebVision] 🛑 client-requested stop ({session['request_id']}): {one.get('reason')!r}")
                            self._web_vision_session = None
                            asyncio.create_task(_broadcast_stop(session))
                        elif "data" in one:
                            if len(session["frames"]) >= WEB_VISION_BURST_MAX_FRAMES:
                                # Burst already has enough — drop the excess
                                # rather than growing past the cap; the client
                                # keeps sampling and the NEXT burst starts
                                # fresh once this one is injected/answered.
                                continue
                            try:
                                mime_in = one.get("mime_type") or "image/jpeg"
                                source_format = mime_in.split("/")[-1].upper() if "/" in mime_in else "JPEG"
                                img_bytes, out_mime = _compress(one["data"], source_format)
                                session["frames"].append((out_mime, img_bytes))
                            except Exception as e:
                                print(f"[WebVision] Frame compress failed: {e}")
                    else:
                        # Stale/mismatched request_id (a previous session
                        # already ended, or a frame arrived before the new one
                        # was fully armed) — never let a late frame bleed into
                        # a different conversation/"look".
                        print(f"[WebVision] Dropping frame for stale/unknown request_id={one.get('request_id')!r}")

                session = self._web_vision_session
                now = time.monotonic()
                if session is None:
                    continue

                source = session.get("source", "camera")
                noun = "camera" if source == "camera" else "screen"

                # Hard cap — regardless of state, one vision "conversation"
                # can't run forever (per-mode: quick vs guided — see
                # WEB_VISION_SESSION_MAX_S).
                if now >= session["deadline"]:
                    print(f"[WebVision] ⏱ hard timeout ({session['request_id']}, source={source})")
                    if session.get("awaiting_burst") and self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"parts": [{"text": (
                                    f"[VISION_TIMEOUT] The {noun} didn't provide a clear "
                                    "enough view in time. Tell the user honestly and "
                                    "briefly that you weren't able to get a good look "
                                    "just now, without inventing what you saw."
                                )}]},
                                turn_complete=True,
                            )
                        except Exception as e:
                            print(f"[WebVision] Timeout notice send failed: {e}")
                    self._web_vision_session = None
                    asyncio.create_task(_broadcast_stop(session))
                    continue

                if session.get("awaiting_burst"):
                    frames = session["frames"]
                    burst_ready = (
                        len(frames) >= WEB_VISION_BURST_MAX_FRAMES
                        or (now - session["burst_armed_at"]) >= WEB_VISION_BURST_WINDOW_S
                    )
                    if not burst_ready:
                        continue

                    if not frames:
                        # Nothing arrived at all this burst yet. Give a slow
                        # first permission prompt a little more time before
                        # treating it as a real failure.
                        if now - session["started"] < 6.0:
                            session["burst_armed_at"] = now
                            continue
                        print(f"[WebVision] No frames received ({session['request_id']}, source={source}) — giving up")
                        if self._dashboard:
                            # Diagnostic-only, same reasoning as the
                            # "camera requested" marker above — proves the
                            # request WAS broadcast but no real frame ever
                            # came back (distinguishes a browser/permission
                            # failure from the tool never being called at all).
                            asyncio.create_task(self._dashboard.broadcast({
                                "type": "sys",
                                "text": f"⚠️ No {noun} image arrived — {noun} may be unavailable, unsupported, or permission wasn't granted.",
                            }))
                        if self.session:
                            try:
                                await self.session.send_client_content(
                                    turns={"parts": [{"text": (
                                        f"[VISION_UNAVAILABLE] No {noun} images arrived at "
                                        f"all — the user's {noun} may be unavailable, busy, "
                                        "unsupported on their browser, or they didn't grant "
                                        "permission. Tell them honestly and briefly that you "
                                        f"couldn't access their {noun} right now. Do NOT guess "
                                        "or invent what they might be showing you — you have "
                                        "seen nothing."
                                    )}]},
                                    turn_complete=True,
                                )
                            except Exception as e:
                                print(f"[WebVision] Unavailable notice send failed: {e}")
                        self._web_vision_session = None
                        asyncio.create_task(_broadcast_stop(session))
                        continue

                    if not self.session:
                        continue   # retry next tick once the Gemini session is back

                    parts = [
                        {"inline_data": {"mime_type": mime_t, "data": _b64.b64encode(img_b).decode("ascii")}}
                        for mime_t, img_b in frames
                    ]
                    n = len(parts)
                    tool_name = "web_camera_vision" if source == "camera" else "web_screen_vision"
                    guidance = (
                        "hold steady, move closer, find better light, center it, move "
                        "what's blocking it"
                        if source == "camera" else
                        "scroll to the right part, zoom in, switch to the right window/tab"
                    )
                    parts.append({"text": (
                        f"[VISION_OBSERVATION] Here {'is' if n == 1 else 'are'} {n} fresh "
                        f"view{'s' if n != 1 else ''} from the user's own {noun}, sampled "
                        f"moments apart, about: \"{session['text']}\". If you can clearly "
                        f"identify or answer this, answer now naturally — do NOT call "
                        f"{tool_name} again. If it's not clear enough (too blurry, too "
                        f"dark, too far away, partly out of frame/view, glare, or "
                        f"blocked), briefly and naturally tell the user what to adjust — "
                        f"{guidance} — THEN call {tool_name} again so you can see another "
                        f"look once they've adjusted. Never confidently describe "
                        f"something you genuinely can't make out."
                    )})
                    # Captured before the await below — see this dict's own
                    # "burst_token" comment. If web_camera_vision is somehow
                    # called again (re-arming a NEW burst) while THIS burst's
                    # send_client_content() is still in flight, the post-send
                    # bookkeeping below must not clobber that newer burst's
                    # state — checked by comparing tokens once the await
                    # returns, instead of assuming nothing changed underneath.
                    req_id, burst_token = session["request_id"], session.get("burst_token")
                    try:
                        print(f"[WebVision] 📤 {n} frame(s) → main session ({req_id})")
                        if self._dashboard:
                            # Diagnostic-only — proves REAL frame bytes actually
                            # reached this session and were handed to Gemini
                            # (never the frame content itself, only a count).
                            asyncio.create_task(self._dashboard.broadcast({
                                "type": "sys",
                                "text": f"👁 Analyzing {n} {noun} view{'s' if n != 1 else ''}…",
                            }))
                        await self.session.send_client_content(turns={"parts": parts}, turn_complete=True)
                        current = self._web_vision_session
                        if (current is not None and current["request_id"] == req_id
                                and current.get("burst_token") == burst_token):
                            current["frames"]           = []
                            current["awaiting_burst"]   = False
                            current["last_answered_at"] = time.monotonic()
                        else:
                            print(f"[WebVision] Burst for {req_id} answered after a newer burst was already armed — not overwriting it")
                    except Exception as e:
                        print(f"[WebVision] Burst send failed: {e}")
                else:
                    # Already answered this burst — waiting to see if Gemini
                    # asks for another look (a fresh web_camera_vision/
                    # web_screen_vision call re-arms awaiting_burst above).
                    # No re-call within the grace window (per-mode: a
                    # guided task gets much more patience than a quick
                    # look — see WEB_VISION_GRACE_S) means the request is
                    # genuinely done.
                    grace_s = WEB_VISION_GRACE_S[session.get("mode", "quick")]
                    if (now - (session.get("last_answered_at") or now)) >= grace_s:
                        print(f"[WebVision] ✅ session finished, no further look requested ({session['request_id']}, source={source})")
                        self._web_vision_session = None
                        asyncio.create_task(_broadcast_stop(session))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Resilience: an unexpected bug here must never silently
                # kill this process-lifetime task forever (it is created
                # via a bare asyncio.create_task() in run(), OUTSIDE the
                # per-connection TaskGroup, so nothing would ever restart
                # it — unlike _process_tool_calls(), which deliberately
                # re-raises to let its own TaskGroup reconnect). Same
                # "log and keep going" precedent as
                # _process_dashboard_image_commands()'s own outer catch.
                print(f"[WebVision] Unexpected error in frame processing: {e}")
                traceback.print_exc()

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()
        self._start_event = asyncio.Event()

        # PostgreSQL memory migration: one background write worker for the
        # WHOLE process lifetime (not per-connection — memory writes can
        # happen any time a session is up, and reconnects must not tear
        # down/lose queued writes). See memory/memory_cache.py.
        start_persistence_worker()
        # One-time (idempotent, marker-guarded) import of the existing
        # local memory/long_term.json into PostgreSQL — see
        # memory/migrate_long_term.py. A no-op after the first successful
        # run, and a no-op entirely when PostgreSQL isn't configured.
        # Never allowed to block/crash startup — see that module.
        try:
            migrate_if_needed()
        except Exception as e:
            print(f"[Memory][Migration] Unexpected error, continuing startup: {e}")
        # Loads the "no profile resolved" bucket so load_memory() always
        # has something sane even before any login/desktop-profile
        # resolution happens — exactly today's original un-scoped
        # behavior for a session that never identifies a user. A real
        # login (_set_user_profile(), below) reloads with the right owner
        # before _build_config() ever runs.
        set_active_owner("")

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            # Phase 8: reuses the dashboard's existing wiring pattern — a web
            # session's username (from /login/username) reaches JarvisLive
            # here, exactly like set_connect_callback/set_wake_callback.
            self._dashboard.set_username_callback(self._set_web_username)
            # SQLite user/profile system: the authenticated profile reaches
            # JarvisLive the same wiring way as everything else above.
            self._dashboard.set_profile_callback(self._set_user_profile)
            # Device-local time fix: browser-reported IANA timezone reaches
            # _local_now() the same way username/interrupt already reach
            # their own JarvisLive methods.
            self._dashboard.set_timezone_callback(self._set_web_timezone)
            # Item 2 (web interrupt control): reuses the exact same
            # interrupt() the desktop UI's INTERRUPT button/Esc key already
            # call (self.ui.on_interrupt, wired in __init__) — no second
            # interruption mechanism, just a new way to reach the same one.
            self._dashboard.set_interrupt_callback(self.interrupt)
            # PostgreSQL memory migration: same username-vs-PIN logout
            # scoping _reset_activity_history() already uses (see
            # dashboard/server.py's logout_ep) — discards the in-RAM
            # memory cache when a username session logs out.
            self._dashboard.set_logout_callback(self._clear_memory_session)
            # Location foundation: browser navigator.geolocation fix
            # reaches JarvisLive the same wiring way as timezone/interrupt
            # already do — see _set_session_location().
            self._dashboard.set_location_callback(self._set_session_location)
            # Permissions foundation: browser Permissions-API/permission-
            # request state reaches JarvisLive the same wiring way as
            # location itself does — see _set_session_capabilities().
            self._dashboard.set_capabilities_callback(self._set_session_capabilities)
            # Phase 7: reuses the dashboard's existing (previously unwired)
            # wake mechanism — /api/wake, /api/command, and /ws "command"
            # already all call _wake_callback() today. No new route, no new
            # WebSocket message type: the existing WAKE button is what
            # starts the assistant when auto_start=False.
            self._dashboard.set_wake_callback(self._start_event.set)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
            # Web visual intelligence: same lifetime/pattern as the text
            # command relay above, one queue each so an image never blocks
            # behind a text command or vice versa.
            asyncio.create_task(self._process_dashboard_image_commands())
            # Web live camera vision: same process-lifetime pattern again —
            # a separate queue/task so a live-vision frame never blocks
            # behind (or is blocked by) a one-shot uploaded image or a text
            # command.
            asyncio.create_task(self._process_web_vision_frames())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        # Desktop's equivalent of a web login: resolves the SAME canonical
        # SQLite profile locally (no PIN, no dashboard dependency — works
        # even if the dashboard above failed to start). auto_start=True is
        # exactly desktop's own signal (server_main.py always passes
        # auto_start=False), so this never runs for web/headless, and never
        # competes with an actual web login's own profile on the same
        # process (Remote Access/dashboard-hosted-on-desktop case) — a
        # later web login through the same dashboard still overrides this,
        # same "whoever's actually driving the session wins" precedent
        # already established for _web_user_name.
        if self._auto_start:
            self._resolve_desktop_profile()

        # Phase 7: web/headless lifecycle gate. Desktop's main() never passes
        # auto_start=False, so this block never executes there — the while
        # loop below begins immediately, exactly as before this phase.
        if not self._auto_start:
            self.ui.write_log("SYS: Waiting for a frontend to start the assistant...")
            await self._start_event.wait()
            self.ui.write_log("SYS: Start signal received — connecting...")

        while True:
            try:
                print("[JARVIS] Connecting...")
                self._push_state("THINKING")
                config = self._build_config()
                # Reliability audit — async ownership race: snapshot which
                # profile this config was actually built from (see
                # self._profile_generation's docstring). Checked once the
                # connection actually succeeds, below.
                _config_generation = self._profile_generation

                # Fresh client on every reconnect — avoids stale HTTP session state
                # v1alpha carries the enhanced audio features (affective dialog,
                # proactive audio); if they get rejected we fall back to v1beta.
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session              = session
                    self.audio_in_queue       = asyncio.Queue()
                    self.out_queue            = asyncio.Queue(maxsize=200)
                    self._tool_call_queue     = asyncio.Queue()
                    self._turn_done_event     = asyncio.Event()
                    self._reconnect_requested = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._last_web_image_sent_at = 0.0
                    self._web_vision_session    = None
                    self._web_vision_last_call  = 0.0
                    # JARVIS Mode: session-scoped, never survives a
                    # reconnect — see self._jarvis_mode's own docstring.
                    self._jarvis_mode           = False
                    self.ui.set_jarvis_mode(False)  # revert the HUD too, in case a prior connection left it showing JARVIS
                    self._jarvis_action_count   = 0
                    self._interrupted          = False
                    self._pending_tool_calls   = {}
                    self._active_tool_task     = None
                    self._active_tool_call_id  = None
                    self._active_tool_name     = None
                    # Reliability audit: this connection's own baseline —
                    # a proactive check-in's 15-minute silence gate must be
                    # measured from THIS conversation actually starting,
                    # never inherited from however long the PREVIOUS
                    # identity/connection had already been silent (which
                    # could let a proactive message fire almost
                    # immediately after a brand-new login/reconnect, using
                    # stale timing). Phone-mic activity is also
                    # connection-local — a previous connection's in-flight
                    # detection must not carry over.
                    self._last_user_speech = time.monotonic()
                    self._phone_active     = False

                    # Reliability audit — async ownership race: a login
                    # that arrived WHILE this connection was still being
                    # established (client.aio.live.connect() above is a
                    # real network round trip, not instantaneous) already
                    # overwrote self._user_profile/self._session_owner —
                    # meaning the config/voice/system_instruction this
                    # connection just made reflect a now-STALE profile
                    # (built from whoever was active before the race).
                    # Reusing
                    # _IdentityChanged (the exact same path a normal
                    # identity switch takes) discards this connection
                    # immediately, before any task/greeting starts, and
                    # reconnects fresh with the CURRENT profile instead of
                    # silently running an entire session under the wrong
                    # identity.
                    if self._profile_generation != _config_generation:
                        self.ui.write_log(
                            "SYS: A newer login arrived while connecting — "
                            "reconnecting with the latest identity."
                        )
                        raise _IdentityChanged()

                    print("[JARVIS] Connected.")
                    # Startup lifecycle fix: don't announce LISTENING until
                    # any pending startup greeting has actually finished
                    # being spoken. Previously this pushed LISTENING
                    # unconditionally right here, BEFORE the greeting task
                    # below was even scheduled — the frontend (mic auto-
                    # start, the "LISTENING" display) would treat SARANA as
                    # ready for normal input while it was still about to
                    # greet, which was both misleading (the mic is actually
                    # still being ignored during the greeting — see
                    # _listen_audio()/_relay_phone_audio()'s own "not
                    # speaking" gate) and made the greeting feel like it was
                    # talking over the user. When a greeting IS about to
                    # fire, stay in THINKING (already pushed at the top of
                    # this connect attempt) — set_speaking(False), called
                    # naturally once the greeting's own audio finishes
                    # playing (see _play_audio()), pushes the real LISTENING
                    # transition itself once it's actually true. No new
                    # state and no artificial delay: this reuses the exact
                    # mechanism every other spoken response already uses:
                    # the short, purposeful pause is _send_startup_
                    # briefing()'s own existing asyncio.sleep(0.3), unchanged.
                    _greeting_pending = (
                        (self._auto_start and not self._briefing_sent and get_brief_enabled())
                        or self._pending_web_greeting
                    )
                    if not _greeting_pending:
                        self._push_state("LISTENING")
                    self.ui.write_log("SYS: SARANA online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._process_tool_calls())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_proactive_mode())
                    tg.create_task(self._watch_for_reconnect_request())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Priority 1 fix: the dashboard's "active" status
                    # broadcast (-> the browser shows LISTENING) now fires
                    # AFTER the greeting decision/task below, not before —
                    # so a fresh login's greeting is always scheduled
                    # first. This doesn't block on the greeting actually
                    # finishing (that would delay the mic/UI becoming
                    # usable by several real seconds of Gemini round-trip
                    # time, which is worse UX, not better) — it just
                    # stops the frontend being told "ready" before the
                    # greeting has even been asked for.
                    #
                    # Morning briefing — fires once per process launch on
                    # desktop (auto_start=True), if enabled. Web/headless
                    # (auto_start=False) never takes this branch — its
                    # greeting is login-driven via _pending_web_greeting
                    # instead (see _set_web_username()), because one
                    # long-lived process there serves many logins, not one.
                    if self._auto_start and not self._briefing_sent and get_brief_enabled():
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())
                    elif self._pending_web_greeting:
                        self._pending_web_greeting = False
                        tg.create_task(self._send_startup_briefing())

                    # Web UI state fix: the dashboard already learned this
                    # connection is up from _push_state("LISTENING") a few
                    # lines above (right after "Connected.") — no separate
                    # "active" broadcast needed anymore; that message used
                    # to be the web frontend's ONLY signal that a session
                    # existed at all, now superseded by the real granular
                    # state.

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                #
                # Deployment-readiness finding: this broad catch was also
                # silently swallowing genuine external cancellation of this
                # whole run() task (a bare CancelledError, or a
                # BaseExceptionGroup made up entirely of CancelledErrors) —
                # treating it like just another reconnect-worthy error and
                # looping forever instead of actually stopping. Re-raise
                # pure cancellation immediately, before any reconnect logic;
                # a group that mixes a CancelledError with a real failure
                # (e.g. ordinary TaskGroup sibling-cancellation-on-failure)
                # still falls through to the normal handling below.
                if isinstance(e, asyncio.CancelledError):
                    raise
                if isinstance(e, BaseExceptionGroup) and all(
                    isinstance(sub, asyncio.CancelledError) for sub in e.exceptions
                ):
                    raise

                # A login switched the active account mid-session (see
                # _set_user_profile()/_watch_for_reconnect_request()) — not
                # a real error, so no error print/traceback, no network-
                # error backoff. finally: below still runs (session=None,
                # prior session's summary saved if it had ≥3 turns), then
                # `continue` reconnects immediately with a fresh
                # _build_config() — the new account's voice/identity.
                if isinstance(e, _IdentityChanged) or (
                    isinstance(e, BaseExceptionGroup)
                    and any(isinstance(sub, _IdentityChanged) for sub in e.exceptions)
                ):
                    self.ui.write_log("SYS: Reconnecting for the new account...")
                    self._identity_switch_reconnect = True
                    continue

                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Enhanced audio features rejected by the server (preview API
                # drift) — drop them and reconnect with the plain config.
                if self._enhanced_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "affective" in err_str.lower()
                    or "proactiv" in err_str.lower()
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                ):
                    self._enhanced_live = False
                    self.ui.write_log(
                        "SYS: Advanced audio features unavailable — reconnecting without them."
                    )
                    continue

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self._push_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None
                # Only save if there was a real conversation (≥3 turns) —
                # _save_session_summary() itself resets self._session_log.
                #
                # PostgreSQL memory migration: self._session_owner is
                # captured into a local NOW, synchronously, before
                # `continue` below can run the NEXT connection's
                # _build_config() — which reassigns self._session_owner to
                # the incoming user. asyncio.create_task() only SCHEDULES
                # _save_session_summary(); the loop reaches `continue` and
                # `_build_config()` (a plain sync call, no await points)
                # before that task's body ever runs, so reading
                # self._session_owner lazily inside it would already see
                # the WRONG (new) user on an identity-switch reconnect.
                _outgoing_owner = self._session_owner
                if len(self._session_log) >= 3:
                    asyncio.create_task(self._save_session_summary(_outgoing_owner))
                elif self._identity_switch_reconnect:
                    # A different account is taking over this session (see
                    # _set_user_profile()) and the outgoing one's turn log
                    # was too short to summarize — still start the new
                    # account with a clean activity log instead of letting
                    # a handful of someone else's turns carry over into
                    # the next connection's context. A plain network-error
                    # reconnect (this flag unset) deliberately leaves the
                    # log alone, exactly as before — same account, allowed
                    # to keep accumulating toward a summary across a blip.
                    self._session_log = []
                self._identity_switch_reconnect = False

            self.set_speaking(False)
            self._push_state("SLEEPING")   # already broadcasts — see _push_state()

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def main():
    from ui import JarvisUI   # deferred: keeps `from main import JarvisLive` PyQt6-free (Phase 1)
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()