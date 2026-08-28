"""Review-first retailer intelligence backed by the X1 Chromium session."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .db import conn


BRIDGE_URL = os.environ.get("RETAILER_BRIDGE_URL", "http://host.docker.internal:8766").rstrip("/")
BRIDGE_SECRET = os.environ.get("RETAILER_BRIDGE_SECRET", "").strip()


class RetailerUnavailable(RuntimeError):
    pass


def _bridge(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not BRIDGE_SECRET:
        raise RetailerUnavailable("Food Lion bridge is not commissioned")
    try:
        response = httpx.request(
            method,
            f"{BRIDGE_URL}{path}",
            headers={"X-Jarvis-Bridge-Secret": BRIDGE_SECRET},
            json=payload,
            timeout=35,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RetailerUnavailable("The signed-in Food Lion session is unavailable") from exc
    if not isinstance(result, dict):
        raise RetailerUnavailable("Food Lion returned an invalid response")
    return result


def bridge_status() -> dict[str, Any]:
    if not BRIDGE_SECRET:
        return {"ready": False, "state": "not_commissioned", "message": "Food Lion bridge is not commissioned"}
    try:
        data = _bridge("GET", "/status")
        return {
            "ready": bool(data.get("ready")),
            "state": "ready" if data.get("ready") else "signed_out",
            "message": data.get("message") or "Food Lion session detected",
            "store": data.get("store"),
        }
    except RetailerUnavailable as exc:
        return {"ready": False, "state": "offline", "message": str(exc)}


def _tokens(value: str) -> set[str]:
    ignored = {"and", "or", "the", "a", "an", "pack", "packs", "oz", "lb", "lbs"}
    return {
        token for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 1 and token not in ignored
    }


def _need_inventory() -> list[dict[str, Any]]:
    with conn() as database:
        groceries = [
            dict(row) for row in database.execute(
                "SELECT id,item,qty,unit,reason,department FROM grocery_list"
                " WHERE done=0 AND COALESCE(shopping_type,'auto')!='home' ORDER BY ts"
            ).fetchall()
        ]
        pantry = [
            dict(row) for row in database.execute(
                "SELECT name,qty,low_stock_threshold FROM pantry"
                " WHERE qty<=low_stock_threshold ORDER BY qty,name"
            ).fetchall()
        ]
    return [
        {"name": row["item"], "source": "market_list", "priority": 3, **row}
        for row in groceries
    ] + [
        {"name": row["name"], "source": "pantry_low", "priority": 2, **row}
        for row in pantry
    ]


def build_deal_plan(limit: int = 12) -> dict[str, Any]:
    scan = _bridge("POST", "/scan", {"department": "bogo"})
    products = scan.get("products") if isinstance(scan.get("products"), list) else []
    needs = _need_inventory()
    ranked: list[dict[str, Any]] = []
    for raw in products:
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
            continue
        product = dict(raw)
        product_tokens = _tokens(str(product["name"]))
        matches = []
        for need in needs:
            need_tokens = _tokens(str(need["name"]))
            overlap = product_tokens & need_tokens
            if overlap and (len(overlap) >= min(2, len(need_tokens)) or need_tokens <= product_tokens):
                matches.append(need)
        savings = float(product.get("savings") or 0)
        relevance = max((int(match["priority"]) for match in matches), default=0)
        score = relevance * 100 + min(savings, 50)
        product.update({
            "matches": [{"name": item["name"], "source": item["source"]} for item in matches],
            "recommended": bool(matches),
            "score": score,
            "quantity": 2 if product.get("deal_type") == "bogo" else 1,
        })
        ranked.append(product)
    ranked.sort(key=lambda item: (-item["score"], str(item["name"])))
    selected = ranked[: max(1, min(limit, 40))]
    return {
        "ok": True,
        "retailer": "Food Lion",
        "store": scan.get("store"),
        "scanned": len(products),
        "needs": len(needs),
        "products": selected,
        "recommended_count": sum(1 for item in selected if item["recommended"]),
        "policy": {
            "scan": "read_only",
            "cart": "explicit_confirmation",
            "checkout": "manual_only",
            "credentials": "remain_in_chromium",
        },
    }


def apply_cart(items: list[dict[str, Any]]) -> dict[str, Any]:
    clean = []
    for item in items:
        product_id = str(item.get("id") or "").strip()
        quantity = int(item.get("quantity") or 0)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", product_id):
            raise ValueError("invalid product id")
        if not 1 <= quantity <= 12:
            raise ValueError("quantity must be between 1 and 12")
        clean.append({"id": product_id, "quantity": quantity})
    if not clean or len(clean) > 30:
        raise ValueError("select between 1 and 30 products")
    return _bridge("POST", "/cart", {"items": clean, "confirmed": True})
