"""Inbound webhooks: Health Auto Export (Apple Watch / HealthKit / smart scale)."""
from datetime import date
import hashlib
import hmac
import json
import os
import time

from fastapi import APIRouter, HTTPException, Request

from ..db import active_profile, conn

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

KG_TO_LB = 2.20462
PROCESSED_EVENTS: dict[str, float] = {}


def prune_events(now: float) -> None:
    for event_id, timestamp in list(PROCESSED_EVENTS.items()):
        if now - timestamp > 86400:
            del PROCESSED_EVENTS[event_id]


def _signature_valid(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


@router.post("/health")
async def health_auto_export(request: Request):
    """Accepts signed Health Auto Export JSON pushes."""
    secret = os.environ.get("LIFEOS_HEALTH_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(503, "health webhook secret is not configured")
    signature = request.headers.get("x-lifeos-signature", "")
    timestamp = request.headers.get("x-lifeos-timestamp", "")
    event_id = request.headers.get("x-lifeos-event-id", "")
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(401, "invalid webhook timestamp")
    if not event_id or abs(time.time() - ts) > 300:
        raise HTTPException(401, "stale or missing webhook metadata")
    body = await request.body()
    expected = hmac.new(secret.encode(), f"{timestamp}.{event_id}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "invalid webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON payload")
    prune_events(time.time())
    if event_id in PROCESSED_EVENTS:
        return {"ok": True, "duplicate": True, "imported": {"steps": 0, "weighins": 0}}
    PROCESSED_EVENTS[event_id] = time.time()
    metrics = (payload.get("data") or {}).get("metrics") or []
    imported = {"steps": 0, "weighins": 0}
    with conn() as c:
        pid = active_profile(c)["id"]
        for m in metrics:
            name = (m.get("name") or "").lower()
            for point in m.get("data") or []:
                qty = point.get("qty")
                if qty is None:
                    continue
                day = str(point.get("date", date.today().isoformat()))[:10]
                if name in ("step_count", "steps"):
                    c.execute(
                        "INSERT INTO steps(date,profile_id,count) VALUES(?,?,?)"
                        " ON CONFLICT(date,profile_id)"
                        " DO UPDATE SET count=excluded.count",
                        (day, pid, int(qty)),
                    )
                    imported["steps"] += 1
                elif name in ("body_mass", "weight_body_mass", "weight"):
                    units = (m.get("units") or "").lower()
                    weight = qty * KG_TO_LB if units == "kg" else qty
                    c.execute(
                        "INSERT INTO weighins(ts,weight_lb,profile_id)"
                        " VALUES(?,?,?)",
                        (day + " 08:00:00", weight, pid),
                    )
                    imported["weighins"] += 1
    return {"ok": True, "imported": imported, "event_id": event_id}
