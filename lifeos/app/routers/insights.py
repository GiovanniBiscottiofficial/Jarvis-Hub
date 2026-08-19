"""Insights: morning briefing + weekly review (both return a `speech`
string Jarvis/HA can read aloud via TTS)."""
import os
from datetime import date, timedelta

import httpx
from fastapi import APIRouter

from ..db import active_profile, conn, get_setting
from ..suggestions import suggest_meals
from .bodyops import protein_today, streak, water_today
from .vaultflow import week_spending

router = APIRouter(prefix="/api", tags=["insights"])

LAT = os.environ.get("LIFEOS_LAT", "")
LON = os.environ.get("LIFEOS_LON", "")

WEATHER_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy", 51: "drizzle", 53: "drizzle", 55: "drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 80: "showers", 81: "showers",
    82: "heavy showers", 95: "thunderstorms", 96: "thunderstorms",
    99: "thunderstorms",
}


def _weather() -> dict | None:
    """Free, keyless forecast from Open-Meteo when LIFEOS_LAT/LON are set."""
    if not (LAT and LON):
        return None
    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT,
                "longitude": LON,
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "temperature_unit": "fahrenheit",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=5,
        )
        d = r.raise_for_status().json()["daily"]
        return {
            "high_f": d["temperature_2m_max"][0],
            "low_f": d["temperature_2m_min"][0],
            "conditions": WEATHER_CODES.get(d["weather_code"][0], "mixed"),
        }
    except (httpx.HTTPError, KeyError, IndexError):
        return None


def _bills_due_soon(c, days: int = 7) -> list[dict]:
    today_day = date.today().day
    month = date.today().strftime("%Y-%m")
    bills = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM bills WHERE paid_month IS NULL OR paid_month<>?",
            (month,),
        ).fetchall()
    ]
    return [
        b for b in bills
        if b["due_day"] < today_day or b["due_day"] <= today_day + days
    ]


@router.get("/briefing")
def morning_briefing():
    """Everything Jarvis needs to say good morning."""
    today_iso = date.today().isoformat()
    with conn() as c:
        prof = active_profile(c)
        pid = prof["id"]
        protein = protein_today(c)
        steps_row = c.execute(
            "SELECT count FROM steps WHERE date=? AND profile_id=?",
            (today_iso, pid),
        ).fetchone()
        vit_row = c.execute(
            "SELECT taken FROM vitamins WHERE date=? AND profile_id=?",
            (today_iso, pid),
        ).fetchone()
        accounts = [dict(r) for r in c.execute("SELECT * FROM accounts").fetchall()]
        bills = _bills_due_soon(c)
        workouts = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM workout_plan WHERE date=? AND profile_id=?"
                " AND done=0",
                (today_iso, pid),
            ).fetchall()
        ]
        vitamin_streak = streak(c, "vitamins")
        spent_week = week_spending(c)

    total = sum(a["balance"] for a in accounts)
    bills_total = sum(b["amount"] for b in bills)
    leftover = total - bills_total
    meals = suggest_meals(limit=2)
    weather = _weather()

    parts = [f"Good morning, {prof['name']}."]
    if weather:
        parts.append(
            f"Today is {weather['conditions']}, high of "
            f"{round(weather['high_f'])}, low of {round(weather['low_f'])}."
        )
    parts.append(
        f"Protein target is {round(prof['protein_target_g'])} grams; "
        f"you're at {round(protein)}."
    )
    if not (vit_row and vit_row["taken"]):
        parts.append(f"Vitamins are pending — streak is {vitamin_streak} days.")
    if bills:
        parts.append(
            f"{len(bills)} bill{'s' if len(bills) != 1 else ''} due this week "
            f"totaling ${bills_total:.0f}; ${leftover:.0f} left after bills."
        )
    else:
        parts.append(f"No bills due this week; ${total:.0f} available.")
    if spent_week:
        parts.append(f"Discretionary spending is ${spent_week:.0f} this week.")
    if workouts:
        parts.append(f"On the plan: {workouts[0]['kind']}.")
    if meals:
        parts.append(f"Breakfast pick: {meals[0]['name']}.")

    return {
        "date": today_iso,
        "profile": prof["name"],
        "weather": weather,
        "protein": {"today_g": protein, "target_g": prof["protein_target_g"]},
        "steps_today": steps_row["count"] if steps_row else 0,
        "vitamins_taken": bool(vit_row and vit_row["taken"]),
        "bills_due_soon": bills,
        "leftover_after_bills": leftover,
        "workouts_today": workouts,
        "meal_picks": meals,
        "speech": " ".join(parts),
    }


@router.get("/review/weekly")
def weekly_review():
    """Sunday summary: weight trend, protein/step averages, money in vs
    bills paid, streaks, treats."""
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    month = date.today().strftime("%Y-%m")
    with conn() as c:
        prof = active_profile(c)
        pid = prof["id"]
        weights = [
            dict(r)
            for r in c.execute(
                "SELECT ts, weight_lb FROM weighins WHERE date(ts)>=?"
                " AND profile_id=? ORDER BY ts",
                (week_ago, pid),
            ).fetchall()
        ]
        protein_days = c.execute(
            "SELECT date(ts) d, SUM(protein_g) p FROM meal_log"
            " WHERE date(ts)>=? AND profile_id=? GROUP BY date(ts)",
            (week_ago, pid),
        ).fetchall()
        step_days = c.execute(
            "SELECT count FROM steps WHERE date>=? AND profile_id=?",
            (week_ago, pid),
        ).fetchall()
        deposits_row = c.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE date(ts)>=?",
            (week_ago,),
        ).fetchone()
        bills_paid = c.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM bills WHERE paid_month=?",
            (month,),
        ).fetchone()
        treats = c.execute(
            "SELECT COUNT(*) n FROM overrides WHERE date(ts)>=?", (week_ago,)
        ).fetchone()
        workouts_done = c.execute(
            "SELECT COUNT(*) n FROM workouts WHERE date(ts)>=?"
            " AND profile_id=?",
            (week_ago, pid),
        ).fetchone()
        streaks = {"vitamins": streak(c, "vitamins"), "steps": streak(c, "steps")}
        spent_week = week_spending(c)

    weight_delta = (
        round(weights[-1]["weight_lb"] - weights[0]["weight_lb"], 1)
        if len(weights) >= 2
        else None
    )
    protein_vals = [r["p"] for r in protein_days]
    avg_protein = round(sum(protein_vals) / len(protein_vals), 1) if protein_vals else 0
    step_vals = [r["count"] for r in step_days]
    avg_steps = round(sum(step_vals) / len(step_vals)) if step_vals else 0

    parts = ["Weekly review."]
    if weight_delta is not None:
        direction = "down" if weight_delta < 0 else "up"
        parts.append(f"Weight is {direction} {abs(weight_delta)} pounds.")
    parts.append(
        f"Protein averaged {round(avg_protein)} grams a day against a "
        f"{round(prof['protein_target_g'])} gram target."
    )
    if avg_steps:
        parts.append(f"Steps averaged {avg_steps:,} a day.")
    parts.append(
        f"${deposits_row['s']:.0f} came in this week and "
        f"${bills_paid['s']:.0f} of bills are paid this month."
    )
    if spent_week:
        parts.append(f"Discretionary spending was ${spent_week:.0f}.")
    if treats["n"]:
        parts.append(
            f"{treats['n']} treat{'s' if treats['n'] != 1 else ''} logged and "
            f"{workouts_done['n']} workout"
            f"{'s' if workouts_done['n'] != 1 else ''} done — balanced week."
        )

    return {
        "period_start": week_ago,
        "weight": {"entries": weights, "delta_lb": weight_delta},
        "avg_daily_protein_g": avg_protein,
        "protein_target_g": prof["protein_target_g"],
        "avg_daily_steps": avg_steps,
        "money_in": deposits_row["s"],
        "bills_paid_this_month": bills_paid["s"],
        "treats_this_week": treats["n"],
        "spending_this_week": spent_week,
        "workouts_this_week": workouts_done["n"],
        "streaks": streaks,
        "speech": " ".join(parts),
    }


@router.get("/ask")
def ask():
    """One-shot spoken answers for voice intents (polled by Home Assistant
    as a REST sensor; each key is a ready-to-speak sentence)."""
    today_iso = date.today().isoformat()
    with conn() as c:
        prof = active_profile(c)
        pid = prof["id"]
        protein = protein_today(c)
        steps_row = c.execute(
            "SELECT count FROM steps WHERE date=? AND profile_id=?",
            (today_iso, pid),
        ).fetchone()
        bills = _bills_due_soon(c)
        accounts = [dict(r) for r in c.execute("SELECT * FROM accounts").fetchall()]
        nudge_row = c.execute(
            "SELECT id, text FROM nudges WHERE resolved=0 ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        vit_row = c.execute(
            "SELECT taken FROM vitamins WHERE date=? AND profile_id=?",
            (today_iso, pid),
        ).fetchone()
        workouts_done = c.execute(
            "SELECT COUNT(*) n FROM workouts WHERE date(ts)=? AND profile_id=?",
            (today_iso, pid),
        ).fetchone()
        spent_week = week_spending(c)
        water = water_today(c)
        goals = [
            dict(r)
            for r in c.execute("SELECT * FROM savings_goals ORDER BY name").fetchall()
        ]
        grocery = [
            r["item"]
            for r in c.execute(
                "SELECT item FROM grocery_list WHERE done=0 ORDER BY ts"
            ).fetchall()
        ]

    target = prof["protein_target_g"]
    left = max(0, round(target - protein))
    protein_speech = (
        f"You're at {round(protein)} of {round(target)} grams — "
        + (f"{left} grams to go." if left else "target hit, nice.")
    )

    if bills:
        bill_list = ", ".join(
            f"{b['name']} ${b['amount']:.0f} on the {b['due_day']}" for b in bills
        )
        bills_speech = (
            f"{len(bills)} bill{'s' if len(bills) != 1 else ''} due soon: {bill_list}."
        )
    else:
        bills_speech = "No bills due this week."

    total = sum(a["balance"] for a in accounts)
    leftover = total - sum(b["amount"] for b in bills)
    money_speech = (
        f"${total:.0f} across accounts, ${leftover:.0f} left after upcoming bills."
    )

    steps = steps_row["count"] if steps_row else 0
    steps_speech = f"{steps:,} steps today against a {prof['step_target']:,} target."

    meals = suggest_meals(limit=2)
    meals_speech = (
        "You could make " + " or ".join(m["name"] for m in meals) + "."
        if meals
        else "No pantry-matched meals right now — log some pantry items."
    )

    spending_speech = (
        f"You've spent ${spent_week:.0f} this week."
        if spent_week
        else "No discretionary spending logged this week."
    )

    grocery_speech = (
        "On the grocery list: " + ", ".join(grocery) + "."
        if grocery
        else "The grocery list is empty."
    )

    water_target = int(get_setting("water_target_glasses") or 8)
    water_speech = (
        f"{water} of {water_target} glasses of water today"
        + (" — target hit." if water >= water_target else ".")
    )

    dinner_speech = (
        (
            "You could make " + " or ".join(m["name"] for m in meals)
            if meals
            else "Nothing pantry-matched tonight — sweet potatoes and grilled "
            "chicken never miss"
        )
        + (
            f" — {left} grams of protein still to go."
            if left
            else " — protein target already hit."
        )
    )

    evening_parts = [
        "Evening report.",
        f"Protein: {round(protein)} of {round(target)} grams.",
        f"Water: {water} of {water_target} glasses.",
        f"Steps: {steps:,} of {prof['step_target']:,}.",
    ]
    if workouts_done["n"]:
        evening_parts.append(
            f"{workouts_done['n']} workout"
            f"{'s' if workouts_done['n'] != 1 else ''} done."
        )
    evening_parts.append(
        "Vitamins taken."
        if vit_row and vit_row["taken"]
        else "Vitamins still pending."
    )
    if spent_week:
        evening_parts.append(f"${spent_week:.0f} spent this week.")
    evening_parts.append(bills_speech)
    evening_speech = " ".join(evening_parts)

    goals_speech = (
        "; ".join(
            f"{g['name']}: ${g['saved']:.0f}"
            + (f" of ${g['target']:.0f}" if g["target"] else "")
            for g in goals
        )
        + "."
        if goals
        else "No savings goals yet — say 'add 50 to my vacation fund' to start one."
    )

    return {
        "protein": protein_speech,
        "steps": steps_speech,
        "bills": bills_speech,
        "money": money_speech,
        "meals": meals_speech,
        "spending": spending_speech,
        "grocery": grocery_speech,
        "water": water_speech,
        "goals": goals_speech,
        "dinner": dinner_speech,
        "evening": evening_speech,
        "nudge": nudge_row["text"] if nudge_row else "",
        "nudge_id": nudge_row["id"] if nudge_row else 0,
    }
