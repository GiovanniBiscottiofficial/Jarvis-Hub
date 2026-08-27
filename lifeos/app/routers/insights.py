"""Insights: morning briefing + weekly review (both return a `speech`
string Jarvis/HA can read aloud via TTS)."""
import os
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..chef import chef_summary
from ..db import active_profile, conn, get_setting
from ..paydays import payday_schedule, scheduled_bill_due_date
from .bodyops import protein_today, streak, water_today
from .budget import budget_speech
from .learning import forget_learning_value, record_learning_observation
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
    today = date.today()
    month = date.today().strftime("%Y-%m")
    bills = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM bills WHERE paid_month IS NULL OR paid_month<>?",
            (month,),
        ).fetchall()
    ]
    return [b for b in bills if 0 <= (
        scheduled_bill_due_date(
            b["paycheck"] or 1, b["due_day"], today, b.get("start_period")
        ) - today
    ).days <= days]


def _pick(options: tuple[str, ...], seed: int) -> str:
    """Choose stable daily wording without making briefings feel random."""
    return options[seed % len(options)]


def compose_briefing(facts: dict, *, hour: int, day_ordinal: int) -> dict:
    """Turn grounded LifeOS facts into a concise, context-aware briefing.

    This deliberately stays deterministic and local. Jarvis varies delivery and
    prioritizes relevant facts, but never asks a language model to invent or
    reinterpret health, finance, schedule, or household data.
    """
    name = facts["name"]
    if hour < 12:
        period = "morning"
        opening = _pick(
            (
                f"Good morning, {name}.",
                f"Morning, {name}.",
                f"{name}, good morning.",
            ),
            day_ordinal,
        )
    elif hour < 17:
        period = "afternoon"
        opening = _pick(
            (
                f"Good afternoon, {name}.",
                f"{name}, here's where the day stands.",
                f"Afternoon, {name}. Here's what matters right now.",
            ),
            day_ordinal,
        )
    else:
        period = "evening"
        opening = _pick(
            (
                f"Good evening, {name}.",
                f"{name}, here's the evening picture.",
                f"Evening, {name}. Here's what still matters today.",
            ),
            day_ordinal,
        )

    sections: list[dict[str, str]] = []

    def add(key: str, text: str) -> None:
        if text:
            sections.append({"key": key, "text": text})

    add("opening", opening)
    if period == "morning":
        add("affirmation", facts["affirmation"])

    weather = facts.get("weather")
    if weather:
        add(
            "weather",
            _pick(
                (
                    "Outside, it's {conditions}, with a high of {high} and a low of {low}.",
                    "Expect {conditions} today, reaching {high} with a low near {low}.",
                ),
                day_ordinal,
            ).format(
                conditions=weather["conditions"],
                high=round(weather["high_f"]),
                low=round(weather["low_f"]),
            ),
        )

    vitamins_pending = not facts["vitamins_taken"]
    meal_name = facts.get("meal_name")
    if period == "morning" and vitamins_pending and meal_name:
        add(
            "before_leaving",
            f"Before you head out, take your vitamins and pull out what you'll need "
            f"for {meal_name} tonight.",
        )
    elif period == "morning" and vitamins_pending:
        add("before_leaving", "Your one loose end before leaving is your vitamins.")
    elif period == "morning" and meal_name:
        add(
            "before_leaving",
            f"For dinner, pull out what you'll need for {meal_name} before you leave.",
        )
    elif vitamins_pending:
        add("vitamins", "Vitamins are still open for today.")

    protein = round(facts["protein"])
    protein_target = round(facts["protein_target"])
    if protein >= protein_target:
        add("protein", f"You've cleared your {protein_target}-gram protein target.")
    elif protein > 0:
        add(
            "protein",
            f"You're at {protein} of {protein_target} grams of protein, with "
            f"{protein_target - protein} to go.",
        )
    elif period == "morning":
        add("protein", f"Your protein target today is {protein_target} grams.")

    workouts = facts.get("workouts") or []
    if workouts:
        add("workout", f"Your movement plan is {workouts[0]['kind']}.")

    bills = facts.get("bills") or []
    bills_total = facts["bills_total"]
    audit = facts.get("audit_health", "")
    safe_to_spend = facts.get("safe_to_spend", 0)
    if bills:
        bill_word = "bill" if len(bills) == 1 else "bills"
        add(
            "bills",
            f"You have {len(bills)} upcoming {bill_word} in the next seven days, "
            f"totaling ${bills_total:,.2f}.",
        )

    next_pay = facts["next_pay"]
    pay_date = date.fromisoformat(next_pay["date"])
    if next_pay["days_away"] == 0:
        add(
            "payday",
            f"{next_pay['label']} lands today at ${next_pay['amount']:,.2f}; "
            "the budget is ready for it.",
        )
    else:
        unit = "day" if next_pay["days_away"] == 1 else "days"
        prefix = "Your first budget cycle is staged" if audit == "scheduled" else next_pay["label"]
        add(
            "payday",
            f"{prefix} for {pay_date.strftime('%A, %B')} {pay_date.day}, "
            f"in {next_pay['days_away']} {unit}.",
        )

    if audit == "action needed":
        add(
            "budget",
            "The budget needs a quick review before I call any amount safe to spend.",
        )
    elif audit in {"balanced", "buffered"}:
        add(
            "budget",
            f"After the current plan, ${safe_to_spend:,.2f} is safe to spend from this paycheck.",
        )

    spent_week = facts.get("spent_week", 0)
    if spent_week > 0:
        add("spending", f"You've logged ${spent_week:,.2f} in spending this week.")

    if period != "morning" and meal_name:
        add("meal", f"Your best pantry match is {meal_name}.")

    add(
        "closing",
        _pick(
            (
                "That's the board. I'll keep watching the details.",
                "You're caught up. I'll flag anything that changes.",
                "That's what matters right now. I'll handle the watch.",
            ),
            day_ordinal + 1,
        ),
    )
    return {
        "style": "contextual-v1",
        "period": period,
        "sections": sections,
        "speech": " ".join(section["text"] for section in sections),
    }


@router.get("/briefing")
def morning_briefing():
    """Grounded, natural briefing for scheduled and on-demand speech."""
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
        budget = budget_speech(c)

    total = sum(a["balance"] for a in accounts)
    bills_total = sum(b["amount"] for b in bills)
    leftover = total - bills_total
    meals = chef_summary()["suggestions"][:2]
    weather = _weather()

    paydays = payday_schedule(count=2)
    next_pay = paydays[0]
    affirmations = (
        "You are capable, prepared, and allowed to move through today with confidence.",
        "You have handled hard days before; today gets your focus, not your fear.",
        "Your consistency is building the life you want, one deliberate choice at a time.",
        "You do not need a perfect day—only a purposeful next step.",
    )
    affirmation = affirmations[date.today().toordinal() % len(affirmations)]

    spoken = compose_briefing(
        {
            "name": prof["name"],
            "affirmation": affirmation,
            "weather": weather,
            "protein": protein,
            "protein_target": prof["protein_target_g"],
            "vitamins_taken": bool(vit_row and vit_row["taken"]),
            "vitamin_streak": vitamin_streak,
            "meal_name": meals[0]["name"] if meals else None,
            "bills": bills,
            "bills_total": bills_total,
            "leftover": leftover,
            "spent_week": spent_week,
            "safe_to_spend": budget["safe_to_spend"],
            "audit_health": budget["audit_health"],
            "workouts": workouts,
            "next_pay": next_pay,
        },
        hour=datetime.now().hour,
        day_ordinal=date.today().toordinal(),
    )

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
        "safe_to_spend": budget["safe_to_spend"],
        "audit_health": budget["audit_health"],
        "affirmation": affirmation,
        "dinner_prep": (
            f"Take out ingredients for {meals[0]['name']}." if meals
            else "Choose and thaw something for dinner before leaving."
        ),
        "paydays": paydays,
        "briefing_style": spoken["style"],
        "briefing_period": spoken["period"],
        "sections": spoken["sections"],
        "speech": spoken["speech"],
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


class MemoryIn(BaseModel):
    fact: str


@router.post("/memory")
def remember(body: MemoryIn):
    """'Remember that I park in spot 22B' — durable fact storage."""
    fact = body.fact.strip()
    if not fact:
        raise HTTPException(400, "memory fact cannot be empty")
    with conn() as c:
        c.execute("INSERT INTO memories(fact) VALUES(?)", (fact,))
        profile = active_profile(c)
        record_learning_observation(
            c,
            profile_id=profile["id"],
            domain="memory",
            subject="durable fact",
            value=fact,
            signal="stated",
            source="voice",
            context={"origin": "remember_that"},
            auto_confirm=True,
        )
    return {"ok": True, "fact": fact}


@router.post("/memory/forget")
def forget():
    """'Forget that' — drops the most recent memory."""
    with conn() as c:
        row = c.execute(
            "SELECT id, fact FROM memories WHERE active=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"ok": False, "fact": ""}
        c.execute("UPDATE memories SET active=0 WHERE id=?", (row["id"],))
        profile = active_profile(c)
        forget_learning_value(
            c,
            profile_id=profile["id"],
            domain="memory",
            subject="durable fact",
            value=row["fact"],
            reason="Forgotten through the Jarvis voice memory command.",
        )
        return {"ok": True, "fact": row["fact"]}


@router.get("/memory")
def memories():
    with conn() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT id, ts, fact FROM memories WHERE active=1 ORDER BY ts"
            ).fetchall()
        ]


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
        memory_facts = [
            r["fact"]
            for r in c.execute(
                "SELECT fact FROM memories WHERE active=1 ORDER BY ts DESC LIMIT 10"
            ).fetchall()
        ]
        learned_preferences = [
            dict(r)
            for r in c.execute(
                "SELECT subject,value,sentiment FROM learned_preferences"
                " WHERE profile_id=? AND status='confirmed' AND domain!='memory'"
                " ORDER BY updated_at DESC LIMIT 8",
                (pid,),
            ).fetchall()
        ]
        budget = budget_speech(c)

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

    chef = chef_summary()
    meals = chef["suggestions"][:2]
    meals_speech = (
        "Chef Jarvis recommends " + " or ".join(m["name"] for m in meals) + ". "
        + meals[0]["why"]
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
    pantry_speech = (
        f"I have {chef['pantry']['items']} pantry items recorded. "
        f"{chef['pantry']['out']} are out and {chef['pantry']['low']} are low. "
        + (f"My best current meal is {meals[0]['name']}." if meals else "Add inventory so I can plan accurately.")
    )

    water_target = int(get_setting("water_target_glasses") or 8)
    water_speech = (
        f"{water} of {water_target} glasses of water today"
        + (" — target hit." if water >= water_target else ".")
    )

    dinner_speech = (
        (
            "Chef Jarvis recommends " + " or ".join(m["name"] for m in meals)
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

    learned_speech = [
        f"for {item['subject']}, you {item['sentiment']} {item['value']}"
        for item in learned_preferences
    ]
    memory_parts = []
    if memory_facts:
        memory_parts.append("You asked me to remember: " + "; ".join(memory_facts) + ".")
    if learned_speech:
        memory_parts.append("Confirmed preferences: " + "; ".join(learned_speech) + ".")
    memory_speech = " ".join(memory_parts) or (
        "I have no confirmed memories or preferences yet. Say 'remember that' or use the LifeOS Learning Ledger."
    )

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
        "pantry": pantry_speech,
        "water": water_speech,
        "water_count": water,
        "water_target": water_target,
        "goals": goals_speech,
        "memory": memory_speech,
        "dinner": dinner_speech,
        "evening": evening_speech,
        "budget": budget["budget"],
        "paycheck": budget["paycheck"],
        "networth": budget["networth"],
        "safe_to_spend": budget["safe_to_spend"],
        "audit_health": budget["audit_health"],
        "nudge": nudge_row["text"] if nudge_row else "",
        "nudge_id": nudge_row["id"] if nudge_row else 0,
    }
