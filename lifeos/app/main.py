"""LifeOS — Vault Flow + Body Ops for the Jarvis Hub."""
from datetime import date
import hmac
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import active_profile, conn, get_setting, init_db
from .routers import (
    bodyops,
    budget,
    insights,
    pantry,
    profiles,
    vaultflow,
    webhooks,
)
from .routers.bodyops import protein_today, streak, water_today
from .suggestions import suggest_meals

app = FastAPI(title="LifeOS", version="0.2.0")


PUBLIC_PATHS = {"/api/health", "/api/auth"}


def _token_matches(supplied: str | None, expected: str) -> bool:
    if not supplied or not expected:
        return False
    supplied_bytes = supplied.encode()
    expected_bytes = expected.encode()
    return len(supplied_bytes) == len(expected_bytes) and hmac.compare_digest(
        supplied_bytes, expected_bytes
    )



@app.middleware("http")
async def require_api_token(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path not in PUBLIC_PATHS:
        expected = os.environ.get("LIFEOS_API_TOKEN", "").strip()
        authorization = request.headers.get("authorization", "")
        supplied = (
            authorization[7:].strip()
            if authorization.lower().startswith("bearer ")
            else request.cookies.get("lifeos_session", "")
        )
        if not expected or not _token_matches(supplied, expected):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


@app.get("/api/health")
def healthcheck():
    return {"ok": True}


@app.post("/api/auth")
async def auth(request: Request):
    expected = os.environ.get("LIFEOS_API_TOKEN", "").strip()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "invalid JSON"}, status_code=400)
    if not expected or not _token_matches(body.get("token"), expected):
        return JSONResponse({"detail": "invalid token"}, status_code=401)
    response = JSONResponse({"ok": True})
    response.set_cookie("lifeos_session", expected, httponly=True, samesite="strict", secure=False, max_age=86400)
    return response

init_db()

app.include_router(bodyops.router)
app.include_router(budget.router)
app.include_router(vaultflow.router)
app.include_router(webhooks.router)
app.include_router(profiles.router)
app.include_router(pantry.router)
app.include_router(insights.router)


@app.get("/api/today")
def today():
    """Morning workflow payload: suggestions, progress, vault snapshot, nudges."""
    with conn() as c:
        prof = active_profile(c)
        target = prof["protein_target_g"]
        protein = protein_today(c)
        steps_row = c.execute(
            "SELECT count FROM steps WHERE date=? AND profile_id=?",
            (date.today().isoformat(), prof["id"]),
        ).fetchone()
        vit_row = c.execute(
            "SELECT taken FROM vitamins WHERE date=? AND profile_id=?",
            (date.today().isoformat(), prof["id"]),
        ).fetchone()
        accounts = [dict(r) for r in c.execute("SELECT * FROM accounts").fetchall()]
        nudges = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM nudges WHERE resolved=0 ORDER BY ts DESC LIMIT 5"
            ).fetchall()
        ]
        return {
            "date": date.today().isoformat(),
            "profile": prof["name"],
            "step_target": prof["step_target"],
            "meal_suggestions": suggest_meals(),
            "protein": {"today_g": protein, "target_g": target},
            "steps_today": steps_row["count"] if steps_row else 0,
            "water": {
                "today": water_today(c),
                "target": int(get_setting("water_target_glasses") or 8),
            },
            "vitamins_taken": bool(vit_row and vit_row["taken"]),
            "streaks": {
                "vitamins": streak(c, "vitamins"),
                "steps": streak(c, "steps"),
            },
            "vault_total": sum(a["balance"] for a in accounts),
            "nudges": nudges,
        }


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
