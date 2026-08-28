"""Pantry-seeded internet recipe discovery with a bounded local cache."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import re
from threading import Lock

import httpx

from .chef import excluded_reason, normalize
from .db import conn

API_ROOT = "https://www.themealdb.com/api/json/v1/1"
SEED_INGREDIENTS = (
    "chicken", "salmon", "beef", "pork", "egg", "tuna", "turkey",
    "shrimp", "cod", "lamb",
)
_cache: dict[str, tuple[datetime, dict]] = {}
_lock = Lock()


def _ingredient_rows(meal: dict) -> list[dict]:
    rows = []
    for index in range(1, 21):
        name = str(meal.get(f"strIngredient{index}") or "").strip()
        if not name:
            continue
        rows.append({
            "name": name,
            "measure": str(meal.get(f"strMeasure{index}") or "").strip(),
        })
    return rows


def _stocked(stock: list[str], ingredient: str) -> bool:
    wanted = normalize(ingredient)
    return any(value == wanted or value in wanted or wanted in value for value in stock)


def discover_recipes(limit: int = 6) -> dict:
    """Return rotating, pantry-aware ideas; callers retain local recipes as fallback."""
    limit = max(1, min(int(limit), 10))
    with conn() as database:
        rows = database.execute("SELECT name FROM pantry WHERE qty>0 ORDER BY name").fetchall()
    stock = [normalize(row["name"]) for row in rows]
    pantry_seeds = [seed for seed in SEED_INGREDIENTS if any(seed in item for item in stock)]
    seed_pool = pantry_seeds or list(SEED_INGREDIENTS)
    day_key = date.today().isoformat()
    offset = int(hashlib.sha256(day_key.encode()).hexdigest()[:8], 16) % len(seed_pool)
    seeds = (seed_pool[offset:] + seed_pool[:offset])[:4]
    cache_key = f"{day_key}:{','.join(seeds)}:{limit}"
    now = datetime.now()
    with _lock:
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < timedelta(hours=6):
            return cached[1]

    summaries: list[dict] = []
    seen: set[str] = set()
    try:
        with httpx.Client(base_url=API_ROOT, timeout=7, follow_redirects=True) as client:
            for seed in seeds:
                response = client.get("/filter.php", params={"i": seed})
                response.raise_for_status()
                choices = (response.json().get("meals") or [])[:4]
                for choice in choices:
                    meal_id = str(choice.get("idMeal") or "")
                    if not meal_id or meal_id in seen:
                        continue
                    detail_response = client.get("/lookup.php", params={"i": meal_id})
                    detail_response.raise_for_status()
                    meal = (detail_response.json().get("meals") or [None])[0]
                    if not meal:
                        continue
                    ingredients = _ingredient_rows(meal)
                    unsafe = [
                        reason for value in [str(meal.get("strMeal") or ""), *[i["name"] for i in ingredients]]
                        if (reason := excluded_reason(value))
                    ]
                    if unsafe:
                        continue
                    available = [item for item in ingredients if _stocked(stock, item["name"])]
                    missing = [item for item in ingredients if item not in available]
                    source_url = str(meal.get("strSource") or meal.get("strYoutube") or "").strip()
                    if source_url and not re.match(r"^https?://", source_url, re.I):
                        source_url = ""
                    summaries.append({
                        "id": f"themealdb-{meal_id}",
                        "name": str(meal.get("strMeal") or "Recipe idea")[:160],
                        "category": str(meal.get("strCategory") or "Dinner")[:60],
                        "area": str(meal.get("strArea") or "")[:60],
                        "image_url": str(meal.get("strMealThumb") or ""),
                        "recipe_url": source_url,
                        "ingredients": ingredients,
                        "missing": missing,
                        "available_count": len(available),
                        "ingredient_count": len(ingredients),
                        "stock_coverage": round(100 * len(available) / max(1, len(ingredients))),
                        "source": "TheMealDB",
                        "nutrition": "Not supplied by source; verify portions before logging.",
                    })
                    seen.add(meal_id)
                    if len(summaries) >= limit:
                        break
                if len(summaries) >= limit:
                    break
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {
            "ok": False,
            "source": "TheMealDB",
            "suggestions": [],
            "message": "Internet recipes are temporarily unavailable; Jarvis kept the local pantry-aware options online.",
            "detail": type(exc).__name__,
        }

    result = {
        "ok": True,
        "source": "TheMealDB",
        "source_url": "https://www.themealdb.com/",
        "seed_ingredients": seeds,
        "pantry_seeded": bool(pantry_seeds),
        "suggestions": sorted(summaries, key=lambda item: (-item["stock_coverage"], item["name"])),
        "message": f"{len(summaries)} fresh internet recipe ideas ready.",
    }
    with _lock:
        _cache.clear()
        _cache[cache_key] = (now, result)
    return result
