import pytest
import re
from pathlib import Path
from fastapi import HTTPException

from app.chef import chef_summary, excluded_reason, market_items
from app.db import conn
from app.context_engine import lifeos_snapshot
from app.internet_recipes import discover_recipes
from app.routers.pantry import (
    ChefFeedbackIn,
    GroceryItemIn,
    MarketListIn,
    PantryItemIn,
    add_item,
    build_market_list,
    build_top_meal_market_list,
    chef_feedback,
    grocery_add,
    grocery_list,
    mark_item_out,
    mark_named_item_out,
)
from app.routers.insights import ask


def test_food_policy_blocks_exclusions_but_allows_sugar_free(fresh_db):
    assert excluded_reason("pineapple smoothie") == "pineapple"
    assert excluded_reason("honey glaze") == "honey"
    assert excluded_reason("zero sugar electrolyte packet") is None
    with pytest.raises(HTTPException, match="excluded"):
        grocery_add(GroceryItemIn(item="pineapple"))


def test_chef_uses_real_pantry_coverage(fresh_db):
    add_item(PantryItemIn(name="Tuna packets", qty=4, unit="packets", category="Canned Goods"))
    add_item(PantryItemIn(name="Green grapes", qty=1, unit="bunch", category="Produce"))
    add_item(PantryItemIn(name="Romaine", qty=1, unit="head", category="Produce"))
    add_item(PantryItemIn(name="Greek yogurt", qty=2, unit="cups", category="Dairy"))

    summary = chef_summary()
    first = summary["suggestions"][0]
    assert first["id"] == "tuna-grape-romaine"
    assert first["stock_coverage"] == 100
    assert first["missing"] == []
    assert summary["chef"] == "Jarvis"
    assert summary["policy"]["automatic_purchase"] is False


def test_marking_item_out_adds_it_to_market_list(fresh_db):
    add_item(PantryItemIn(name="Chicken breast", qty=2, unit="lb", category="Meat"))
    with conn() as c:
        item_id = c.execute("SELECT id FROM pantry WHERE name='Chicken breast'").fetchone()["id"]

    result = mark_item_out(item_id)
    assert result["added_to_market_list"] is True
    with conn() as c:
        pantry = c.execute("SELECT qty,last_depleted_at FROM pantry WHERE id=?", (item_id,)).fetchone()
    assert pantry["qty"] == 0
    assert pantry["last_depleted_at"]
    assert grocery_list()[0]["item"] == "Chicken breast"
    assert grocery_list()[0]["reason"] == "Marked out by Giovanni"
    snapshot = lifeos_snapshot()
    assert snapshot["food"]["out_of_stock"] == ["Chicken breast"]
    assert snapshot["food"]["market_list"][0]["item"] == "Chicken breast"
    with conn() as c:
        event = c.execute(
            "SELECT event_type,state FROM context_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert dict(event) == {"event_type": "pantry.depleted", "state": "out"}


def test_market_builder_is_local_review_not_checkout(fresh_db):
    result = build_market_list(MarketListIn(recipe_ids=["salmon-zucchini"], include_low_stock=False))
    assert result["ok"] is True
    assert result["added"] == 4
    assert "never places an order" in result["checkout"]
    assert "Planning estimates only" in result["pricing"]
    assert {item["item"] for item in grocery_list()} == {
        "Salmon", "Zucchini", "Green apple", "Avocado oil"
    }


def test_explicit_feedback_transparently_changes_ranking(fresh_db):
    before = chef_summary()["suggestions"][0]["id"]
    assert before == "tuna-grape-romaine"
    chef_feedback(ChefFeedbackIn(recipe_id="blackened-chicken-tenders", action="liked"))
    after = chef_summary()["suggestions"][0]
    assert after["id"] == "blackened-chicken-tenders"
    assert after["history"]["liked"] == 1
    assert "Explicit cooked, liked, and skipped" in chef_summary()["learning"]["method"]


def test_market_item_generation_deduplicates_recipe_and_low_stock(fresh_db):
    add_item(PantryItemIn(name="Green apple", qty=0, unit="each", category="Produce"))
    items = market_items(["blackened-chicken-tenders"], include_low_stock=True)
    assert [item["item"] for item in items].count("Green apple") == 1


def test_voice_depletion_updates_tracked_stock_or_adds_untracked_item(fresh_db):
    add_item(PantryItemIn(name="Greek yogurt", qty=2, unit="cups", category="Dairy"))
    tracked = mark_named_item_out(GroceryItemIn(item="greek yogurt"))
    untracked = mark_named_item_out(GroceryItemIn(item="lemons"))
    assert tracked["tracked"] is True
    assert untracked["tracked"] is False
    with conn() as c:
        qty = c.execute("SELECT qty FROM pantry WHERE name='Greek yogurt'").fetchone()["qty"]
    assert qty == 0
    assert {item["item"].lower() for item in grocery_list()} == {"greek yogurt", "lemons"}


def test_voice_answers_and_top_market_list_use_chef_engine(fresh_db):
    answer = ask()
    assert answer["meals"].startswith("Chef Jarvis recommends")
    assert "pantry items recorded" in answer["pantry"]
    result = build_top_meal_market_list()
    assert result["recipe"]
    assert result["checkout"].startswith("Jarvis never places an order")


def test_chef_frontend_ids_are_unique_and_resolved():
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    javascript = (static / "app.js").read_text(encoding="utf-8")
    ids = re.findall(r'id="([^"]+)"', html)
    references = re.findall(r'\$\("([^"]+)"\)', javascript)
    assert len(ids) == len(set(ids))
    dynamic_ids = {"photo-name", "photo-protein", "photo-calories", "photo-log-btn"}
    assert sorted(set(references) - set(ids) - dynamic_ids) == []


def test_internet_discovery_is_pantry_seeded_and_never_invents_nutrition(fresh_db, monkeypatch):
    add_item(PantryItemIn(name="Chicken breast", qty=2, unit="lb", category="Meat"))

    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): return None
        def json(self): return self.payload

    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def get(self, path, params):
            if path == "/filter.php":
                return Response({"meals": [{"idMeal": "77"}]})
            return Response({"meals": [{
                "idMeal": "77", "strMeal": "Herbed Chicken Plate",
                "strCategory": "Dinner", "strArea": "American",
                "strIngredient1": "Chicken breast", "strMeasure1": "8 oz",
                "strIngredient2": "Spinach", "strMeasure2": "2 cups",
                "strSource": "https://example.test/recipe",
            }]})

    monkeypatch.setattr("app.internet_recipes.httpx.Client", Client)
    result = discover_recipes(1)
    assert result["ok"] is True
    assert result["pantry_seeded"] is True
    assert result["suggestions"][0]["stock_coverage"] == 50
    assert result["suggestions"][0]["missing"][0]["name"] == "Spinach"
    assert "verify portions" in result["suggestions"][0]["nutrition"]
