"""Body Ops: meals, protein, weigh-ins, steps, vitamins, streaks."""
from datetime import date, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import conn, get_setting
from ..suggestions import high_protein_snacks, suggest_meals

router = APIRouter(prefix="/api/body", tags=["bodyops"])


class MealLogIn(BaseModel):
    name: str
    protein_g: float = 0
    calories: float = 0
    override_kind: str | None = None


class OverrideIn(BaseModel):
    meal: str
    kind: str  # 'sometimes' | 'today'


class WeighIn(BaseModel):
    weight_lb: float


class StepsIn(BaseModel):
    count: int
    date: str | None = None


def _today() -> str:
    return date.today().isoformat()


def protein_today(c) -> float:
    row = c.execute(
        "SELECT COALESCE(SUM(protein_g),0) p FROM meal_log WHERE date(ts)=?",
        (_today(),),
    ).fetchone()
    return row["p"]


def streak(c, table: str, col: str = "date") -> int:
    """Consecutive days (ending today or yesterday) with an entry."""
    days = {
        r[col]
        for r in c.execute(f"SELECT {col} FROM {table}").fetchall()  # noqa: S608
    }
    n, d = 0, date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


@router.get("/suggestions")
def get_suggestions(max_minutes: int = 15):
    return suggest_meals(max_minutes=max_minutes)


@router.post("/meals/log")
def log_meal(body: MealLogIn):
    with conn() as c:
        if body.protein_g == 0 and body.calories == 0:
            row = c.execute(
                "SELECT protein_g, calories FROM meals WHERE name=?", (body.name,)
            ).fetchone()
            if row:
                body.protein_g, body.calories = row["protein_g"], row["calories"]
        c.execute(
            "INSERT INTO meal_log(name,protein_g,calories,override_kind)"
            " VALUES(?,?,?,?)",
            (body.name, body.protein_g, body.calories, body.override_kind),
        )
        return {"ok": True, "protein_today": protein_today(c)}


@router.post("/overrides")
def add_override(body: OverrideIn):
    """One-tap 'Sometimes / Today' — records the indulgence without changing
    defaults, and mirrors a pragmatic nudge into Vault Flow."""
    with conn() as c:
        c.execute(
            "INSERT INTO overrides(meal,kind) VALUES(?,?)", (body.meal, body.kind)
        )
        c.execute(
            "INSERT INTO nudges(kind,text) VALUES(?,?)",
            (
                "food_override",
                f"Logged '{body.meal}' as a {body.kind} treat — "
                "we'll work it out this week. Options: smaller portion, swap a "
                "later meal, or add a 15-min workout.",
            ),
        )
        return {"ok": True}


@router.post("/weighin")
def add_weighin(body: WeighIn):
    with conn() as c:
        prev = c.execute(
            "SELECT weight_lb FROM weighins ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        c.execute("INSERT INTO weighins(weight_lb) VALUES(?)", (body.weight_lb,))
        msg = "Logged."
        if prev:
            delta = body.weight_lb - prev["weight_lb"]
            if delta < 0:
                msg = f"Down {abs(delta):.1f} lb — nice work, keep the routine."
            elif delta > 0:
                msg = (
                    f"Up {delta:.1f} lb — no drama. One solid day of protein + "
                    "steps gets the trend back."
                )
        return {"ok": True, "message": msg}


@router.post("/steps")
def set_steps(body: StepsIn):
    d = body.date or _today()
    with conn() as c:
        c.execute(
            "INSERT INTO steps(date,count) VALUES(?,?)"
            " ON CONFLICT(date) DO UPDATE SET count=excluded.count",
            (d, body.count),
        )
        return {"ok": True}


@router.post("/vitamins/take")
def take_vitamins():
    with conn() as c:
        c.execute(
            "INSERT INTO vitamins(date,taken) VALUES(?,1)"
            " ON CONFLICT(date) DO UPDATE SET taken=1",
            (_today(),),
        )
        return {"ok": True, "streak": streak(c, "vitamins")}


@router.get("/summary")
def body_summary():
    target = float(get_setting("protein_target_g") or 100)
    step_target = int(get_setting("step_target") or 8000)
    with conn() as c:
        protein = protein_today(c)
        steps_row = c.execute(
            "SELECT count FROM steps WHERE date=?", (_today(),)
        ).fetchone()
        steps = steps_row["count"] if steps_row else 0
        cals_row = c.execute(
            "SELECT COALESCE(SUM(calories),0) k FROM meal_log WHERE date(ts)=?",
            (_today(),),
        ).fetchone()
        vit_row = c.execute(
            "SELECT taken FROM vitamins WHERE date=?", (_today(),)
        ).fetchone()
        weights = [
            dict(r)
            for r in c.execute(
                "SELECT ts, weight_lb FROM weighins ORDER BY ts DESC LIMIT 14"
            ).fetchall()
        ]
        shortfall = max(0.0, target - protein)
        return {
            "protein": {"today_g": protein, "target_g": target},
            "steps": {"today": steps, "target": step_target},
            "calories_today": cals_row["k"],
            "vitamins_taken": bool(vit_row and vit_row["taken"]),
            "streaks": {
                "vitamins": streak(c, "vitamins"),
                "steps": streak(c, "steps"),
            },
            "weighins": weights,
            "protein_shortfall_g": shortfall,
            "snack_suggestions": high_protein_snacks(shortfall)
            if shortfall > 15
            else [],
        }
