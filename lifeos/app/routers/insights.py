"""Insights: morning briefing + weekly review (both return a `speech`
string Jarvis/HA can read aloud via TTS)."""
import asyncio
import io
import os
import re
import wave
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..body_intelligence import daily_loop
from ..chef import chef_summary
from ..commute import commute_snapshot
from ..context_engine import current_context, list_proposals
from ..db import active_profile, conn, get_setting
from ..intelligence import build_intelligence
from ..pattern_learning import record_commute_observation
from ..paydays import payday_schedule, scheduled_bill_due_date
from .bodyops import protein_today, streak, water_today
from .budget import budget_speech
from .learning import (
    forget_learning_value,
    learning_snapshot,
    record_learning_observation,
)
from .vaultflow import week_spending

router = APIRouter(prefix="/api", tags=["insights"])

LAT = os.environ.get("LIFEOS_LAT", "")
LON = os.environ.get("LIFEOS_LON", "")
PIPER_URI = os.environ.get("PIPER_URI", "tcp://piper:10200")

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

    commute = facts.get("commute")
    if period == "morning" and facts.get("workday") and commute and commute.get("minutes"):
        distance = (
            f" over {commute['miles']:.1f} miles" if commute.get("miles") is not None else ""
        )
        if commute.get("traffic_live"):
            add(
                "commute",
                f"Your live commute to work is {commute['minutes']} minutes{distance}. "
                f"Your planned departure is {commute.get('planned_departure', '07:35')}.",
            )
        else:
            add(
                "commute",
                f"Your normal drive to work is about {commute['minutes']} minutes{distance}. "
                f"Live traffic is not commissioned, so keep your {commute.get('planned_departure', '07:35')} departure buffer.",
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

    decision_support = facts.get("decision_support")
    if decision_support:
        add("decision_support", str(decision_support))

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
    workday = date.today().weekday() < 5
    commute = commute_snapshot() if workday else None
    if commute:
        record_commute_observation(pid, commute)

    paydays = payday_schedule(count=2)
    next_pay = paydays[0]
    affirmations = (
        "You are capable, prepared, and allowed to move through today with confidence.",
        "You have handled hard days before; today gets your focus, not your fear.",
        "Your consistency is building the life you want, one deliberate choice at a time.",
        "You do not need a perfect day—only a purposeful next step.",
    )
    affirmation = affirmations[date.today().toordinal() % len(affirmations)]

    intelligence = build_intelligence(
        current_context(event_limit=40),
        learning=learning_snapshot(limit=20),
        proposals=list_proposals("pending"),
    )
    # Body, food, and finance already have dedicated briefing sections.  Only
    # inject cross-domain safety/presence conflicts so Jarvis adds intelligence
    # without repeating the same facts in different words.
    spoken_intelligence = next(
        (
            item
            for item in intelligence["recommendations"]
            if item["id"] in {"review_security", "resolve_away_presence"}
            and item["confidence"] >= 0.7
            and intelligence["data_quality"]["fresh"]
        ),
        None,
    )

    spoken = compose_briefing(
        {
            "name": prof["name"],
            "affirmation": affirmation,
            "weather": weather,
            "workday": workday,
            "commute": commute,
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
            "decision_support": (
                f"Decision support: {spoken_intelligence['title']}. "
                f"{spoken_intelligence['rationale']}"
                if spoken_intelligence
                else None
            ),
        },
        hour=datetime.now().hour,
        day_ordinal=date.today().toordinal(),
    )

    return {
        "date": today_iso,
        "profile": prof["name"],
        "weather": weather,
        "commute": commute,
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
        "intelligence": intelligence,
        "briefing_style": spoken["style"],
        "briefing_period": spoken["period"],
        "sections": spoken["sections"],
        "speech": spoken["speech"],
    }


@router.get("/review/weekly")
def weekly_review():
    """Evidence-backed weekly operating picture for Giovanni.

    The review separates missing data from poor performance, compares the last
    seven calendar days with the seven before them, and returns explainable
    recommendations.  It never authorizes household, health, or money actions.
    """
    today = date.today()
    period_start = (today - timedelta(days=6)).isoformat()
    previous_start = (today - timedelta(days=13)).isoformat()
    previous_end = (today - timedelta(days=7)).isoformat()
    month = date.today().strftime("%Y-%m")
    with conn() as c:
        prof = active_profile(c)
        pid = prof["id"]
        weights = [
            dict(r)
            for r in c.execute(
                "SELECT ts, weight_lb FROM weighins WHERE date(ts)>=?"
                " AND profile_id=? ORDER BY ts",
                (period_start, pid),
            ).fetchall()
        ]
        protein_days = [dict(r) for r in c.execute(
            "SELECT date(ts) d, SUM(protein_g) p FROM meal_log"
                " WHERE date(ts)>=? AND profile_id=? GROUP BY date(ts)",
            (period_start, pid),
        ).fetchall()]
        previous_protein_days = [dict(r) for r in c.execute(
            "SELECT date(ts) d, SUM(protein_g) p FROM meal_log"
            " WHERE date(ts)>=? AND date(ts)<=? AND profile_id=?"
            " GROUP BY date(ts)",
            (previous_start, previous_end, pid),
        ).fetchall()]
        step_days = [dict(r) for r in c.execute(
            "SELECT date, count FROM steps WHERE date>=? AND profile_id=?",
            (period_start, pid),
        ).fetchall()]
        previous_step_days = [dict(r) for r in c.execute(
            "SELECT date, count FROM steps WHERE date>=? AND date<=?"
            " AND profile_id=?",
            (previous_start, previous_end, pid),
        ).fetchall()]
        vitamin_days = [dict(r) for r in c.execute(
            "SELECT date, taken FROM vitamins WHERE date>=? AND profile_id=?",
            (period_start, pid),
        ).fetchall()]
        deposits_row = c.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE date(ts)>=?",
            (period_start,),
        ).fetchone()
        bills_paid = c.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM bills WHERE paid_month=?",
            (month,),
        ).fetchone()
        treats = c.execute(
            "SELECT COUNT(*) n FROM overrides WHERE date(ts)>=?", (period_start,)
        ).fetchone()
        workouts_done = c.execute(
            "SELECT COUNT(*) n FROM workouts WHERE date(ts)>=?"
            " AND profile_id=?",
            (period_start, pid),
        ).fetchone()
        previous_workouts = c.execute(
            "SELECT COUNT(*) n FROM workouts WHERE date(ts)>=? AND date(ts)<=?"
            " AND profile_id=?",
            (previous_start, previous_end, pid),
        ).fetchone()
        streaks = {"vitamins": streak(c, "vitamins"), "steps": streak(c, "steps")}
        spent_week = week_spending(c)
        previous_spending = c.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM spending"
            " WHERE date(ts)>=? AND date(ts)<=?",
            (previous_start, previous_end),
        ).fetchone()["s"]
        spending_entries = c.execute(
            "SELECT COUNT(*) n FROM spending WHERE date(ts)>=?", (period_start,)
        ).fetchone()["n"]
        meal_entries = c.execute(
            "SELECT COUNT(*) n FROM meal_log WHERE date(ts)>=? AND profile_id=?",
            (period_start, pid),
        ).fetchone()["n"]
        budget = budget_speech(c)

    weight_delta = (
        round(weights[-1]["weight_lb"] - weights[0]["weight_lb"], 1)
        if len(weights) >= 2
        else None
    )
    protein_vals = [r["p"] for r in protein_days]
    avg_protein = round(sum(protein_vals) / len(protein_vals), 1) if protein_vals else 0
    step_vals = [r["count"] for r in step_days]
    avg_steps = round(sum(step_vals) / len(step_vals)) if step_vals else 0

    def observed_average(rows: list[dict], key: str, digits: int = 1) -> float:
        values = [float(row[key]) for row in rows]
        return round(sum(values) / len(values), digits) if values else 0

    def trend(current: float, previous: float, *, lower_is_better: bool = False) -> dict:
        delta = round(current - previous, 1)
        percent = round(delta / previous * 100, 1) if previous else None
        if not previous:
            direction = "new" if current else "flat"
        elif abs(percent or 0) < 3:
            direction = "steady"
        else:
            direction = "up" if delta > 0 else "down"
        favorable = None if direction in ("new", "flat", "steady") else (
            delta < 0 if lower_is_better else delta > 0
        )
        return {
            "current": current,
            "previous": previous,
            "delta": delta,
            "percent": percent,
            "direction": direction,
            "favorable": favorable,
        }

    previous_protein = observed_average(previous_protein_days, "p")
    previous_steps = observed_average(previous_step_days, "count", 0)
    protein_target_days = sum(
        1 for value in protein_vals if value >= prof["protein_target_g"]
    )
    step_target_days = sum(1 for value in step_vals if value >= prof["step_target"])
    vitamins_taken = sum(1 for row in vitamin_days if row["taken"])

    coverage_inputs = {
        "protein": round(min(len(protein_days), 7) / 7 * 100),
        "steps": round(min(len(step_days), 7) / 7 * 100),
        "vitamins": round(min(len(vitamin_days), 7) / 7 * 100),
        "weight": round(min(len(weights), 2) / 2 * 100),
    }
    confidence_score = round(sum(coverage_inputs.values()) / len(coverage_inputs))
    confidence_label = (
        "strong" if confidence_score >= 75
        else "developing" if confidence_score >= 45
        else "limited"
    )
    audit_score = {
        "balanced": 1.0, "buffered": 0.85, "scheduled": 0.75,
        "action needed": 0.35,
    }.get(budget["audit_health"], 0.5)
    protein_score = (
        min(avg_protein / max(prof["protein_target_g"], 1), 1)
        if protein_days else 0.5
    )
    step_score = (
        min(avg_steps / max(prof["step_target"], 1), 1)
        if step_days else 0.5
    )
    vitamin_score = vitamins_taken / 7 if vitamin_days else 0.5
    calculated_score = round(100 * (
        protein_score * 0.25
        + step_score * 0.20
        + vitamin_score * 0.15
        + min(workouts_done["n"] / 3, 1) * 0.15
        + audit_score * 0.15
        + confidence_score / 100 * 0.10
    ))
    operating_score = calculated_score if confidence_score >= 25 else None

    wins: list[dict] = []
    watch: list[dict] = []
    priorities: list[dict] = []

    def signal(target: list[dict], domain: str, title: str, evidence: str) -> None:
        target.append({"domain": domain, "title": title, "evidence": evidence})

    if len(protein_days) < 3:
        pass  # Coverage guidance below handles unknown performance honestly.
    elif protein_target_days >= 4:
        signal(wins, "body", "Protein rhythm held", f"Target reached on {protein_target_days} of 7 days.")
    else:
        signal(watch, "body", "Protein consistency is open", f"Target reached on {protein_target_days} of 7 days.")
        signal(priorities, "body", "Protect the weekday protein floor", "Use the three one-tap protein anchors before adding new meal complexity.")
    if step_target_days >= 4:
        signal(wins, "body", "Movement cleared the weekly majority", f"Step target reached on {step_target_days} days.")
    elif len(step_days) >= 3:
        signal(watch, "body", "Movement is below target", f"Average {avg_steps:,} against {prof['step_target']:,} steps.")
        signal(priorities, "body", "Raise the movement floor", "Add one reliable walk window to the lowest-step workdays.")
    if len(vitamin_days) < 3:
        pass
    elif vitamins_taken >= 5:
        signal(wins, "routine", "Morning vitamins stayed protected", f"Taken on {vitamins_taken} of 7 days.")
    else:
        signal(watch, "routine", "Vitamin cue needs reinforcement", f"Recorded on {vitamins_taken} of 7 days.")
        signal(priorities, "routine", "Keep vitamins inside Full Wake", "Use the 7:00 AM Jarvis cue until the routine is automatic.")
    if workouts_done["n"] >= 3:
        signal(wins, "body", "Training cadence is on line", f"{workouts_done['n']} workouts completed.")
    elif workouts_done["n"]:
        signal(watch, "body", "Training volume is light", f"{workouts_done['n']} workout{'s' if workouts_done['n'] != 1 else ''} completed.")
    if previous_spending and spent_week > previous_spending * 1.15:
        signal(watch, "money", "Spending accelerated", f"${spent_week:,.0f} vs ${previous_spending:,.0f} in the prior week.")
        signal(priorities, "money", "Review the spending delta", "Confirm the larger purchases before changing the paycheck plan.")
    elif spending_entries and previous_spending and spent_week <= previous_spending:
        signal(wins, "money", "Discretionary spending eased", f"Down ${previous_spending - spent_week:,.0f} week over week.")
    if confidence_score < 60:
        signal(watch, "system", "Evidence coverage is incomplete", f"Weekly confidence is {confidence_score} percent.")
        signal(priorities, "system", "Close the data gaps", "Log the missing daily signals before Jarvis changes any recommendation.")

    next_pay = payday_schedule(today, 1)[0]
    if next_pay["days_away"] <= 7:
        signal(priorities, "money", f"Stage {next_pay['label']}", f"${next_pay['amount']:,.2f} is expected in {next_pay['days_away']} days.")
    priorities = priorities[:3]
    if not priorities:
        signal(priorities, "system", "Maintain the current rhythm", "No material correction is supported by this week's evidence.")

    if operating_score is None:
        verdict = "Evidence incomplete — holding the weekly verdict"
    elif operating_score >= 80:
        verdict = "Strong operating week"
    elif operating_score >= 60:
        verdict = "Stable week with a few open loops"
    else:
        verdict = "Recovery week — simplify and rebuild the floor"

    parts = [f"Giovanni, weekly intelligence. {verdict}."]
    if operating_score is not None:
        parts.append(f"Operating score {operating_score} out of 100.")
    if weight_delta is not None:
        direction = "down" if weight_delta < 0 else "up"
        parts.append(f"Weight is {direction} {abs(weight_delta)} pounds.")
    if protein_days:
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
    if wins:
        parts.append(f"Top win: {wins[0]['title'].lower()}. {wins[0]['evidence']}")
    parts.append(f"Next priority: {priorities[0]['title'].lower()}. {priorities[0]['evidence']}")
    if confidence_label == "limited":
        parts.append("Evidence is limited, so Jarvis is holding recommendations conservative.")

    return {
        "period_start": period_start,
        "period_end": today.isoformat(),
        "previous_period": {"start": previous_start, "end": previous_end},
        "verdict": verdict,
        "operating_score": operating_score,
        "confidence": {
            "score": confidence_score,
            "label": confidence_label,
            "coverage": coverage_inputs,
        },
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
        "target_days": {
            "protein": protein_target_days,
            "steps": step_target_days,
            "vitamins": vitamins_taken,
        },
        "activity": {
            "meal_entries": meal_entries,
            "spending_entries": spending_entries,
        },
        "trends": {
            "protein": trend(avg_protein, previous_protein),
            "steps": trend(avg_steps, previous_steps),
            "spending": trend(spent_week, previous_spending, lower_is_better=True),
            "workouts": trend(workouts_done["n"], previous_workouts["n"]),
        },
        "wins": wins,
        "watch": watch,
        "priorities": priorities,
        "next_payday": next_pay,
        "finance_state": {
            "audit_health": budget["audit_health"],
            "safe_to_spend": budget["safe_to_spend"],
        },
        "policy": "Recommendations are advisory. Manual control and confirmed data remain authoritative.",
        "speech": " ".join(parts),
    }


class MemoryIn(BaseModel):
    fact: str


class LocalSpeechIn(BaseModel):
    text: str


async def _piper_wav(text: str) -> bytes:
    """Synthesize speech through the local Wyoming Piper service."""
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.client import AsyncClient
    from wyoming.tts import Synthesize

    client = AsyncClient.from_uri(PIPER_URI)
    audio_start = None
    chunks = bytearray()
    try:
        await asyncio.wait_for(client.connect(), timeout=5)
        await client.write_event(Synthesize(text=text).event())
        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=45)
            if event is None:
                raise RuntimeError("Piper closed the audio stream")
            if AudioStart.is_type(event.type):
                audio_start = AudioStart.from_event(event)
            elif AudioChunk.is_type(event.type):
                chunks.extend(AudioChunk.from_event(event).audio)
            elif AudioStop.is_type(event.type):
                break
    finally:
        await client.disconnect()

    if audio_start is None or not chunks:
        raise RuntimeError("Piper returned no audio")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(audio_start.channels)
        wav_file.setsampwidth(audio_start.width)
        wav_file.setframerate(audio_start.rate)
        wav_file.writeframes(bytes(chunks))
    return output.getvalue()


@router.post("/speech/local")
async def local_speech(body: LocalSpeechIn):
    """Return local Piper audio for playback on the requesting X1 browser."""
    text = re.sub(r"(?i)\bsir\b", "Giovanni", body.text).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        raise HTTPException(400, "speech text cannot be empty")
    if len(text) > 6000:
        raise HTTPException(400, "speech text is too long")
    try:
        audio = await _piper_wav(text)
    except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
        raise HTTPException(503, "local Piper voice is unavailable") from exc
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


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

    body = daily_loop()
    readiness = body["readiness"]
    targets = body["targets"]
    body_readiness_speech = (
        f"Giovanni, Body Ops readiness is {readiness['score']} out of 100, "
        f"in the {readiness['band']} range, with {readiness['confidence_percent']} percent "
        f"signal confidence. {targets['workout_guidance']} "
        "Missing data is treated as unknown, not as failure."
    )
    dinner = body["morning"].get("dinner")
    body_morning_parts = [
        f"Giovanni, {body['morning']['affirmation']}",
        body["morning"]["vitamins"],
        body["morning"]["hydration"],
    ]
    if dinner:
        body_morning_parts.append(dinner["prep"])
    body_morning_parts.append(
        f"Today's movement goal is {targets['steps']:,} steps, "
        f"with {targets['protein_g']} grams of protein and {targets['water_glasses']} glasses of water."
    )
    body_morning_speech = " ".join(body_morning_parts)
    body_evening_speech = " ".join(
        ["Giovanni, here is your Body Ops review."]
        + body["evening"]["wins"]
        + body["evening"]["remaining"]
        + [body["evening"]["tomorrow"]]
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
        "body_readiness": body_readiness_speech,
        "body_morning": body_morning_speech,
        "body_evening": body_evening_speech,
        "budget": budget["budget"],
        "paycheck": budget["paycheck"],
        "networth": budget["networth"],
        "safe_to_spend": budget["safe_to_spend"],
        "audit_health": budget["audit_health"],
        "nudge": nudge_row["text"] if nudge_row else "",
        "nudge_id": nudge_row["id"] if nudge_row else 0,
    }
