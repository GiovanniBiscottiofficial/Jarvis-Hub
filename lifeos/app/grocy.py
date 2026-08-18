"""Grocy client: pull pantry stock into LifeOS.

Configure with GROCY_URL (e.g. http://grocy:80) and GROCY_API_KEY
(Grocy -> wrench icon -> Manage API keys).
"""
import os

import httpx

GROCY_URL = os.environ.get("GROCY_URL", "")
GROCY_API_KEY = os.environ.get("GROCY_API_KEY", "")


def configured() -> bool:
    return bool(GROCY_URL and GROCY_API_KEY)


def fetch_stock() -> list[dict]:
    """Current stock: [{product_id, name, amount, unit}]."""
    headers = {"GROCY-API-KEY": GROCY_API_KEY}
    with httpx.Client(base_url=GROCY_URL, headers=headers, timeout=10) as client:
        stock = client.get("/api/stock").raise_for_status().json()
        units = {
            u["id"]: u["name"]
            for u in client.get("/api/objects/quantity_units")
            .raise_for_status()
            .json()
        }
    out = []
    for item in stock:
        product = item.get("product") or {}
        out.append(
            {
                "product_id": item.get("product_id"),
                "name": product.get("name", f"product {item.get('product_id')}"),
                "amount": float(item.get("amount", 0)),
                "unit": units.get(product.get("qu_id_stock"), ""),
            }
        )
    return out
