import json

import pytest
from fastapi import HTTPException

from app import retailer
from app.db import conn
from app.routers import retailer as retailer_router


def test_plan_prioritizes_market_list_and_low_stock(fresh_db, monkeypatch):
    with conn() as database:
        database.execute(
            "INSERT INTO grocery_list(item,shopping_type) VALUES(?,?)",
            ("Pepsi Cola", "food"),
        )
        database.execute(
            "INSERT INTO pantry(name,qty,low_stock_threshold) VALUES(?,?,?)",
            ("Greek yogurt", 0, 1),
        )

    monkeypatch.setattr(retailer, "_bridge", lambda *_args, **_kwargs: {
        "store": "1605 Way St",
        "products": [
            {"id": "11", "name": "Food Lion Greek Yogurt", "price": 2.5, "savings": 1, "deal_type": "sale"},
            {"id": "22", "name": "Pepsi Cola Soda 12 Pack", "price": 5.49, "savings": 5.49, "deal_type": "bogo"},
            {"id": "33", "name": "Paper Plates", "price": 3, "savings": 2, "deal_type": "sale"},
        ],
    })

    plan = retailer.build_deal_plan()
    assert [item["id"] for item in plan["products"][:2]] == ["22", "11"]
    assert plan["products"][0]["quantity"] == 2
    assert plan["recommended_count"] == 2
    assert plan["policy"]["checkout"] == "manual_only"


def test_cart_requires_explicit_confirmation(fresh_db):
    body = retailer_router.CartRequest(items=[{"id": "464", "quantity": 2}], confirmed=False)
    with pytest.raises(HTTPException) as exc:
        retailer_router.foodlion_cart(body)
    assert exc.value.status_code == 409


def test_confirmed_cart_is_audited_without_checkout(fresh_db, monkeypatch):
    monkeypatch.setattr(retailer_router, "apply_cart", lambda items: {"ok": True, "updated": len(items)})
    body = retailer_router.CartRequest(items=[{"id": "464", "quantity": 2}], confirmed=True)
    result = retailer_router.foodlion_cart(body)
    assert result["updated"] == 1
    assert result["checkout"] == "not_performed"
    with conn() as database:
        audit = database.execute("SELECT * FROM action_audit").fetchone()
    assert audit["action_id"] == "retailer.foodlion_add_to_cart"
    assert json.loads(audit["details_json"])["checkout"] == "not_performed"


def test_invalid_product_ids_never_reach_bridge(monkeypatch):
    monkeypatch.setattr(retailer, "_bridge", lambda *_args, **_kwargs: pytest.fail("bridge called"))
    with pytest.raises(ValueError, match="invalid product id"):
        retailer.apply_cart([{"id": "../../checkout", "quantity": 1}])
