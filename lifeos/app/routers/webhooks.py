"""Inbound webhooks: Health Auto Export (Apple Watch / HealthKit / smart scale)."""
from datetime import date

from fastapi import APIRouter, Request

from ..db import conn

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

KG_TO_LB = 2.20462


@router.post("/health")
async def health_auto_export(request: Request):
    """Accepts Health Auto Export JSON pushes.
    Payload shape: {"data": {"metrics": [{"name": ..., "units": ...,
    "data": [{"date": ..., "qty": ...}, ...]}, ...]}}
    Handles step_count and body_mass (weight); other metrics ignored for now.
    """
    payload = await request.json()
    metrics = (payload.get("data") or {}).get("metrics") or []
    imported = {"steps": 0, "weighins": 0}
    with conn() as c:
        for m in metrics:
            name = (m.get("name") or "").lower()
            for point in m.get("data") or []:
                qty = point.get("qty")
                if qty is None:
                    continue
                day = str(point.get("date", date.today().isoformat()))[:10]
                if name in ("step_count", "steps"):
                    c.execute(
                        "INSERT INTO steps(date,count) VALUES(?,?)"
                        " ON CONFLICT(date) DO UPDATE SET count=excluded.count",
                        (day, int(qty)),
                    )
                    imported["steps"] += 1
                elif name in ("body_mass", "weight_body_mass", "weight"):
                    units = (m.get("units") or "").lower()
                    weight = qty * KG_TO_LB if units == "kg" else qty
                    c.execute(
                        "INSERT INTO weighins(ts,weight_lb) VALUES(?,?)",
                        (day + " 08:00:00", weight),
                    )
                    imported["weighins"] += 1
    return {"ok": True, "imported": imported}
