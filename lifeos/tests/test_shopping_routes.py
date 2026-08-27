import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers.pantry import (
    GroceryItemIn,
    ShoppingTypeIn,
    build_market_list,
    classify_shopping_type,
    grocery_add,
    grocery_list,
    grocery_set_type,
    MarketListIn,
)


def test_classifier_routes_food_and_non_food_to_distinct_store_pairs():
    assert classify_shopping_type("whole milk")[0] == "food"
    assert classify_shopping_type("chicken breast", "Meat")[0] == "food"
    assert classify_shopping_type("toilet paper")[0] == "home"
    assert classify_shopping_type("water filter")[0] == "home"
    assert classify_shopping_type("phone charger")[0] == "home"
    assert classify_shopping_type("olive oil")[0] == "food"


def test_explicit_route_is_durable_and_can_be_corrected(fresh_db):
    grocery_add(GroceryItemIn(item="sparkling water", shopping_type="food"))
    row = grocery_list()[0]
    assert row["shopping_type"] == "food"
    assert row["shopping_type_source"] == "explicit"

    grocery_set_type(row["id"], ShoppingTypeIn(shopping_type="home"))
    assert grocery_list()[0]["shopping_type"] == "home"

    grocery_add(GroceryItemIn(item="sparkling water", shopping_type="food"))
    assert grocery_list()[0]["shopping_type"] == "food"


def test_recipe_market_items_are_always_food(fresh_db):
    build_market_list(MarketListIn(recipe_ids=["salmon-zucchini"], include_low_stock=False))
    rows = grocery_list()
    assert rows
    assert {row["shopping_type"] for row in rows} == {"food"}


def test_household_item_does_not_inherit_food_exclusions(fresh_db):
    result = grocery_add(GroceryItemIn(item="honey hand soap", shopping_type="home"))
    assert result["ok"] is True
    with pytest.raises(HTTPException, match="excluded"):
        grocery_add(GroceryItemIn(item="honey", shopping_type="food"))


def test_missing_route_change_returns_not_found(fresh_db):
    with pytest.raises(HTTPException) as exc:
        grocery_set_type(404, ShoppingTypeIn(shopping_type="food"))
    assert exc.value.status_code == 404


def test_shopping_ui_uses_allowlisted_review_only_retailer_links():
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    javascript = (static / "app.js").read_text(encoding="utf-8")
    css = (static / "style.css").read_text(encoding="utf-8")

    assert 'id="shopping-item"' in html
    assert 'id="shopping-type"' in html
    assert 'id="shopping-add-btn"' in html
    assert "REVIEW-ONLY CHECKOUT" in html
    assert "https://foodlion.com/personal-list" in javascript
    assert "https://www.instacart.com/store/s?k=" in javascript
    assert "https://www.walmart.com/search?q=" in javascript
    assert "https://www.amazon.com/s?k=" in javascript
    assert "encodeURIComponent(item.item)" in javascript
    assert 'link.rel = "noopener noreferrer"' in javascript
    assert re.search(r"\.retailer-link\s*\{[^}]*min-height:\s*44px", css)
