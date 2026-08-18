"""LifeOS — Vault Flow + Body Ops for the Jarvis Hub."""
from datetime import date

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import active_profile, conn, init_db
from .routers import bodyops, insights, pantry, profiles, vaultflow, webhooks
from .routers.bodyops import protein_today, streak
from .suggestions import suggest_meals

app = FastAPI(title="LifeOS", version="0.2.0")
init_db()

app.include_router(bodyops.router)
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
            "vitamins_taken": bool(vit_row and vit_row["taken"]),
            "streaks": {
                "vitamins": streak(c, "vitamins"),
                "steps": streak(c, "steps"),
            },
            "vault_total": sum(a["balance"] for a in accounts),
            "nudges": nudges,
        }


@app.get("/api/health")
def healthcheck():
    return {"ok": True}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
