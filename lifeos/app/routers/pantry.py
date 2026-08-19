"""Pantry: local inventory mirror of Grocy + grocery suggestions from
protein deficits."""
from datetime import date, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from .. import grocy
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


class GroceryItemIn(BaseModel):
    item: str


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


@router.post("/items")
def add_item(body: PantryItemIn):
    with conn() as c:
        c.execute(
            "INSERT INTO pantry(name,qty,unit,protein_g_per_serving)"
            " VALUES(?,?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET qty=excluded.qty,"
            " unit=excluded.unit",
            (body.name, body.qty, body.unit, body.protein_g_per_serving),
        )
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
