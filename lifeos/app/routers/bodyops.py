"""Body Ops: meals, protein, weigh-ins, steps, vitamins, workouts, streaks."""
import os
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import active_profile, conn, get_setting
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


class WorkoutPlanIn(BaseModel):
    date: str | None = None
    kind: str = "15-min bodyweight circuit"
    minutes: int = 15


class FavoriteIn(BaseModel):
    meal: str


class WaterIn(BaseModel):
    glasses: int = 1


FAVORITE_SLOTS = ("breakfast", "lunch", "dinner", "snack")


PHOTO_DIR = os.environ.get("LIFEOS_PHOTOS", "/data/photos")


def _today() -> str:
    return date.today().isoformat()


def protein_today(c) -> float:
    pid = active_profile(c)["id"]
    row = c.execute(
        "SELECT COALESCE(SUM(protein_g),0) p FROM meal_log"
        " WHERE date(ts)=? AND profile_id=?",
        (_today(), pid),
    ).fetchone()
    return row["p"]


def streak(c, table: str, col: str = "date") -> int:
    """Consecutive days (ending today or yesterday) with an entry."""
    pid = active_profile(c)["id"]
    days = {
        r[col]
        for r in c.execute(
            f"SELECT {col} FROM {table} WHERE profile_id=?", (pid,)  # noqa: S608
        ).fetchall()
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
            "INSERT INTO meal_log(name,protein_g,calories,override_kind,profile_id)"
            " VALUES(?,?,?,?,?)",
            (
                body.name,
                body.protein_g,
                body.calories,
                body.override_kind,
                active_profile(c)["id"],
            ),
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
        pid = active_profile(c)["id"]
        existing = c.execute(
            "SELECT 1 FROM workout_plan WHERE date=? AND profile_id=?"
            " AND source='treat_balance' AND done=0",
            (_today(), pid),
        ).fetchone()
        if not existing:
            c.execute(
                "INSERT INTO workout_plan(date,kind,minutes,source,profile_id)"
                " VALUES(?,?,?,?,?)",
                (_today(), "15-min balance-the-treat circuit", 15,
                 "treat_balance", pid),
            )
        return {"ok": True}


@router.post("/weighin")
def add_weighin(body: WeighIn):
    with conn() as c:
        pid = active_profile(c)["id"]
        prev = c.execute(
            "SELECT weight_lb FROM weighins WHERE profile_id=?"
            " ORDER BY ts DESC LIMIT 1",
            (pid,),
        ).fetchone()
        c.execute(
            "INSERT INTO weighins(weight_lb,profile_id) VALUES(?,?)",
            (body.weight_lb, pid),
        )
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
            "INSERT INTO steps(date,profile_id,count) VALUES(?,?,?)"
            " ON CONFLICT(date,profile_id) DO UPDATE SET count=excluded.count",
            (d, active_profile(c)["id"], body.count),
        )
        return {"ok": True}


@router.post("/vitamins/take")
def take_vitamins():
    with conn() as c:
        c.execute(
            "INSERT INTO vitamins(date,profile_id,taken) VALUES(?,?,1)"
            " ON CONFLICT(date,profile_id) DO UPDATE SET taken=1",
            (_today(), active_profile(c)["id"]),
        )
        return {"ok": True, "streak": streak(c, "vitamins")}


def water_today(c) -> int:
    row = c.execute(
        "SELECT glasses FROM water WHERE date=? AND profile_id=?",
        (_today(), active_profile(c)["id"]),
    ).fetchone()
    return row["glasses"] if row else 0


@router.post("/water")
def log_water(body: WaterIn):
    """'Log a glass of water' — counts glasses toward the daily target."""
    with conn() as c:
        c.execute(
            "INSERT INTO water(date,profile_id,glasses) VALUES(?,?,?)"
            " ON CONFLICT(date,profile_id)"
            " DO UPDATE SET glasses=glasses+excluded.glasses",
            (_today(), active_profile(c)["id"], max(1, body.glasses)),
        )
        return {
            "ok": True,
            "today": water_today(c),
            "target": int(get_setting("water_target_glasses") or 8),
        }


@router.get("/workouts/plan")
def workout_plan():
    with conn() as c:
        pid = active_profile(c)["id"]
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM workout_plan WHERE profile_id=? AND date>=?"
                " ORDER BY date, id",
                (pid, (date.today() - timedelta(days=1)).isoformat()),
            ).fetchall()
        ]


@router.post("/workouts/plan")
def add_workout_plan(body: WorkoutPlanIn):
    with conn() as c:
        c.execute(
            "INSERT INTO workout_plan(date,kind,minutes,profile_id)"
            " VALUES(?,?,?,?)",
            (body.date or _today(), body.kind, body.minutes,
             active_profile(c)["id"]),
        )
        return {"ok": True}


@router.post("/workouts/plan/{plan_id}/done")
def complete_workout(plan_id: int):
    with conn() as c:
        row = c.execute(
            "SELECT * FROM workout_plan WHERE id=?", (plan_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "plan not found")
        c.execute("UPDATE workout_plan SET done=1 WHERE id=?", (plan_id,))
        c.execute(
            "INSERT INTO workouts(kind,minutes,profile_id) VALUES(?,?,?)",
            (row["kind"], row["minutes"], row["profile_id"]),
        )
        return {"ok": True, "message": f"{row['kind']} done — logged."}


@router.get("/favorites")
def list_favorites():
    with conn() as c:
        return [
            dict(r)
            for r in c.execute("SELECT * FROM favorites ORDER BY slot").fetchall()
        ]


@router.put("/favorites/{slot}")
def set_favorite(slot: str, body: FavoriteIn):
    """'Set my usual breakfast to sweet potato and eggs' — fuzzy-matches the
    meal library so the favorite carries real macros."""
    slot = slot.lower()
    if slot not in FAVORITE_SLOTS:
        raise HTTPException(400, f"slot must be one of {FAVORITE_SLOTS}")
    spoken = body.meal.strip()
    # spoken names say "and" where the meal library uses "+"
    normalized = spoken.replace(" and ", " + ")
    with conn() as c:
        row = c.execute(
            "SELECT name, protein_g, calories FROM meals"
            " WHERE name LIKE ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE"
            " OR ? LIKE '%' || name || '%' COLLATE NOCASE"
            " OR ? LIKE '%' || name || '%' COLLATE NOCASE"
            " ORDER BY LENGTH(name) LIMIT 1",
            (f"%{spoken}%", f"%{normalized}%", spoken, normalized),
        ).fetchone()
        name, protein, cals = (
            (row["name"], row["protein_g"], row["calories"])
            if row
            else (spoken, 0, 0)
        )
        c.execute(
            "INSERT INTO favorites(slot,meal_name,protein_g,calories)"
            " VALUES(?,?,?,?) ON CONFLICT(slot) DO UPDATE SET"
            " meal_name=excluded.meal_name, protein_g=excluded.protein_g,"
            " calories=excluded.calories",
            (slot, name, protein, cals),
        )
        return {"ok": True, "slot": slot, "meal": name, "matched": bool(row)}


@router.post("/favorites/{slot}/log")
def log_favorite(slot: str):
    """'Log my usual breakfast' — logs the saved favorite with its macros."""
    slot = slot.lower()
    with conn() as c:
        row = c.execute(
            "SELECT * FROM favorites WHERE slot=?", (slot,)
        ).fetchone()
        if row is None:
            raise HTTPException(
                404,
                f"no usual {slot} saved yet — say 'set my usual {slot} to ...'",
            )
        c.execute(
            "INSERT INTO meal_log(name,protein_g,calories,profile_id)"
            " VALUES(?,?,?,?)",
            (
                row["meal_name"],
                row["protein_g"],
                row["calories"],
                active_profile(c)["id"],
            ),
        )
        return {"ok": True, "meal": row["meal_name"],
                "protein_today": protein_today(c)}


@router.post("/meals/photo")
async def photo_meal(photo: UploadFile):
    """Save a plate photo for portion estimation. Vision-LLM analysis plugs in
    here later (Ollama llava / cloud vision); manual macros for now."""
    os.makedirs(PHOTO_DIR, exist_ok=True)
    ext = os.path.splitext(photo.filename or "photo.jpg")[1] or ".jpg"
    path = os.path.join(PHOTO_DIR, f"{_today()}-{uuid.uuid4().hex[:8]}{ext}")
    data = await photo.read()
    with open(path, "wb") as f:
        f.write(data)
    return {
        "ok": True,
        "saved": os.path.basename(path),
        "estimate": None,
        "message": "Photo saved. Auto-estimation needs a vision model "
        "(Ollama llava) — log macros manually for now.",
    }


@router.get("/summary")
def body_summary():
    with conn() as c:
        prof = active_profile(c)
        pid = prof["id"]
        target = prof["protein_target_g"]
        step_target = prof["step_target"]
        protein = protein_today(c)
        steps_row = c.execute(
            "SELECT count FROM steps WHERE date=? AND profile_id=?",
            (_today(), pid),
        ).fetchone()
        steps = steps_row["count"] if steps_row else 0
        cals_row = c.execute(
            "SELECT COALESCE(SUM(calories),0) k FROM meal_log"
            " WHERE date(ts)=? AND profile_id=?",
            (_today(), pid),
        ).fetchone()
        vit_row = c.execute(
            "SELECT taken FROM vitamins WHERE date=? AND profile_id=?",
            (_today(), pid),
        ).fetchone()
        weights = [
            dict(r)
            for r in c.execute(
                "SELECT ts, weight_lb FROM weighins WHERE profile_id=?"
                " ORDER BY ts DESC LIMIT 14",
                (pid,),
            ).fetchall()
        ]
        shortfall = max(0.0, target - protein)
        return {
            "protein": {"today_g": protein, "target_g": target},
            "steps": {"today": steps, "target": step_target},
            "water": {
                "today": water_today(c),
                "target": int(get_setting("water_target_glasses") or 8),
            },
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
