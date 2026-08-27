"""Grounded home-to-work commute readiness for Jarvis briefings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from threading import Lock

import httpx

HOME_LAT = os.environ.get("LIFEOS_LAT", "").strip()
HOME_LON = os.environ.get("LIFEOS_LON", "").strip()
WORK_LAT = os.environ.get("LIFEOS_WORK_LAT", "").strip()
WORK_LON = os.environ.get("LIFEOS_WORK_LON", "").strip()
WORK_ADDRESS = os.environ.get(
    "LIFEOS_WORK_ADDRESS", "4411 W. Market St, Greensboro, NC 27405"
).strip()
COMMUTE_ENTITY = os.environ.get("LIFEOS_COMMUTE_ENTITY", "").strip()
HA_URL = os.environ.get("HOME_ASSISTANT_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HOME_ASSISTANT_TOKEN", "").strip()

_cache: tuple[datetime, dict] | None = None
_lock = Lock()


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _home_assistant_commute() -> dict | None:
    """Prefer a commissioned live-traffic sensor without requiring one."""
    if not (HA_URL and HA_TOKEN):
        return None
    try:
        response = httpx.get(
            f"{HA_URL}/api/states",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            timeout=4,
        )
        states = response.raise_for_status().json()
    except (httpx.HTTPError, ValueError):
        return None
    candidates = []
    for state in states:
        entity_id = str(state.get("entity_id") or "")
        if not entity_id.startswith("sensor."):
            continue
        attributes = state.get("attributes") or {}
        label = f"{entity_id} {attributes.get('friendly_name', '')}".lower()
        if COMMUTE_ENTITY:
            if entity_id != COMMUTE_ENTITY:
                continue
        elif not any(term in label for term in ("commute", "travel time", "time to work")):
            continue
        minutes = _number(state.get("state"))
        unit = str(attributes.get("unit_of_measurement") or "").lower()
        if minutes is None or unit not in {"min", "mins", "minute", "minutes"}:
            continue
        try:
            updated = datetime.fromisoformat(str(state.get("last_updated") or "").replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - updated
        except ValueError:
            age = timedelta.max
        if age > timedelta(minutes=45):
            continue
        distance = _number(attributes.get("distance"))
        candidates.append({
            "status": "ready",
            "minutes": round(minutes),
            "miles": round(distance, 1) if distance is not None else None,
            "source": attributes.get("friendly_name") or entity_id,
            "traffic_live": True,
            "fresh": True,
            "entity_id": entity_id,
        })
    return candidates[0] if candidates else None


def _work_coordinates() -> tuple[float, float] | None:
    if _number(WORK_LAT) is not None and _number(WORK_LON) is not None:
        return float(WORK_LAT), float(WORK_LON)
    if not WORK_ADDRESS:
        return None
    try:
        response = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": WORK_ADDRESS, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": "Jarvis-Hub-LifeOS/1.0"},
            timeout=6,
        )
        rows = response.raise_for_status().json()
        if not rows:
            return None
        return float(rows[0]["lat"]), float(rows[0]["lon"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


def _baseline_route() -> dict | None:
    home_lat, home_lon = _number(HOME_LAT), _number(HOME_LON)
    work = _work_coordinates()
    if home_lat is None or home_lon is None or work is None:
        return None
    work_lat, work_lon = work
    try:
        response = httpx.get(
            f"https://router.project-osrm.org/route/v1/driving/"
            f"{home_lon},{home_lat};{work_lon},{work_lat}",
            params={"overview": "false", "steps": "false", "alternatives": "false"},
            headers={"User-Agent": "Jarvis-Hub-LifeOS/1.0"},
            timeout=7,
        )
        route = response.raise_for_status().json()["routes"][0]
        return {
            "status": "degraded",
            "minutes": max(1, round(float(route["duration"]) / 60)),
            "miles": round(float(route["distance"]) / 1609.344, 1),
            "source": "OpenStreetMap / OSRM baseline",
            "traffic_live": False,
            "fresh": True,
            "entity_id": None,
        }
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


def commute_snapshot(*, force: bool = False) -> dict:
    """Return live traffic when commissioned, otherwise a labeled baseline."""
    global _cache
    now = datetime.now(timezone.utc)
    with _lock:
        if not force and _cache and now - _cache[0] < timedelta(minutes=15):
            return _cache[1]
    result = _home_assistant_commute() or _baseline_route()
    if result is None:
        result = {
            "status": "unavailable",
            "minutes": None,
            "miles": None,
            "source": None,
            "traffic_live": False,
            "fresh": False,
            "entity_id": COMMUTE_ENTITY or None,
            "guidance": (
                "Home coordinates or internet routing are unavailable. For live traffic, "
                "commission a Waze or Google travel-time sensor in Home Assistant."
            ),
        }
    else:
        result["guidance"] = (
            "Live traffic sensor commissioned."
            if result["traffic_live"] else
            "Baseline route only; commission a Waze or Google travel-time sensor for traffic-aware timing."
        )
    result["destination"] = "Work"
    result["planned_departure"] = "07:35"
    with _lock:
        _cache = (now, result)
    return result
