"""Pantry: local inventory mirror of Grocy + grocery suggestions from
protein deficits."""
import json
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import grocy
from ..chef import chef_summary, excluded_reason, market_items, recipe_by_id
from ..db import active_profile, conn

router = APIRouter(prefix="/api/pantry", tags=["pantry"])

HIGH_PROTEIN_STAPLES = [
    ("Chicken breast", 31.0),
    ("Ground turkey", 27.0),
    ("Eggs", 6.0),
    ("Greek yogurt", 18.0),
    ("Cottage cheese", 22.0),
    ("Tuna packets", 25.0),
    ("Protein powder", 30.0),
    ("Sweet potatoes", 2.0),
]


class PantryItemIn(BaseModel):
    name: str
    qty: float = 1
    unit: str = ""
    protein_g_per_serving: float = 0
    category: str = "other"
    low_stock_threshold: float = 1


class GroceryItemIn(BaseModel):
    item: str


class MarketListIn(BaseModel):
    recipe_ids: list[str] = Field(default_factory=list)
    include_low_stock: bool = True


class ChefFeedbackIn(BaseModel):
    recipe_id: str
    action: str


@router.get("/grocery")
def grocery_list():
    with conn() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM grocery_list WHERE done=0 ORDER BY ts"
            ).fetchall()
        ]


@router.post("/grocery")
def grocery_add(body: GroceryItemIn):
    item = body.item.strip()
    if not item:
        raise HTTPException(400, "grocery item is required")
    if reason := excluded_reason(item):
        raise HTTPException(400, f"{reason} is excluded from Giovanni's food plan")
    with conn() as c:
        existing = c.execute(
            "SELECT id FROM grocery_list WHERE done=0"
            " AND item=? COLLATE NOCASE",
            (item,),
        ).fetchone()
        if existing is None:
            c.execute("INSERT INTO grocery_list(item) VALUES(?)", (item,))
        return {"ok": True, "item": item}


@router.post("/grocery/remove")
def grocery_remove(body: GroceryItemIn):
    item = body.item.strip()
    with conn() as c:
        c.execute(
            "UPDATE grocery_list SET done=1 WHERE done=0"
            " AND (item=? COLLATE NOCASE"
            " OR item LIKE ? COLLATE NOCASE)",
            (item, f"%{item}%"),
        )
        return {"ok": True, "item": item}


@router.post("/grocery/clear")
def grocery_clear():
    with conn() as c:
        c.execute("UPDATE grocery_list SET done=1 WHERE done=0")
        return {"ok": True}


@router.get("")
def list_pantry():
    with conn() as c:
        items = [dict(r) for r in c.execute("SELECT * FROM pantry").fetchall()]
    return {"grocy_configured": grocy.configured(), "items": items}


@router.get("/chef")
def chef(max_minutes: int = 45):
    """Jarvis's pantry-aware recommendations and explainable ranking."""
    return chef_summary(max(5, min(max_minutes, 120)))


@router.post("/items")
def add_item(body: PantryItemIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "item name is required")
    if body.qty < 0 or body.protein_g_per_serving < 0:
        raise HTTPException(400, "quantity and protein cannot be negative")
    if body.low_stock_threshold < 0:
        raise HTTPException(400, "low-stock threshold cannot be negative")
    with conn() as c:
        c.execute(
            "INSERT INTO pantry(name,qty,unit,protein_g_per_serving,category,"
            "low_stock_threshold,updated_at) VALUES(?,?,?,?,?,?,datetime('now','localtime'))"
            " ON CONFLICT(name) DO UPDATE SET qty=excluded.qty,"
            " unit=excluded.unit,protein_g_per_serving=excluded.protein_g_per_serving,"
            " category=excluded.category,low_stock_threshold=excluded.low_stock_threshold,"
            " updated_at=datetime('now','localtime'),"
            " last_depleted_at=CASE WHEN excluded.qty<=0 THEN datetime('now','localtime')"
            " ELSE pantry.last_depleted_at END",
            (name, body.qty, body.unit.strip(), body.protein_g_per_serving,
             body.category.strip() or "other", body.low_stock_threshold),
        )
        return {"ok": True, "item": name}


@router.post("/items/{item_id}/out")
def mark_item_out(item_id: int):
    """Record a depletion and put the item on the local market list."""
    with conn() as c:
        item = c.execute("SELECT * FROM pantry WHERE id=?", (item_id,)).fetchone()
        if item is None:
            raise HTTPException(404, "pantry item not found")
        c.execute(
            "UPDATE pantry SET qty=0,updated_at=datetime('now','localtime'),"
            "last_depleted_at=datetime('now','localtime') WHERE id=?", (item_id,),
        )
        c.execute(
            "INSERT INTO grocery_list(item,qty,unit,source,reason,department)"
            " SELECT ?,MAX(1,?),?,'pantry','Marked out by Giovanni',?"
            " WHERE NOT EXISTS (SELECT 1 FROM grocery_list WHERE done=0"
            " AND item=? COLLATE NOCASE)",
            (item["name"], item["low_stock_threshold"], item["unit"],
             item["category"] or "Other", item["name"]),
        )
        c.execute(
            "INSERT INTO context_events(source,event_type,entity_id,state,"
            "previous_state,attributes_json) VALUES(?,?,?,?,?,?)",
            ("lifeos", "pantry.depleted", f"pantry.{item_id}", "out",
             str(item["qty"]), json.dumps({"item": item["name"], "market_list": True})),
        )
    return {"ok": True, "item": item["name"], "added_to_market_list": True}


@router.post("/items/out")
def mark_named_item_out(body: GroceryItemIn):
    """Voice-friendly depletion: resolve a pantry name, or add an untracked item."""
    name = body.item.strip()
    if not name:
        raise HTTPException(400, "item name is required")
    with conn() as c:
        item = c.execute(
            "SELECT id FROM pantry WHERE name=? COLLATE NOCASE",
            (name,),
        ).fetchone()
    if item:
        result = mark_item_out(item["id"])
        result["tracked"] = True
        return result
    grocery_add(GroceryItemIn(item=name))
    with conn() as c:
        c.execute(
            "INSERT INTO context_events(source,event_type,state,attributes_json)"
            " VALUES('lifeos','pantry.depletion_reported','untracked',?)",
            (json.dumps({"item": name, "market_list": True}),),
        )
    return {
        "ok": True, "item": name, "tracked": False,
        "added_to_market_list": True,
    }


@router.post("/market-list/build")
def build_market_list(body: MarketListIn):
    invalid = [recipe_id for recipe_id in body.recipe_ids if recipe_by_id(recipe_id) is None]
    if invalid:
        raise HTTPException(400, f"unknown recipe: {invalid[0]}")
    items = market_items(body.recipe_ids, body.include_low_stock)
    added = 0
    with conn() as c:
        for item in items:
            added += c.execute(
                "INSERT INTO grocery_list(item,qty,unit,source,reason,department,"
                "estimated_price,recipe_id) SELECT ?,?,?,?,?,?,?,? WHERE NOT EXISTS"
                " (SELECT 1 FROM grocery_list WHERE done=0 AND item=? COLLATE NOCASE)",
                (item["item"], item["qty"], item["unit"], "chef_jarvis",
                 item["reason"], item["department"], item["estimated_price"],
                 item["recipe_id"], item["item"]),
            ).rowcount
        c.execute(
            "INSERT INTO context_events(source,event_type,state,attributes_json)"
            " VALUES('lifeos','market_list.built','review_required',?)",
            (json.dumps({"recipe_ids": body.recipe_ids, "items_added": added,
                         "automatic_purchase": False}),),
        )
    return {
        "ok": True, "added": added, "items": items,
        "pricing": "Planning estimates only — confirm current Food Lion price and stock.",
        "checkout": "Jarvis never places an order without Giovanni's explicit review.",
    }


@router.post("/market-list/top")
def build_top_meal_market_list():
    suggestions = chef_summary()["suggestions"]
    if not suggestions:
        raise HTTPException(409, "Jarvis needs pantry inventory before choosing a meal")
    recipe = suggestions[0]
    result = build_market_list(
        MarketListIn(recipe_ids=[recipe["id"]], include_low_stock=True)
    )
    result["recipe"] = recipe["name"]
    return result


@router.post("/chef/feedback")
def chef_feedback(body: ChefFeedbackIn):
    if recipe_by_id(body.recipe_id) is None:
        raise HTTPException(404, "recipe not found")
    if body.action not in {"liked", "cooked", "skipped"}:
        raise HTTPException(400, "action must be liked, cooked, or skipped")
    with conn() as c:
        c.execute(
            "INSERT INTO chef_feedback(recipe_id,action,profile_id) VALUES(?,?,?)",
            (body.recipe_id, body.action, active_profile(c)["id"]),
        )
        c.execute(
            "INSERT INTO context_events(source,event_type,entity_id,state,attributes_json)"
            " VALUES('lifeos','chef.feedback',?,?,?)",
            (f"recipe.{body.recipe_id}", body.action,
             json.dumps({"recipe_id": body.recipe_id, "learning": "explicit"})),
        )
    return {"ok": True, "learned": body.action, "recipe_id": body.recipe_id}


@router.delete("/items/{item_id}")
def remove_item(item_id: int):
    with conn() as c:
        cur = c.execute("DELETE FROM pantry WHERE id=?", (item_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "pantry item not found")
        return {"ok": True}


@router.post("/sync")
def sync_from_grocy():
    if not grocy.configured():
        return {
            "ok": False,
            "message": "Set GROCY_URL and GROCY_API_KEY on the lifeos "
            "container to enable sync.",
        }
    stock = grocy.fetch_stock()
    with conn() as c:
        for item in stock:
            c.execute(
                "INSERT INTO pantry(name,qty,unit,grocy_product_id)"
                " VALUES(?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET qty=excluded.qty,"
                " unit=excluded.unit, grocy_product_id=excluded.grocy_product_id",
                (item["name"], item["amount"], item["unit"],
                 item["product_id"]),
            )
    return {"ok": True, "synced": len(stock)}


@router.get("/grocery-suggestions")
def grocery_suggestions():
    """Suggest high-protein staples to buy, based on the last 7 days'
    average protein deficit and what's already in the pantry."""
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    with conn() as c:
        prof = active_profile(c)
        rows = c.execute(
            "SELECT date(ts) d, SUM(protein_g) p FROM meal_log"
            " WHERE date(ts)>=? AND profile_id=? GROUP BY date(ts)",
            (week_ago, prof["id"]),
        ).fetchall()
        in_stock = {
            r["name"].lower()
            for r in c.execute("SELECT name FROM pantry WHERE qty>0").fetchall()
        }
    daily = [r["p"] for r in rows]
    avg = sum(daily) / len(daily) if daily else 0.0
    deficit = max(0.0, prof["protein_target_g"] - avg)
    suggestions = [
        {"name": name, "protein_g_per_serving": protein}
        for name, protein in HIGH_PROTEIN_STAPLES
        if name.lower() not in in_stock
    ]
    if deficit < 10:
        suggestions = suggestions[:3]
    return {
        "avg_daily_protein_g": round(avg, 1),
        "target_g": prof["protein_target_g"],
        "avg_deficit_g": round(deficit, 1),
        "suggestions": suggestions,
    }
