"""Authenticated retailer planning and explicitly confirmed cart actions."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import conn
from ..retailer import RetailerUnavailable, apply_cart, bridge_status, build_deal_plan


router = APIRouter(prefix="/api/retailer", tags=["retailer"])


class CartItem(BaseModel):
    id: str
    quantity: int = Field(ge=1, le=12)


class CartRequest(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=30)
    confirmed: bool = False


def _audit(outcome: str, details: dict[str, Any]) -> None:
    with conn() as database:
        database.execute(
            "INSERT INTO action_audit(action_id,requested_by,outcome,details_json) VALUES(?,?,?,?)",
            ("retailer.foodlion_add_to_cart", "giovanni", outcome, json.dumps(details, separators=(",", ":"))),
        )


@router.get("/foodlion/status")
def foodlion_status():
    return bridge_status()


@router.post("/foodlion/plan")
def foodlion_plan(limit: int = 12):
    try:
        return build_deal_plan(limit)
    except RetailerUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/foodlion/cart")
def foodlion_cart(body: CartRequest):
    if not body.confirmed:
        raise HTTPException(409, "explicit confirmation is required")
    requested = [item.model_dump() for item in body.items]
    try:
        result = apply_cart(requested)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RetailerUnavailable as exc:
        _audit("failed", {"items": requested, "reason": str(exc)})
        raise HTTPException(503, str(exc)) from exc
    details = {"items": requested, "updated": result.get("updated", 0), "checkout": "not_performed"}
    _audit("executed", details)
    return {"ok": True, **details, "message": "Food Lion cart updated. Review substitutions and checkout manually."}
