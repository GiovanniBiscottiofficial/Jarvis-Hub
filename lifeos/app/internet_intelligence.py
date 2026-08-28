"""Bounded, explainable internet intelligence for Jarvis.

Online data is read-only, attributed, cached briefly, and never grants action
authority. Search text is processed ephemerally and is not written to LifeOS.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import html
import os
import re
from threading import Lock
from typing import Any, Callable
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx

from .commute import commute_snapshot
from .db import active_profile, conn


LAT = os.environ.get("LIFEOS_LAT", "").strip()
LON = os.environ.get("LIFEOS_LON", "").strip()
HA_URL = os.environ.get("HOME_ASSISTANT_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HOME_ASSISTANT_TOKEN", "").strip()
CALENDAR_ENTITY = os.environ.get("LIFEOS_CALENDAR_ENTITY", "").strip()
USDA_KEY = os.environ.get("USDA_FDC_API_KEY", "DEMO_KEY").strip() or "DEMO_KEY"
USER_AGENT = "Jarvis-Hub-LifeOS/1.0 (local personal assistant)"
CONNECTED_CAPABILITIES = {
    "retailer_pricing", "inbox_delivery", "bank_sync", "streaming_availability",
}
SENSITIVE_KEYS = {
    "password", "secret", "token", "access_token", "refresh_token",
    "authorization", "account_number", "routing_number", "card_number", "cvv",
}
ACTION_KEYS = {"execute", "checkout", "transfer", "service_call", "payment", "purchase"}

_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
_cache_lock = Lock()

WEATHER_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy", 51: "drizzle", 53: "drizzle", 55: "drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 80: "showers", 81: "showers",
    82: "heavy showers", 95: "thunderstorms", 96: "thunderstorms",
    99: "thunderstorms",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source(
    source_id: str,
    name: str,
    status: str,
    *,
    data: dict[str, Any] | list[Any] | None = None,
    message: str = "",
    source_url: str | None = None,
    fetched_at: str | None = None,
    cached: bool = False,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "name": name,
        "status": status,
        "fresh": status == "ready",
        "cached": cached,
        "fetched_at": fetched_at or _now().isoformat(timespec="seconds"),
        "source_url": source_url,
        "message": message,
        "data": data if data is not None else {},
        "authority": "read_only_advisory",
    }


def _not_commissioned(source_id: str, name: str, message: str) -> dict[str, Any]:
    return _source(source_id, name, "not_commissioned", message=message)


def _cached(key: str, ttl: timedelta, loader: Callable[[], dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
    now = _now()
    with _cache_lock:
        prior = _cache.get(key)
    if prior and not force and now - prior[0] < ttl:
        result = dict(prior[1])
        result["cached"] = True
        return result
    try:
        result = loader()
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        if prior:
            result = dict(prior[1])
            result.update({
                "status": "stale",
                "fresh": False,
                "cached": True,
                "message": f"Live refresh failed; last verified result retained ({type(exc).__name__}).",
            })
            return result
        return _source(key, key.replace("_", " ").title(), "unavailable", message=f"Source unavailable ({type(exc).__name__}).")
    with _cache_lock:
        _cache[key] = (now, result)
    return result


def _client(timeout: float = 7) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def weather_source(*, force: bool = False) -> dict[str, Any]:
    if not (LAT and LON):
        return _not_commissioned("weather", "Weather", "Set LIFEOS_LAT and LIFEOS_LON for local forecasts.")

    def load() -> dict[str, Any]:
        with _client() as client:
            response = client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": LAT,
                "longitude": LON,
                "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                "hourly": "precipitation_probability,temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code,uv_index_max,sunrise,sunset",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "forecast_days": 2,
                "timezone": "auto",
            })
            payload = response.raise_for_status().json()
        current = payload["current"]
        hourly = payload["hourly"]
        current_hour = datetime.now().strftime("%Y-%m-%dT%H:00")
        try:
            start = hourly["time"].index(current_hour)
        except ValueError:
            start = 0
        next_hours = [
            {
                "time": hourly["time"][index],
                "temperature_f": hourly["temperature_2m"][index],
                "rain_chance": hourly["precipitation_probability"][index],
                "conditions": WEATHER_CODES.get(hourly["weather_code"][index], "mixed"),
            }
            for index in range(start, min(start + 8, len(hourly["time"])))
        ]
        daily = payload["daily"]
        return _source(
            "weather", "Open-Meteo forecast", "ready",
            source_url="https://open-meteo.com/",
            message="Live local forecast verified.",
            data={
                "temperature_f": current.get("temperature_2m"),
                "feels_like_f": current.get("apparent_temperature"),
                "conditions": WEATHER_CODES.get(current.get("weather_code"), "mixed"),
                "wind_mph": current.get("wind_speed_10m"),
                "high_f": daily["temperature_2m_max"][0],
                "low_f": daily["temperature_2m_min"][0],
                "uv_index_max": daily["uv_index_max"][0],
                "sunrise": daily["sunrise"][0],
                "sunset": daily["sunset"][0],
                "next_hours": next_hours,
                "max_rain_chance_8h": max((row["rain_chance"] or 0 for row in next_hours), default=0),
            },
        )

    return _cached("weather", timedelta(minutes=20), load, force=force)


def air_quality_source(*, force: bool = False) -> dict[str, Any]:
    if not (LAT and LON):
        return _not_commissioned("air_quality", "Air quality", "Set LIFEOS_LAT and LIFEOS_LON for air-quality and pollen guidance.")

    def load() -> dict[str, Any]:
        with _client() as client:
            response = client.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
                "latitude": LAT,
                "longitude": LON,
                "current": "us_aqi,pm2_5,grass_pollen,birch_pollen,alder_pollen",
                "timezone": "auto",
            })
            current = response.raise_for_status().json()["current"]
        aqi = current.get("us_aqi")
        category = "unknown"
        if aqi is not None:
            category = "good" if aqi <= 50 else "moderate" if aqi <= 100 else "unhealthy for sensitive groups" if aqi <= 150 else "unhealthy"
        return _source(
            "air_quality", "Open-Meteo air quality", "ready",
            source_url="https://open-meteo.com/en/docs/air-quality-api",
            message=f"Current air quality is {category}.",
            data={
                "us_aqi": aqi,
                "category": category,
                "pm2_5": current.get("pm2_5"),
                "pollen": {
                    "grass": current.get("grass_pollen"),
                    "birch": current.get("birch_pollen"),
                    "alder": current.get("alder_pollen"),
                },
            },
        )

    return _cached("air_quality", timedelta(minutes=45), load, force=force)


def weather_alerts_source(*, force: bool = False) -> dict[str, Any]:
    if not (LAT and LON):
        return _not_commissioned("weather_alerts", "NWS alerts", "Set LIFEOS_LAT and LIFEOS_LON for official local alerts.")

    def load() -> dict[str, Any]:
        with _client() as client:
            response = client.get("https://api.weather.gov/alerts/active", params={"point": f"{LAT},{LON}"}, headers={"Accept": "application/geo+json"})
            features = response.raise_for_status().json().get("features", [])
        alerts = []
        for feature in features[:10]:
            props = feature.get("properties") or {}
            alerts.append({
                "event": props.get("event"),
                "severity": props.get("severity"),
                "urgency": props.get("urgency"),
                "headline": props.get("headline"),
                "ends": props.get("ends") or props.get("expires"),
                "instruction": str(props.get("instruction") or "")[:500],
                "url": props.get("@id") or feature.get("id"),
            })
        return _source(
            "weather_alerts", "National Weather Service", "ready",
            source_url="https://api.weather.gov/alerts/active",
            message=f"{len(alerts)} active official alert(s)." if alerts else "No active official weather alerts.",
            data={"alerts": alerts, "count": len(alerts)},
        )

    return _cached("weather_alerts", timedelta(minutes=10), load, force=force)


def calendar_source(*, force: bool = False) -> dict[str, Any]:
    if not (HA_URL and HA_TOKEN):
        return _not_commissioned("calendar", "Calendar", "Home Assistant credentials are required to read commissioned calendars.")

    def load() -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {HA_TOKEN}"}
        now = _now()
        end = now + timedelta(hours=36)
        with _client() as client:
            states = client.get(f"{HA_URL}/api/states", headers=headers).raise_for_status().json()
            entities = [state["entity_id"] for state in states if str(state.get("entity_id", "")).startswith("calendar.")]
            if CALENDAR_ENTITY:
                entities = [CALENDAR_ENTITY] if CALENDAR_ENTITY in entities else []
            events = []
            for entity_id in entities[:6]:
                response = client.get(
                    f"{HA_URL}/api/calendars/{quote(entity_id, safe='.')}",
                    headers=headers,
                    params={"start": now.isoformat(), "end": end.isoformat()},
                )
                if response.status_code != 200:
                    continue
                for event in response.json()[:10]:
                    start = event.get("start") or {}
                    events.append({
                        "calendar": entity_id,
                        "summary": str(event.get("summary") or "Scheduled event")[:180],
                        "start": start.get("dateTime") or start.get("date"),
                        "end": (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date"),
                        "location": str(event.get("location") or "")[:180],
                        "description_available": bool(event.get("description")),
                    })
        events.sort(key=lambda event: event.get("start") or "")
        return _source(
            "calendar", "Home Assistant calendars", "ready" if entities else "not_commissioned",
            message=(f"{len(events)} upcoming event(s) across {len(entities)} calendar(s)." if entities else "No Home Assistant calendar entity is commissioned."),
            data={"events": events[:12], "count": len(events), "entities": entities},
        )

    return _cached("calendar", timedelta(minutes=5), load, force=force)


def commute_source(*, force: bool = False) -> dict[str, Any]:
    commute = commute_snapshot(force=force)
    status = "ready" if commute.get("traffic_live") else "degraded" if commute.get("minutes") else "not_commissioned"
    return _source(
        "commute", str(commute.get("source") or "Commute routing"), status,
        message=str(commute.get("guidance") or "Commute status unavailable."),
        data=commute,
    )


def capability_catalog(calendar: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    calendar = calendar or {}
    connected = connected_feeds()
    feed_status = {feed["capability"]: feed["status"] for feed in connected}
    return [
        {"id": "environment", "name": "Weather, air quality and official alerts", "status": "ready" if LAT and LON else "not_commissioned", "dependency": "LIFEOS_LAT / LIFEOS_LON", "authority": "briefing_advisory"},
        {"id": "commute", "name": "Live traffic and leave-by timing", "status": "ready" if HA_URL and HA_TOKEN else "degraded", "dependency": "Home Assistant Waze sensor", "authority": "briefing_advisory"},
        {"id": "calendar", "name": "Calendar-aware routines", "status": calendar.get("status", "not_commissioned"), "dependency": "Home Assistant calendar entity", "authority": "context_only"},
        {"id": "research", "name": "Cited reference and current-news research", "status": "ready", "dependency": "Crossref + Bing News RSS", "authority": "answer_only"},
        {"id": "nutrition", "name": "USDA nutrition lookup", "status": "ready" if USDA_KEY else "not_commissioned", "dependency": "USDA_FDC_API_KEY", "authority": "estimate_requires_review"},
        {"id": "media_catalog", "name": "Cross-catalog media discovery", "status": "limited", "dependency": "Apple catalog ready; streaming availability provider not commissioned", "authority": "open_link_only"},
        {"id": "retailer_pricing", "name": "Live grocery price, stock and coupons", "status": feed_status.get("retailer_pricing", "not_commissioned"), "dependency": "Approved retailer account/API", "authority": "review_only_no_checkout"},
        {"id": "inbox_delivery", "name": "Email, package and appointment extraction", "status": feed_status.get("inbox_delivery", "not_commissioned"), "dependency": "Read-only email connector", "authority": "summary_only"},
        {"id": "bank_sync", "name": "Read-only bank reconciliation", "status": feed_status.get("bank_sync", "not_commissioned"), "dependency": "Financial-data connector and Giovanni approval", "authority": "read_only_no_transfers"},
        {"id": "streaming_availability", "name": "Where-to-watch availability", "status": feed_status.get("streaming_availability", "not_commissioned"), "dependency": "Licensed availability provider", "authority": "open_link_only"},
    ]


def internet_snapshot(*, force: bool = False) -> dict[str, Any]:
    loaders = {
        "weather": lambda: weather_source(force=force),
        "air_quality": lambda: air_quality_source(force=force),
        "weather_alerts": lambda: weather_alerts_source(force=force),
        "calendar": lambda: calendar_source(force=force),
        "commute": lambda: commute_source(force=force),
    }
    sources: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(loaders)) as pool:
        futures = {pool.submit(loader): key for key, loader in loaders.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                sources[key] = future.result()
            except Exception as exc:  # defensive isolation: one source never blanks the page
                sources[key] = _source(key, key.replace("_", " ").title(), "unavailable", message=f"Source isolated after {type(exc).__name__}.")
    ordered = [sources[key] for key in loaders]
    capabilities = capability_catalog(sources.get("calendar"))
    feeds = connected_feeds()
    ready = sum(source["status"] == "ready" for source in ordered)
    degraded = sum(source["status"] in {"degraded", "stale", "unavailable"} for source in ordered)
    return {
        "generated_at": _now().isoformat(timespec="seconds"),
        "status": "attention" if degraded else "ready" if ready else "awaiting_setup",
        "summary": {"live_sources": ready, "degraded_sources": degraded, "total_sources": len(ordered)},
        "sources": ordered,
        "capabilities": capabilities,
        "connected_feeds": feeds,
        "policy": {
            "read_only": True,
            "internet_results_authorize_actions": False,
            "source_and_timestamp_required": True,
            "stale_results_labeled": True,
            "search_queries_persisted": False,
            "checkout_allowed": False,
            "financial_transfers_allowed": False,
        },
    }


def _sanitize_connected(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[depth-limited]"
    if isinstance(value, dict):
        clean = {}
        for key, item in list(value.items())[:100]:
            normalized = str(key).lower()
            if normalized in SENSITIVE_KEYS or normalized in ACTION_KEYS:
                continue
            clean[str(key)[:80]] = _sanitize_connected(item, depth=depth + 1)
        return clean
    if isinstance(value, list):
        return [_sanitize_connected(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value[:1000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1000]


def ingest_connected_feed(
    capability: str,
    *,
    source: str,
    payload: dict[str, Any],
    observed_at: datetime | None = None,
    ttl_minutes: int = 60,
) -> dict[str, Any]:
    if capability not in CONNECTED_CAPABILITIES:
        raise ValueError("unsupported connected capability")
    now = _now()
    observed = observed_at or now
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    expires = observed + timedelta(minutes=max(5, min(int(ttl_minutes), 1440)))
    clean = _sanitize_connected(payload)
    import json
    encoded = json.dumps(clean, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 65536:
        raise ValueError("connected feed payload exceeds 64 KB")
    with conn() as database:
        profile = active_profile(database)
        database.execute(
            "INSERT INTO internet_feeds(capability,profile_id,source,status,observed_at,expires_at,payload_json)"
            " VALUES(?,?,?,?,?,?,?) ON CONFLICT(capability,profile_id) DO UPDATE SET"
            " source=excluded.source,status=excluded.status,observed_at=excluded.observed_at,"
            " expires_at=excluded.expires_at,payload_json=excluded.payload_json,"
            " updated_at=datetime('now','localtime')",
            (capability, profile["id"], source[:120], "ready", observed.isoformat(), expires.isoformat(), encoded),
        )
    return {"capability": capability, "source": source[:120], "status": "ready", "observed_at": observed.isoformat(), "expires_at": expires.isoformat(), "payload": clean, "authority": "read_only_advisory"}


def connected_feeds() -> list[dict[str, Any]]:
    import json
    now = _now()
    with conn() as database:
        profile = active_profile(database)
        rows = database.execute(
            "SELECT * FROM internet_feeds WHERE profile_id=? ORDER BY capability",
            (profile["id"],),
        ).fetchall()
    feeds = []
    for row in rows:
        try:
            expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
            status = "ready" if expires >= now else "stale"
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError, json.JSONDecodeError):
            status, payload = "unavailable", {}
        feeds.append({
            "capability": row["capability"], "source": row["source"],
            "status": status, "observed_at": row["observed_at"],
            "expires_at": row["expires_at"], "payload": payload,
            "authority": "read_only_advisory",
        })
    return feeds


def _plain(value: Any, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def research(query: str) -> dict[str, Any]:
    query = query.strip()[:200]
    if len(query) < 2:
        raise ValueError("Enter at least two characters to research.")
    references: list[dict[str, Any]] = []
    news: list[dict[str, Any]] = []
    errors: list[str] = []
    with _client(timeout=10) as client:
        try:
            response = client.get("https://api.crossref.org/works", params={
                "query": query, "rows": 5,
                "select": "DOI,title,published,URL,publisher,type",
            })
            for work in response.raise_for_status().json().get("message", {}).get("items", []):
                titles = work.get("title") or []
                title = str(titles[0] if titles else "Untitled work")
                date_parts = (work.get("published") or {}).get("date-parts") or []
                published = "-".join(str(part) for part in (date_parts[0] if date_parts else []))
                references.append({
                    "title": title,
                    "summary": " · ".join(filter(None, [str(work.get("publisher") or ""), str(work.get("type") or "").replace("-", " "), published])),
                    "url": str(work.get("URL") or f"https://doi.org/{quote(str(work.get('DOI') or ''))}"),
                    "source": "Crossref",
                })
        except (httpx.HTTPError, ValueError, KeyError):
            errors.append("Crossref")
        try:
            response = client.get("https://www.bing.com/news/search", params={
                "q": query, "format": "rss", "qft": 'interval="7"',
            })
            root = ET.fromstring(response.raise_for_status().text)
            for item in root.findall("./channel/item")[:6]:
                values = {child.tag.split("}")[-1]: child.text for child in item}
                news.append({
                    "title": _plain(values.get("title"), 220),
                    "url": str(values.get("link") or ""),
                    "source": _plain(values.get("Source") or "Bing News", 100),
                    "published_at": values.get("pubDate"),
                    "language": None,
                })
        except (httpx.HTTPError, ValueError, KeyError, ET.ParseError):
            errors.append("Bing News RSS")
    return {
        "query": query,
        "generated_at": _now().isoformat(timespec="seconds"),
        "references": references,
        "news": news,
        "status": "ready" if references or news else "unavailable",
        "message": "Results are source material, not an automatically verified conclusion.",
        "unavailable_sources": errors,
        "policy": {"read_only": True, "query_persisted": False, "citations_required": True},
    }


def media_search(query: str) -> dict[str, Any]:
    query = query.strip()[:160]
    if len(query) < 2:
        raise ValueError("Enter at least two characters to search media.")
    results: list[dict[str, Any]] = []
    with _client(timeout=10) as client:
        for media in ("movie", "tvShow", "music"):
            response = client.get("https://itunes.apple.com/search", params={
                "term": query, "country": "US", "media": media, "limit": 4, "explicit": "No",
            })
            for item in response.raise_for_status().json().get("results", []):
                title = item.get("trackName") or item.get("collectionName") or item.get("artistName")
                if not title:
                    continue
                results.append({
                    "title": str(title)[:180],
                    "creator": str(item.get("artistName") or "")[:140],
                    "kind": item.get("kind") or item.get("collectionType") or media,
                    "genre": item.get("primaryGenreName"),
                    "release_year": str(item.get("releaseDate") or "")[:4] or None,
                    "store_url": item.get("trackViewUrl") or item.get("collectionViewUrl") or item.get("artistViewUrl"),
                    "price": item.get("trackPrice") or item.get("collectionPrice"),
                    "currency": item.get("currency"),
                    "source": "Apple catalog",
                })
    return {
        "query": query, "results": results[:12], "status": "ready",
        "message": "Catalog matches only; subscription and streaming availability are not inferred.",
        "policy": {"query_persisted": False, "playback_authorized": False, "availability_claimed": False},
    }


def nutrition_search(query: str) -> dict[str, Any]:
    query = query.strip()[:160]
    if len(query) < 2:
        raise ValueError("Enter at least two characters to search nutrition.")
    with _client(timeout=12) as client:
        response = client.get("https://api.nal.usda.gov/fdc/v1/foods/search", params={
            "api_key": USDA_KEY, "query": query, "pageSize": 6,
        })
        foods = response.raise_for_status().json().get("foods", [])
    results = []
    for food in foods[:6]:
        nutrient_rows = food.get("foodNutrients", [])

        def nutrient(name: str, unit: str | None = None) -> float | None:
            for item in nutrient_rows:
                if str(item.get("nutrientName") or "").lower() != name.lower():
                    continue
                if unit and str(item.get("unitName") or "").upper() != unit.upper():
                    continue
                try:
                    return float(item.get("value"))
                except (TypeError, ValueError):
                    return None
            return None

        protein = nutrient("Protein", "G")
        calories = nutrient("Energy", "KCAL")
        serving_size = food.get("servingSize")
        serving_unit = str(food.get("servingSizeUnit") or "")
        basis = "per 100 g reference basis"
        if serving_size is not None and serving_unit.lower() in {"g", "gram", "grams"}:
            try:
                factor = float(serving_size) / 100
                protein = round(protein * factor, 1) if protein is not None else None
                calories = round(calories * factor) if calories is not None else None
                basis = f"per {float(serving_size):g} g listed serving"
            except (TypeError, ValueError):
                pass
        results.append({
            "fdc_id": food.get("fdcId"),
            "description": str(food.get("description") or "")[:220],
            "brand": str(food.get("brandOwner") or food.get("brandName") or "")[:140],
            "data_type": food.get("dataType"),
            "serving_size": serving_size,
            "serving_unit": serving_unit,
            "protein_g": protein,
            "calories": calories,
            "nutrient_basis": basis,
            "source": "USDA FoodData Central",
        })
    return {
        "query": query, "results": results, "status": "ready",
        "message": "Match the package and serving size before logging; nutrition remains an estimate until confirmed.",
        "source_url": "https://fdc.nal.usda.gov/",
        "policy": {"query_persisted": False, "requires_user_confirmation_before_logging": True},
    }


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
