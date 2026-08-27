"""
actions/routing.py -- real distance/ETA via OSRM's public routing
service (free, keyless). No paid dependency, no MCP.

Same plain-synchronous-function convention as actions/geo.py/weather.py
(see actions/geo.py's docstring for the fuller rationale) -- dispatched
via main.py's _execute_tool() through loop.run_in_executor(). Coordinates
for both endpoints are resolved by main.py before calling in here (current
session location for the origin, actions/geo.py's geocode_place() for a
named destination) -- this module has no notion of "current location" or
sessions at all.

Honesty over pretending: the public OSRM demo instance may not support
every requested transport mode reliably. A failure here (bad HTTP status,
or OSRM's own JSON body reporting a non-"Ok" code -- e.g. an unsupported
profile, or no route found between two points) raises a plain RuntimeError
rather than returning invented numbers. main.py's get_directions handling
catches that specifically and falls back to an honest straight-line
distance (see actions/geo.py's haversine_m()) with a clear caveat instead
of a real ETA -- never silent fabrication.
"""
from __future__ import annotations

import requests

OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1"
HTTP_TIMEOUT_S = 8

# OSRM's HTTP API profile path segment -- see http://project-osrm.org/docs/
# .../api/#route-service. Which profiles are actually enabled is a
# property of the specific OSRM deployment; the public demo instance is
# not guaranteed to support every one of these (see this module's
# docstring for how a resulting failure is handled honestly rather than
# silently defaulting to "driving").
_MODE_TO_PROFILE = {
    "driving": "driving", "drive": "driving", "car": "driving",
    "walking": "walking", "walk": "walking", "foot": "walking",
    "cycling": "cycling", "cycle": "cycling", "bike": "cycling", "bicycle": "cycling",
}
DEFAULT_MODE = "driving"


def get_route(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, mode: str = DEFAULT_MODE,
) -> dict:
    """Returns {"distance_m": float, "duration_s": float, "mode": str}
    for the requested profile. Raises RuntimeError with an honest message
    if OSRM can't compute this specific route/profile -- never returns
    invented numbers."""
    profile = _MODE_TO_PROFILE.get((mode or DEFAULT_MODE).lower().strip(), DEFAULT_MODE)
    url = f"{OSRM_ROUTE_URL}/{profile}/{origin_lon},{origin_lat};{dest_lon},{dest_lat}"

    resp = requests.get(url, params={"overview": "false"}, timeout=HTTP_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(
            f"OSRM could not compute a '{profile}' route ({data.get('code', 'unknown error')})."
        )

    route = data["routes"][0]
    return {"distance_m": route["distance"], "duration_s": route["duration"], "mode": profile}
