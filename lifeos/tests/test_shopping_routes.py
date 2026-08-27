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
    assert "encodeURIComponent(item)" in javascript
    assert 'link.rel = "noopener noreferrer"' in javascript
    assert 'link.target = "_top"' in javascript
    assert re.search(r"\.retailer-link\s*\{[^}]*min-height:\s*44px", css)
    assert re.search(
        r"\.shopping-composer input,\.shopping-composer select,\.shopping-composer button,"
        r"\.shopping-card \.market-heading button\s*\{[^}]*min-height:\s*44px",
        css,
    )


def test_retailer_access_has_exactly_four_top_level_allowlisted_launchers():
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    javascript = (static / "app.js").read_text(encoding="utf-8")
    todo = html.split('id="todo"', 1)[1].split('id="budget"', 1)[0]

    assert todo.count('class="retailer-airlock"') == 4
    for retailer, url in {
        "foodlion": "https://foodlion.com/",
        "instacart": "https://www.instacart.com/",
        "walmart": "https://www.walmart.com/",
        "amazon": "https://www.amazon.com/",
    }.items():
        assert f'data-retailer="{retailer}" href="{url}" target="_top"' in todo
    assert todo.count("Sign in / Shop") == 4
    assert "Account session stays in Chromium on this X1" in todo
    assert "Jarvis never sees credentials or payment details" in todo
    assert 'class="todo-boundaries" role="group"' in todo
    assert 'class="retailer-airlocks" role="group"' in todo
    assert 'class="shopping-composer" role="group"' in todo
    assert "RETAILER_ALLOWLIST" in javascript
    market_code = javascript.split("function renderMarketList", 1)[1].split("async function loadPantry", 1)[0]
    assert "window.open" not in market_code


def test_retailer_portal_has_no_account_or_checkout_capture_surface():
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    combined = "\n".join(
        (static / name).read_text(encoding="utf-8")
        for name in ("index.html", "app.js")
    ).lower()
    for forbidden in (
        'type="password"', "credentialmanagement", "navigator.credentials",
        "localstorage.setitem", "sessionstorage.setitem", "place order",
        "add to cart", "payment method", "mfa code",
    ):
        assert forbidden not in combined


def test_shopping_ui_lives_only_in_todo_and_supports_allowlisted_query_activation():
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    javascript = (static / "app.js").read_text(encoding="utf-8")
    body = html.split('id="body"', 1)[1].split('id="todo"', 1)[0]
    todo = html.split('id="todo"', 1)[1].split('id="budget"', 1)[0]

    for element_id in ("market-build-btn", "shopping-item", "shopping-type", "shopping-add-btn", "shopping-status", "grocery"):
        assert html.count(f'id="{element_id}"') == 1
        assert f'id="{element_id}"' not in body
        assert f'id="{element_id}"' in todo
    assert 'new Set(["command", "today", "internet", "body", "todo", "budget", "learning", "review"])' in javascript
    assert 'ALLOWED_PANELS.has(requestedPanel) ? requestedPanel : "today"' in javascript
    assert 'if (panelName === "todo") loadShopping();' in javascript
    assert 'rail.clientWidth - chevronWidth' in javascript
    assert 'nav.scrollTo({ left:' in javascript


def test_home_assistant_wrapper_exposes_only_the_lifeos_todo_system():
    repo = Path(__file__).resolve().parents[2]
    wrapper = (repo / "ha-config" / "www" / "lifeos.html").read_text(encoding="utf-8")
    dashboard = (repo / "ha-config" / "dashboards" / "jarvis.yaml").read_text(encoding="utf-8")

    assert "requestedTab === 'todo' ? '&tab=todo' : ''" in wrapper
    assert "token" not in wrapper.split("app.src =", 1)[1]
    assert "localStorage" not in wrapper and "sessionStorage" not in wrapper
    assert "title: To-do" in dashboard
    assert "entity: todo.shopping_list" not in dashboard
    assert "entity: todo.errands" not in dashboard
    assert "type: todo-list" not in dashboard
    assert "url: /local/lifeos.html?tab=todo&v=" in dashboard
    assert "navigation_path: /local/open.html?port=9283&path=/" in dashboard
    configuration = (repo / "ha-config" / "configuration.yaml").read_text(encoding="utf-8")
    assert "\nshopping_list:" not in configuration
