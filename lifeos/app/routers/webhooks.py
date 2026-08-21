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


@router.post("/health")
async def health_auto_export(request: Request):
    """Accept Health Auto Export pushes with a static secret and dedupe key."""
    secret = os.environ.get("LIFEOS_HEALTH_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(503, "health webhook secret is not configured")

    supplied_secret = request.headers.get("x-lifeos-webhook-secret", "")
    if not hmac.compare_digest(supplied_secret, secret):
        raise HTTPException(401, "invalid webhook secret")

    body = await request.body()
    signature = request.headers.get("x-lifeos-signature", "")
    timestamp = request.headers.get("x-lifeos-timestamp", "")
    event_id = request.headers.get("x-lifeos-event-id") or request.headers.get("session-id", "")
    if not event_id:
        raise HTTPException(401, "missing webhook event id")
    if signature or timestamp:
        try:
            ts = int(timestamp)
        except ValueError:
            raise HTTPException(401, "invalid webhook timestamp")
        if abs(time.time() - ts) > 300:
            raise HTTPException(401, "stale webhook timestamp")
        expected = hmac.new(
            secret.encode(),
            f"{timestamp}.{event_id}.".encode() + body,
            hashlib.sha256,
        ).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            raise HTTPException(401, "invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON payload")
    prune_events(time.time())
    metrics = (payload.get("data") or {}).get("metrics") or []
    imported = {"steps": 0, "weighins": 0}
    with conn() as c:
        existing = c.execute(
            "SELECT 1 FROM webhook_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if existing or event_id in PROCESSED_EVENTS:
            return {"ok": True, "duplicate": True, "imported": {"steps": 0, "weighins": 0}}
        c.execute(
            "INSERT INTO webhook_events(event_id,received_at) VALUES(?,?)",
            (event_id, time.time()),
        )
        PROCESSED_EVENTS[event_id] = time.time()
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
