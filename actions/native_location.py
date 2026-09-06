"""
actions/native_location.py -- a thin, pure adapter over Windows' own
Location API (WinRT `Windows.Devices.Geolocation.Geolocator`, exposed to
Python via the `winsdk` package) for a native Windows desktop session.

Fixes a real, confirmed gap: JARVIS/SARANA previously had NO location
source for a standalone desktop session at all -- every location fix
came from a browser's `navigator.geolocation`, whether the actual web
frontend or a phone paired via Remote Access (see main.py's own
`_get_current_location()`/`_session_location` docstrings). A desktop
session with nothing paired to it started, and stayed, at
`_session_location = None` forever (confirmed by
`tests/test_location_context.py::test_desktop_never_creates_location_state`).
This module adds the ONE thing that was actually missing -- reading the
desktop machine's OWN location from Windows itself -- as ANOTHER SOURCE
feeding the exact same `_session_location` state main.py already
maintains (see main.py's own `_set_session_location(source=...)`); it is
NOT a second location/context system.

Every function here is a small, pure, testable wrapper -- no Qt, no
main.py state, no session/user concept. This module is deliberately
"dumb": it knows how to ask Windows for permission and for a position,
map every documented failure mode to one of the exceptions below, and
nothing else. It is a genuine ADAPTER (per this stage's own explicit
"isolate the provider behind a small adapter and test the real provider
separately" instruction) so its mapping/validation logic can be unit-
tested with a mocked winsdk, while the real WinRT calls are exercised
once, for real, in this stage's own completion report.

CRITICAL THREADING REQUIREMENT (verified against Microsoft's own
documented contract, not assumed): `Geolocator.request_access_async()`
shows a real system consent prompt the first time an app asks, and that
requires a genuine foreground/UI thread with a message pump to render
reliably -- calling it from a bare background worker thread (which is
exactly where main.py's own asyncio loop runs; see ui.py's `main()`:
`threading.Thread(target=runner).start(); ui.root.mainloop()` -- the Qt
GUI event loop owns the MAIN thread, JarvisLive.run() owns a background
one) is unsupported and, per Windows' own guidance, must not be done.
This module therefore does NOT call `request_access()` itself from just
anywhere -- see `request_access()`'s own docstring: the CALLER is
responsible for invoking it from the foreground thread (main.py routes
this through a new `AssistantSurface.request_native_location_permission()`
method, implemented on `ui.py`'s `JarvisUI` by marshaling the actual
WinRT call onto the Qt GUI thread via a signal/slot, exactly the same
thread-safety pattern this codebase already uses for `show_content`/
`write_log`/etc. -- see ui.py's own new `_location_permission_sig`).
Fetching an already-granted position (`get_current_location()` below)
shows no UI and has no such restriction -- it runs on the ordinary
background asyncio loop, same as every other tool in main.py.

Deliberately NOT built, and why:
  - No IP-geolocation fallback anywhere in this module. If Windows'
    OWN location platform internally uses an IP-based provider as one of
    its OS-level sources (`PositionSource.IP_ADDRESS` is a real, official
    Windows Geolocator value alongside GNSS/Wi-Fi/cellular/default) that
    is Windows' own documented behavior, reached only through the same
    permission-gated Geolocator API -- never a second, hidden HTTP call
    to a geolocation service invented by this module.
  - No location history/activity database -- this module returns exactly
    one fix per call, stateless; main.py's own existing session-only,
    RAM-only, never-persisted `_session_location` field is the only place
    a fix is ever held, unchanged from before this module existed.
"""
from __future__ import annotations

import asyncio
import platform

_WINSDK_AVAILABLE = False
if platform.system() == "Windows":
    try:
        from winsdk.windows.devices.geolocation import (
            Geolocator, GeolocationAccessStatus, PositionStatus,
        )
        _WINSDK_AVAILABLE = True
    except ImportError:
        pass

# Bounded -- a hung/slow GNSS fix must never block a tool call forever
# (same "never hang forever" discipline as every other network call in
# this codebase, e.g. actions/weather.py's own HTTP_TIMEOUT_S).
NATIVE_LOCATION_TIMEOUT_S = 8.0


class NativeLocationError(Exception):
    """Base class -- a real, evidence-carrying failure, never guessed at."""


class NativeLocationUnavailable(NativeLocationError):
    """Not Windows, or the winsdk package/WinRT API itself isn't usable
    on this machine -- a platform/environment fact, not a permission or
    a transient failure."""


class NativeLocationPermissionDenied(NativeLocationError):
    """The user (or a Windows policy) has denied this app's OWN access
    to location -- distinct from PositionDisabled below, which is the
    system-wide Windows Location toggle."""


class NativeLocationDisabled(NativeLocationError):
    """Windows Location is turned off system-wide (Settings > Privacy >
    Location) -- affects every app, not just this one."""


class NativeLocationNoData(NativeLocationError):
    """Access is allowed and the platform is enabled, but no fix is
    currently obtainable (e.g. no GNSS/Wi-Fi/cellular signal, no default
    location configured) -- a real, honest "nothing to report" state."""


class NativeLocationTimeout(NativeLocationError):
    """The fix did not arrive within NATIVE_LOCATION_TIMEOUT_S."""


def is_platform_supported() -> bool:
    """True only on Windows with a working winsdk/WinRT Geolocation API
    import -- checked once at import time, never re-imported per call."""
    return _WINSDK_AVAILABLE


def _map_access_status(status) -> str:
    """GeolocationAccessStatus -> a plain, JSON/log-friendly string.
    UNSPECIFIED (Windows' own "the user hasn't been asked, or dismissed
    the prompt without a clear answer" value) is reported as its own
    honest state, never silently folded into either allowed or denied."""
    if status == GeolocationAccessStatus.ALLOWED:
        return "allowed"
    if status == GeolocationAccessStatus.DENIED:
        return "denied"
    return "unspecified"


async def request_access() -> str:
    """Requests this app's own permission to use Windows location.

    MUST be awaited from a genuine foreground/UI-thread context (see
    module docstring's "CRITICAL THREADING REQUIREMENT") -- this
    function itself does no thread marshaling; it is the small, pure,
    directly-testable half of that contract. The actual cross-thread
    delivery lives in ui.py (desktop) / core/headless_surface.py
    (headless, where it's simply reported unavailable).

    Returns "allowed" | "denied" | "unspecified" -- never raises for an
    ordinary denial (that is a real, expected outcome, not an error);
    raises NativeLocationUnavailable only if the platform/API itself
    isn't usable at all."""
    if not _WINSDK_AVAILABLE:
        raise NativeLocationUnavailable("winsdk/WinRT Geolocation API is not available on this platform")
    status = await Geolocator.request_access_async()
    return _map_access_status(status)


async def get_current_location(timeout_s: float = NATIVE_LOCATION_TIMEOUT_S) -> dict:
    """Fetches ONE real fix from Windows' own Geolocator -- never called
    before request_access() has already returned "allowed" for this
    process (the caller, main.py's own _try_native_location(), owns that
    sequencing; see its own docstring) -- but this function still never
    trusts that blindly: request_access_async() is NOT re-invoked here
    (that would risk a second permission prompt from a background
    thread, exactly what must not happen), so a not-actually-granted
    state here surfaces honestly as NativeLocationPermissionDenied from
    the real WinRT call itself, never assumed.

    Shows no UI, runs no consent prompt -- safe to call from an ordinary
    background asyncio task, exactly like every other tool in main.py.

    Returns {"latitude": float, "longitude": float, "accuracy": float,
    "timestamp": datetime} on success -- "timestamp" is the REAL
    Geocoordinate.timestamp (a timezone-aware datetime, Windows' own
    fix time), never main.py's own monotonic clock (that conversion, for
    staleness bookkeeping, is main.py's own job -- see
    _try_native_location()). Raises one of the NativeLocation* exceptions
    above for every other real outcome -- never returns a partial/guessed
    dict, never silently substitutes a default/IP-based value."""
    if not _WINSDK_AVAILABLE:
        raise NativeLocationUnavailable("winsdk/WinRT Geolocation API is not available on this platform")

    geolocator = Geolocator()
    status = geolocator.location_status
    if status == PositionStatus.DISABLED:
        raise NativeLocationDisabled("Windows Location is turned off system-wide")
    if status == PositionStatus.NOT_AVAILABLE:
        raise NativeLocationNoData("Windows reports no location provider is available on this device")
    if status == PositionStatus.NO_DATA:
        raise NativeLocationNoData("Windows Location is enabled but currently has no fix (no signal / no default location configured)")
    # INITIALIZING/NOT_INITIALIZED/READY are all worth a real attempt --
    # get_geoposition_async() below is the actual, authoritative check;
    # location_status is a best-effort hint, never the final word.

    try:
        position = await asyncio.wait_for(geolocator.get_geoposition_async(), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise NativeLocationTimeout(f"no fix within {timeout_s}s") from None
    except PermissionError as e:
        raise NativeLocationPermissionDenied(str(e)) from None
    except OSError as e:
        # WinRT surfaces access-denied as a plain OSError with a Windows
        # HRESULT under `winerror` on this projection -- E_ACCESSDENIED
        # (0x80070005 / -2147024891) is a standard, well-documented COM
        # HRESULT, mapped explicitly here. Deliberately NOT mapping other
        # HRESULTs to NativeLocationNoData/Disabled: this project's own
        # discipline is to never assert an unverified numeric mapping as
        # confirmed (this stage's own real-machine testing could not
        # trigger a genuine "no data" HRESULT to check against) -- any
        # other OSError honestly falls through to the generic
        # NativeLocationError below instead, still fully caught, never
        # silently miscategorized as something more specific than
        # actually confirmed.
        winerror = getattr(e, "winerror", None)
        if winerror == -2147024891:   # E_ACCESSDENIED
            raise NativeLocationPermissionDenied(str(e)) from None
        raise NativeLocationError(f"Windows location fetch failed ({winerror}): {e}") from None
    except Exception as e:
        raise NativeLocationError(f"Windows location fetch failed: {e}") from None

    if position is None:
        raise NativeLocationNoData("Windows returned no position")
    coord = position.coordinate
    if coord is None:
        raise NativeLocationNoData("Windows returned a position with no coordinate")

    lat, lon, acc = coord.latitude, coord.longitude, coord.accuracy
    if lat is None or lon is None or not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise NativeLocationError(f"Windows returned an implausible coordinate ({lat}, {lon})")

    return {
        "latitude": lat,
        "longitude": lon,
        "accuracy": acc if (acc is not None and acc >= 0) else None,
        "timestamp": coord.timestamp,
    }
