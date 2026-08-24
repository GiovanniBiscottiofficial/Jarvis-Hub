"""Chef Jarvis: local, pantry-aware meal planning and transparent learning."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from .db import active_profile, conn


# These are Giovanni's explicit exclusions. "Sugar-free" and "zero sugar"
# products remain valid; the policy is intended to exclude added sweeteners.
EXCLUDED_FOODS = {
    "pineapple", "crab", "lobster", "hummus", "quinoa", "jerky",
    "meat stick", "slaw", "coleslaw", "cabbage slaw", "syrup", "honey",
    "broth", "soup", "soda", "fruit juice", "agave", "cane sugar",
    "molasses", "corn syrup", "dextrose",
}


RECIPES = [
    {
        "id": "blackened-chicken-tenders",
        "name": "Air-Fryer Blackened Chicken Tenders",
        "minutes": 14,
        "protein_g": 50,
        "calories": 430,
        "tier": "Fast",
        "ingredients": [
            {"name": "Chicken breast", "qty": 1, "unit": "lb", "department": "Meat", "aliases": ["chicken", "chicken tenders"]},
            {"name": "Green apple", "qty": 1, "unit": "each", "department": "Produce", "aliases": ["apple", "granny smith"]},
            {"name": "Cucumber", "qty": 1, "unit": "each", "department": "Produce", "aliases": []},
            {"name": "Avocado oil", "qty": 1, "unit": "bottle", "department": "Pantry", "aliases": ["olive oil"]},
        ],
        "steps": ["Season the chicken with salt, pepper, paprika, garlic, and cayenne.", "Air-fry at 390°F until the thickest piece reaches 165°F.", "Serve with sliced green apple and cucumber."],
    },
    {
        "id": "tuna-grape-romaine",
        "name": "Tuna & Green Grape Romaine Cups",
        "minutes": 6,
        "protein_g": 44,
        "calories": 360,
        "tier": "Immediate",
        "ingredients": [
            {"name": "Tuna packets", "qty": 2, "unit": "packets", "department": "Canned Goods", "aliases": ["tuna", "canned tuna"]},
            {"name": "Green grapes", "qty": 1, "unit": "bunch", "department": "Produce", "aliases": ["grapes"]},
            {"name": "Romaine", "qty": 1, "unit": "head", "department": "Produce", "aliases": ["romaine lettuce", "lettuce"]},
            {"name": "Greek yogurt", "qty": 1, "unit": "cup", "department": "Dairy", "aliases": ["plain greek yogurt"]},
        ],
        "steps": ["Mix tuna with plain Greek yogurt, pepper, and a squeeze of lemon.", "Fold in halved green grapes.", "Spoon into romaine leaves."],
    },
    {
        "id": "sirloin-asparagus",
        "name": "Seared Sirloin with Asparagus & Grapes",
        "minutes": 25,
        "protein_g": 52,
        "calories": 520,
        "tier": "Balanced",
        "ingredients": [
            {"name": "Sirloin steak", "qty": 1, "unit": "lb", "department": "Meat", "aliases": ["sirloin", "steak"]},
            {"name": "Asparagus", "qty": 1, "unit": "bunch", "department": "Produce", "aliases": []},
            {"name": "Green grapes", "qty": 1, "unit": "bunch", "department": "Produce", "aliases": ["grapes"]},
            {"name": "Avocado oil", "qty": 1, "unit": "bottle", "department": "Pantry", "aliases": ["olive oil"]},
        ],
        "steps": ["Pat the sirloin dry, season, and sear to your preferred doneness.", "Rest the steak while the asparagus cooks in the same pan.", "Serve with a small side of green grapes."],
    },
    {
        "id": "salmon-zucchini",
        "name": "Roasted Salmon with Zucchini & Apple",
        "minutes": 35,
        "protein_g": 48,
        "calories": 490,
        "tier": "Prepared",
        "ingredients": [
            {"name": "Salmon", "qty": 1, "unit": "lb", "department": "Seafood", "aliases": ["salmon fillet"]},
            {"name": "Zucchini", "qty": 2, "unit": "each", "department": "Produce", "aliases": []},
            {"name": "Green apple", "qty": 1, "unit": "each", "department": "Produce", "aliases": ["apple", "granny smith"]},
            {"name": "Avocado oil", "qty": 1, "unit": "bottle", "department": "Pantry", "aliases": ["olive oil"]},
        ],
        "steps": ["Heat the oven to 400°F.", "Roast seasoned salmon and zucchini until the salmon flakes and reaches 145°F.", "Serve with sliced green apple."],
    },
]


# Planning estimates from the supplied prototype. They are not live prices,
# promotions, or a promise that Food Lion has the item in stock.
FOOD_LION_ESTIMATES = {
    "chicken breast": 5.49, "green apple": 1.25, "cucumber": 0.89,
    "avocado oil": 8.99, "tuna packets": 2.29, "green grapes": 4.49,
    "romaine": 3.49, "greek yogurt": 1.49, "sirloin steak": 10.99,
    "asparagus": 3.49, "salmon": 10.99, "zucchini": 1.79,
}


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def excluded_reason(value: str) -> str | None:
    normalized = normalize(value)
    if "sugar free" in normalized or "zero sugar" in normalized:
        normalized = normalized.replace("sugar free", "").replace("zero sugar", "")
    for term in sorted(EXCLUDED_FOODS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(term)}s?\b", normalized):
            return term
    if re.search(r"\b(?:added )?sugar\b", normalized):
        return "added sugar"
    return None


def _has_ingredient(stock: dict[str, float], ingredient: dict) -> bool:
    candidates = [ingredient["name"], *ingredient.get("aliases", [])]
    normalized = [normalize(candidate) for candidate in candidates]
    return any(
        qty > 0 and any(name == candidate or candidate in name or name in candidate for candidate in normalized)
        for name, qty in stock.items()
    )


def _feedback(profile_id: int) -> dict[str, dict[str, int]]:
    with conn() as c:
        rows = c.execute(
            "SELECT recipe_id,action,COUNT(*) n FROM chef_feedback"
            " WHERE profile_id=? GROUP BY recipe_id,action",
            (profile_id,),
        ).fetchall()
    result: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        result[row["recipe_id"]][row["action"]] = row["n"]
    return result


def chef_summary(max_minutes: int = 45) -> dict:
    with conn() as c:
        profile = active_profile(c)
        items = [dict(row) for row in c.execute("SELECT * FROM pantry ORDER BY name").fetchall()]
    stock = {normalize(item["name"]): float(item["qty"]) for item in items}
    feedback = _feedback(profile["id"])
    ranked = []
    for recipe in RECIPES:
        if recipe["minutes"] > max_minutes:
            continue
        unsafe = [reason for value in [recipe["name"], *[i["name"] for i in recipe["ingredients"]]] if (reason := excluded_reason(value))]
        if unsafe:
            continue
        available = [item for item in recipe["ingredients"] if _has_ingredient(stock, item)]
        missing = [item for item in recipe["ingredients"] if item not in available]
        coverage = len(available) / max(1, len(recipe["ingredients"]))
        history = feedback.get(recipe["id"], {})
        preference = history.get("liked", 0) * 8 + history.get("cooked", 0) * 2 - history.get("skipped", 0) * 5
        score = coverage * 60 + min(recipe["protein_g"], 60) * .35 + max(0, 25 - recipe["minutes"]) * .35 + preference
        item = {key: value for key, value in recipe.items() if key != "ingredients"}
        item.update({
            "ingredients": recipe["ingredients"],
            "available_count": len(available),
            "ingredient_count": len(recipe["ingredients"]),
            "stock_coverage": round(coverage * 100),
            "missing": missing,
            "score": round(score, 1),
            "history": dict(history),
            "why": (
                "Everything is in stock."
                if not missing else
                f"{len(available)} of {len(recipe['ingredients'])} ingredients are in stock; {len(missing)} missing."
            ),
        })
        ranked.append(item)
    ranked.sort(key=lambda item: (-item["score"], item["minutes"], item["name"]))
    depleted = [item for item in items if float(item["qty"]) <= 0]
    low = [
        item for item in items
        if 0 < float(item["qty"]) <= float(item.get("low_stock_threshold") or 1)
    ]
    return {
        "chef": "Jarvis",
        "profile": profile["name"],
        "suggestions": ranked[:4],
        "pantry": {"items": len(items), "out": len(depleted), "low": len(low)},
        "learning": {
            "enabled": True,
            "method": "Explicit cooked, liked, and skipped feedback changes recipe ranking.",
            "privacy": "Stored locally in LifeOS for the active profile.",
        },
        "market": {
            "store": "Food Lion",
            "pricing": "Planning estimates only — confirm current price, MVP deal, and stock before checkout.",
        },
        "policy": {"excluded_foods": sorted(EXCLUDED_FOODS), "automatic_purchase": False},
    }


def recipe_by_id(recipe_id: str) -> dict | None:
    return next((recipe for recipe in RECIPES if recipe["id"] == recipe_id), None)


def estimate_for(item: str) -> float | None:
    normalized = normalize(item)
    return FOOD_LION_ESTIMATES.get(normalized)


def market_items(recipe_ids: Iterable[str], include_low_stock: bool = True) -> list[dict]:
    with conn() as c:
        pantry = [dict(row) for row in c.execute("SELECT * FROM pantry").fetchall()]
    stock = {normalize(item["name"]): float(item["qty"]) for item in pantry}
    requested: dict[str, dict] = {}
    for recipe_id in recipe_ids:
        recipe = recipe_by_id(recipe_id)
        if not recipe:
            continue
        for ingredient in recipe["ingredients"]:
            if _has_ingredient(stock, ingredient):
                continue
            key = normalize(ingredient["name"])
            requested[key] = {
                "item": ingredient["name"], "qty": ingredient["qty"],
                "unit": ingredient["unit"], "department": ingredient["department"],
                "reason": f"Needed for {recipe['name']}", "recipe_id": recipe_id,
                "estimated_price": estimate_for(ingredient["name"]),
            }
    if include_low_stock:
        for item in pantry:
            if float(item["qty"]) > float(item.get("low_stock_threshold") or 1):
                continue
            key = normalize(item["name"])
            requested.setdefault(key, {
                "item": item["name"], "qty": max(1, float(item.get("low_stock_threshold") or 1)),
                "unit": item["unit"], "department": item.get("category") or "Other",
                "reason": "Out of stock" if float(item["qty"]) <= 0 else "Low stock",
                "recipe_id": None, "estimated_price": estimate_for(item["name"]),
            })
    return sorted(requested.values(), key=lambda item: (item["department"], item["item"]))
