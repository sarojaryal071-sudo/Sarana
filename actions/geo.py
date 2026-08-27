"""
actions/geo.py -- OpenStreetMap-backed geocoding, reverse geocoding, and
nearby-place search. No API key, no paid dependency (per the location-
capability plan: Nominatim for geocoding, Overpass for POI search).

Every public function here is a plain, synchronous, stateless function --
same shape every other actions/*.py module already uses (e.g.
weather_report.py, web_search.py) -- so main.py's _execute_tool() can keep
dispatching to it via loop.run_in_executor(), exactly like every other
tool. No session state, no caching, and no asyncio lives in this file:
session-scoped caching (which place was last resolved, for how long)
belongs in main.py, which is the thing that actually owns a session --
this module has no notion of "session" at all.

Provider usage policy: both Nominatim and Overpass are shared public
infrastructure with real usage limits (Nominatim: effectively 1 request/
second, requires an identifying User-Agent; Overpass: fair-use rate
limits, discourages large/uncontrolled queries). _throttle_nominatim()
below self-imposes the 1 req/s ceiling; NOMINATIM_USER_AGENT identifies
this app truthfully (override via env var with real operator contact
info before any real production traffic -- see that constant's own
comment). Overpass queries are always bounded to a capped search radius
(see MAX_RADIUS_M) and a capped result count -- never an unbounded area/
city-wide query. If usage ever grows past what these public instances are
meant for, only the URLs/throttling in this one file need to change to
point at a self-hosted or paid instance -- nothing in main.py or the tool
layer needs to know the difference.
"""
from __future__ import annotations

import math
import os
import threading
import time

import requests

NOMINATIM_SEARCH_URL  = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OVERPASS_URL          = "https://overpass-api.de/api/interpreter"

HTTP_TIMEOUT_S = 8

# Nominatim's usage policy asks for a real, identifying User-Agent (ideally
# with operator contact info) -- this default is honest about not having
# one configured; set NOMINATIM_USER_AGENT in the environment (same pattern
# as GEMINI_API_KEY -- see main.py's _get_api_key()) before any real
# production deployment.
NOMINATIM_USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "SARANA-Voice-Assistant/1.0 (location feature; "
    "set NOMINATIM_USER_AGENT with real contact info for production use)",
)
_HEADERS = {"User-Agent": NOMINATIM_USER_AGENT}

DEFAULT_RADIUS_M = 1500
MAX_RADIUS_M     = 5000
MIN_RADIUS_M     = 100
MAX_RESULTS      = 8


# ── Nominatim rate limiting (policy: effectively 1 request/second) ───────

_nominatim_lock = threading.Lock()
_last_nominatim_call = 0.0


def _throttle_nominatim() -> None:
    """Blocks the calling thread (never the asyncio event loop -- every
    caller reaches this via loop.run_in_executor(), same as any other
    blocking I/O in this codebase) just long enough to keep this
    process's own Nominatim traffic at or under ~1 request/second,
    regardless of how many geocode/reverse-geocode calls happen to land
    close together."""
    global _last_nominatim_call
    with _nominatim_lock:
        wait = 1.0 - (time.monotonic() - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim_call = time.monotonic()


# ── Geocoding ──────────────────────────────────────────────────────────

def geocode_place(name: str) -> tuple[float, float, str] | None:
    """Forward geocode a free-text place name via Nominatim.
    Returns (latitude, longitude, display_label) or None if nothing
    matched. Raises on a genuine network/HTTP failure (never swallowed
    here -- see this module's docstring: callers/main.py's existing
    generic tool-failure handling is what turns that into an honest
    response, exactly like every other action in this codebase)."""
    name = (name or "").strip()
    if not name:
        return None
    _throttle_nominatim()
    resp = requests.get(
        NOMINATIM_SEARCH_URL,
        params={"q": name, "format": "jsonv2", "limit": 1, "addressdetails": 1},
        headers=_HEADERS, timeout=HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    r = results[0]
    return float(r["lat"]), float(r["lon"]), r.get("display_name", name)


def reverse_geocode(latitude: float, longitude: float) -> dict | None:
    """Reverse geocode a coordinate via Nominatim into a small place
    dict: {"city", "area", "country", "label"} (any field may be "" if
    Nominatim's response didn't include it). Returns None only if
    Nominatim itself reports no result for these coordinates (e.g. open
    ocean) -- a genuine failure still raises, same convention as
    geocode_place()."""
    _throttle_nominatim()
    resp = requests.get(
        NOMINATIM_REVERSE_URL,
        params={"lat": latitude, "lon": longitude, "format": "jsonv2", "zoom": 14, "addressdetails": 1},
        headers=_HEADERS, timeout=HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data or "error" in data:
        return None
    addr = data.get("address", {})
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or ""
    area = addr.get("suburb") or addr.get("neighbourhood") or addr.get("city_district") or ""
    country = addr.get("country") or ""
    return {"city": city, "area": area, "country": country, "label": data.get("display_name", "")}


def format_place(place: dict) -> str:
    """Turns a reverse_geocode()/place-cache dict into a short natural-
    language-ready description for Gemini -- never raw coordinates."""
    if not place:
        return "Current location could not be resolved to a place."
    parts = [p for p in (place.get("area"), place.get("city"), place.get("country")) if p]
    if not parts:
        return place.get("label") or "Current location could not be resolved to a place."
    return ", ".join(parts)


# ── Distance ───────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters. Pure math, no dependency -- used
    both to sort/annotate nearby-places results and as an honest
    straight-line fallback when routing (actions/routing.py) can't
    compute a real route."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def format_distance(meters: float) -> str:
    if meters < 1000:
        return f"{round(meters)} m"
    return f"{meters / 1000:.1f} km"


# ── Nearby places (Overpass) ──────────────────────────────────────────

# Common natural-language query terms -> OSM tag(s). Deliberately small
# and easy to extend -- an unmatched query still works via the free-text
# name search fallback below, this table just makes the common cases
# (which is most real usage) return properly-categorized OSM data instead
# of a fuzzy name match.
_CATEGORY_TAGS: dict[str, list[tuple[str, str]]] = {
    "pharmacy":     [("amenity", "pharmacy")],
    "chemist":      [("amenity", "pharmacy")],
    "coffee":       [("amenity", "cafe")],
    "cafe":         [("amenity", "cafe")],
    "coffee shop":  [("amenity", "cafe")],
    "restaurant":   [("amenity", "restaurant")],
    "food":         [("amenity", "restaurant"), ("amenity", "fast_food")],
    "fast food":    [("amenity", "fast_food")],
    "supermarket":  [("shop", "supermarket")],
    "grocery":      [("shop", "supermarket"), ("shop", "convenience")],
    "convenience store": [("shop", "convenience")],
    "hospital":     [("amenity", "hospital")],
    "clinic":       [("amenity", "clinic")],
    "doctor":       [("amenity", "doctors")],
    "dentist":      [("amenity", "dentist")],
    "atm":          [("amenity", "atm")],
    "bank":         [("amenity", "bank")],
    "gas station":  [("amenity", "fuel")],
    "petrol station": [("amenity", "fuel")],
    "fuel":         [("amenity", "fuel")],
    "bar":          [("amenity", "bar")],
    "pub":          [("amenity", "pub")],
    "hotel":        [("tourism", "hotel")],
    "park":         [("leisure", "park")],
    "bakery":       [("shop", "bakery")],
    "bus stop":     [("highway", "bus_stop")],
    "train station": [("railway", "station")],
    "toilet":       [("amenity", "toilets")],
    "restroom":     [("amenity", "toilets")],
    "library":      [("amenity", "library")],
    "post office":  [("amenity", "post_office")],
}


def _match_tags(query: str) -> list[tuple[str, str]]:
    q = (query or "").lower().strip()
    if q in _CATEGORY_TAGS:
        return _CATEGORY_TAGS[q]
    for key, tags in _CATEGORY_TAGS.items():
        if key in q or q in key:
            return tags
    return []


def find_nearby_places(
    query: str, latitude: float, longitude: float, radius_m: int = DEFAULT_RADIUS_M,
) -> list[dict]:
    """Searches OpenStreetMap (via Overpass) for places matching `query`
    within `radius_m` of the given coordinates. Returns a list of
    {"name", "category", "distance_m", "address"} dicts sorted nearest
    first, capped at MAX_RESULTS. Radius is always clamped to
    [MIN_RADIUS_M, MAX_RADIUS_M] -- never an uncontrolled/unbounded query,
    per Overpass's own fair-use expectations."""
    try:
        radius_m = int(radius_m)
    except (TypeError, ValueError):
        radius_m = DEFAULT_RADIUS_M
    radius_m = max(MIN_RADIUS_M, min(radius_m, MAX_RADIUS_M))

    tags = _match_tags(query)
    if tags:
        clauses = "\n".join(
            f'  node["{k}"="{v}"](around:{radius_m},{latitude},{longitude});\n'
            f'  way["{k}"="{v}"](around:{radius_m},{latitude},{longitude});'
            for k, v in tags
        )
    else:
        # Free-text fallback: a bounded, case-insensitive name match --
        # still scoped to the same capped radius, never a broader search.
        safe_q = (query or "").replace('"', "").strip()[:80]
        clauses = (
            f'  node["name"~"{safe_q}",i](around:{radius_m},{latitude},{longitude});\n'
            f'  way["name"~"{safe_q}",i](around:{radius_m},{latitude},{longitude});'
        )

    ql = f"[out:json][timeout:10];\n(\n{clauses}\n);\nout center 20;"
    resp = requests.post(OVERPASS_URL, data={"data": ql}, headers=_HEADERS, timeout=12)
    resp.raise_for_status()
    elements = resp.json().get("elements", [])

    results = []
    for el in elements:
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        el_tags = el.get("tags", {})
        name = el_tags.get("name")
        if not name:
            continue  # unnamed OSM elements aren't useful to speak aloud
        dist = haversine_m(latitude, longitude, lat, lon)
        results.append({
            "name": name,
            "category": el_tags.get("amenity") or el_tags.get("shop")
                        or el_tags.get("tourism") or el_tags.get("leisure") or query,
            "distance_m": round(dist),
            "address": _short_address(el_tags),
        })

    results.sort(key=lambda r: r["distance_m"])
    return results[:MAX_RESULTS]


def _short_address(tags: dict) -> str:
    street = tags.get("addr:street")
    if not street:
        return ""
    number = tags.get("addr:housenumber", "")
    return f"{street} {number}".strip()


def format_nearby_places(query: str, results: list[dict]) -> str:
    """Turns find_nearby_places()'s result list into short natural-
    language-ready text for Gemini."""
    if not results:
        return f"No {query} found nearby."
    lines = [f"Nearby results for '{query}' (closest first):"]
    for r in results:
        addr = f" -- {r['address']}" if r["address"] else ""
        lines.append(f"- {r['name']} ({format_distance(r['distance_m'])}){addr}")
    return "\n".join(lines)
