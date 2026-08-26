"""Explainable Body Ops readiness, trends, habits, and daily protocols."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any

from .chef import chef_summary
from .db import active_profile, conn, get_setting


def _day(offset: int = 0) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _daily_metrics(c, profile_id: int, day: str) -> dict[str, Any]:
    meal = c.execute(
        "SELECT COALESCE(SUM(protein_g),0) protein, COALESCE(SUM(calories),0) calories,"
        " COUNT(*) meals FROM meal_log WHERE date(ts)=? AND profile_id=?",
        (day, profile_id),
    ).fetchone()
    steps = c.execute(
        "SELECT count FROM steps WHERE date=? AND profile_id=?", (day, profile_id)
    ).fetchone()
    water = c.execute(
        "SELECT glasses FROM water WHERE date=? AND profile_id=?", (day, profile_id)
    ).fetchone()
    vitamins = c.execute(
        "SELECT taken FROM vitamins WHERE date=? AND profile_id=?", (day, profile_id)
    ).fetchone()
    workouts = c.execute(
        "SELECT COALESCE(SUM(minutes),0) minutes, COUNT(*) sessions FROM workouts"
        " WHERE date(ts)=? AND profile_id=?",
        (day, profile_id),
    ).fetchone()
    return {
        "date": day,
        "protein_g": float(meal["protein"]),
        "calories": float(meal["calories"]),
        "meals": int(meal["meals"]),
        "steps": int(steps["count"]) if steps else None,
        "water_glasses": int(water["glasses"]) if water else None,
        "vitamins_taken": bool(vitamins and vitamins["taken"]),
        "vitamins_logged": vitamins is not None,
        "workout_minutes": int(workouts["minutes"]),
        "workout_sessions": int(workouts["sessions"]),
    }


def weight_trends(c, profile_id: int) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in c.execute(
            "SELECT ts,weight_lb,source FROM weighins WHERE profile_id=?"
            " ORDER BY datetime(ts) DESC LIMIT 180",
            (profile_id,),
        ).fetchall()
    ]
    now = datetime.now()
    windows = {}
    for days in (7, 30, 90):
        values = []
        for row in rows:
            try:
                observed = datetime.fromisoformat(str(row["ts"]))
            except ValueError:
                continue
            if now - observed <= timedelta(days=days):
                values.append(float(row["weight_lb"]))
        windows[str(days)] = {
            "average_lb": round(mean(values), 1) if values else None,
            "samples": len(values),
        }
    latest = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    return {
        "latest": latest,
        "previous": previous,
        "change_since_previous_lb": round(
            float(latest["weight_lb"]) - float(previous["weight_lb"]), 1
        )
        if latest and previous
        else None,
        "windows": windows,
        "history": rows[:30],
        "interpretation": "Weight direction is informational; Jarvis emphasizes the multi-week trend, not one reading.",
    }


def readiness_snapshot() -> dict[str, Any]:
    with conn() as c:
        profile = active_profile(c)
        pid = profile["id"]
        prior = _daily_metrics(c, pid, _day(-1))
        checkin = c.execute(
            "SELECT * FROM body_checkins WHERE date=? AND profile_id=?",
            (_day(), pid),
        ).fetchone()
        checkin = dict(checkin) if checkin else {}
        weights = weight_trends(c, pid)
        protein_target = float(profile["protein_target_g"])
        step_target = int(profile["step_target"])
        water_target = int(get_setting("water_target_glasses") or 8)

    components: list[dict[str, Any]] = []

    def add(key: str, label: str, points: float, maximum: float, available: bool, reason: str, source: str) -> None:
        components.append(
            {
                "id": key,
                "label": label,
                "points": round(_clamp(points, 0, maximum), 1),
                "maximum": maximum,
                "available": available,
                "reason": reason,
                "source": source,
            }
        )

    sleep = checkin.get("sleep_hours")
    if sleep is None:
        add("sleep", "Sleep", 15, 25, False, "No sleep check-in yet; neutral credit used.", "awaiting self-report")
    else:
        sleep = float(sleep)
        points = 25 if 7 <= sleep <= 9 else 20 if 6 <= sleep <= 10 else 12 if 5 <= sleep <= 11 else 5
        add("sleep", "Sleep", points, 25, True, f"{sleep:.1f} hours reported.", checkin.get("source", "manual"))

    energy = checkin.get("energy")
    add(
        "energy",
        "Energy",
        float(energy) * 3 if energy is not None else 9,
        15,
        energy is not None,
        f"Energy {energy}/5." if energy is not None else "No energy check-in; neutral credit used.",
        checkin.get("source", "awaiting self-report"),
    )
    soreness = checkin.get("soreness")
    add(
        "recovery",
        "Recovery",
        (6 - float(soreness)) * 2 if soreness is not None else 6,
        10,
        soreness is not None,
        f"Soreness {soreness}/5." if soreness is not None else "No soreness check-in; neutral credit used.",
        checkin.get("source", "awaiting self-report"),
    )

    protein_available = prior["meals"] > 0
    protein_ratio = prior["protein_g"] / max(1, protein_target)
    add(
        "protein",
        "Prior-day protein",
        15 * min(1, protein_ratio) if protein_available else 9,
        15,
        protein_available,
        f"{prior['protein_g']:.0f} of {protein_target:.0f} g logged yesterday."
        if protein_available
        else "No meals logged yesterday; neutral credit used.",
        "LifeOS meal ledger",
    )
    steps_available = prior["steps"] is not None
    add(
        "movement",
        "Prior-day movement",
        10 * min(1, (prior["steps"] or 0) / max(1, step_target)) if steps_available else 6,
        10,
        steps_available,
        f"{prior['steps']:,} of {step_target:,} steps yesterday." if steps_available else "No prior-day steps received; neutral credit used.",
        "recorded or imported steps",
    )
    water_available = prior["water_glasses"] is not None
    add(
        "hydration",
        "Prior-day hydration",
        10 * min(1, (prior["water_glasses"] or 0) / max(1, water_target)) if water_available else 6,
        10,
        water_available,
        f"{prior['water_glasses']} of {water_target} glasses yesterday." if water_available else "No prior-day hydration log; neutral credit used.",
        "LifeOS hydration ledger",
    )
    add(
        "vitamins",
        "Prior-day vitamins",
        5 if prior["vitamins_taken"] else (0 if prior["vitamins_logged"] else 3),
        5,
        prior["vitamins_logged"],
        "Taken yesterday." if prior["vitamins_taken"] else "Not marked taken yesterday." if prior["vitamins_logged"] else "No prior-day vitamin record; neutral credit used.",
        "LifeOS vitamin ledger",
    )
    latest_weight = weights["latest"]
    weight_fresh = False
    if latest_weight:
        try:
            weight_fresh = datetime.now() - datetime.fromisoformat(str(latest_weight["ts"])) <= timedelta(days=14)
        except ValueError:
            pass
    add(
        "weight_data",
        "Weight trend freshness",
        10 if weight_fresh else 6,
        10,
        bool(latest_weight),
        "A recent measured trend is available." if weight_fresh else "No weight reading in the last 14 days; neutral credit used.",
        str(latest_weight.get("source")) if latest_weight else "awaiting scale or manual entry",
    )

    score = round(sum(item["points"] for item in components))
    available_weight = sum(item["maximum"] for item in components if item["available"])
    confidence = round(available_weight / sum(item["maximum"] for item in components) * 100)
    band = "ready" if score >= 80 else "steady" if score >= 65 else "conserve" if score >= 45 else "recover"
    return {
        "score": score,
        "band": band,
        "confidence_percent": confidence,
        "components": components,
        "checkin": checkin or None,
        "prior_day": prior,
        "weights": weights,
        "explanation": "Readiness uses recovery signals and the previous completed day so an unfinished morning is not treated as failure.",
        "medical_policy": "Wellness guidance only; not diagnosis or medical advice.",
    }


def adaptive_targets(readiness: dict[str, Any] | None = None) -> dict[str, Any]:
    readiness = readiness or readiness_snapshot()
    score = int(readiness["score"])
    with conn() as c:
        profile = active_profile(c)
        water = int(get_setting("water_target_glasses") or 8)
    factor = 1.0 if score >= 75 else 0.85 if score >= 55 else 0.7
    steps = max(4000, round(int(profile["step_target"]) * factor / 500) * 500)
    workout = (
        "Normal planned session; stop or scale down if your body disagrees."
        if score >= 75
        else "Choose an easy walk, mobility, or a shorter session."
        if score >= 55
        else "Recovery emphasis: gentle movement only unless you feel clearly better."
    )
    return {
        "protein_g": int(round(float(profile["protein_target_g"]))),
        "steps": steps,
        "water_glasses": water,
        "calories": int(profile["calorie_target"]),
        "workout_guidance": workout,
        "reason": f"Step and training guidance adapted to readiness {score}; protein, hydration, and calories were not reduced.",
        "confirmation": "Targets are suggestions and never override symptoms, clinician guidance, or manual choice.",
    }


def habit_insights(days: int = 28) -> list[dict[str, Any]]:
    cutoff = _day(-(max(7, min(days, 90)) - 1))
    with conn() as c:
        profile = active_profile(c)
        pid = profile["id"]
        protein = c.execute(
            "SELECT date(ts) day,SUM(protein_g) value FROM meal_log WHERE profile_id=?"
            " AND date(ts)>=? GROUP BY date(ts)",
            (pid, cutoff),
        ).fetchall()
        steps = c.execute(
            "SELECT date day,count value FROM steps WHERE profile_id=? AND date>=?",
            (pid, cutoff),
        ).fetchall()
        water = c.execute(
            "SELECT date day,glasses value FROM water WHERE profile_id=? AND date>=?",
            (pid, cutoff),
        ).fetchall()
        vitamins = c.execute(
            "SELECT date day,taken value FROM vitamins WHERE profile_id=? AND date>=?",
            (pid, cutoff),
        ).fetchall()
        targets = {
            "protein": float(profile["protein_target_g"]),
            "steps": int(profile["step_target"]),
            "water": int(get_setting("water_target_glasses") or 8),
        }
    datasets = {"protein": protein, "steps": steps, "water": water, "vitamins": vitamins}
    insights = []
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for metric, rows in datasets.items():
        by_weekday: dict[int, list[float]] = defaultdict(list)
        for row in rows:
            try:
                weekday = date.fromisoformat(row["day"]).weekday()
            except ValueError:
                continue
            by_weekday[weekday].append(float(row["value"]))
        if len(rows) < 3 or not by_weekday:
            continue
        averages = {weekday: mean(values) for weekday, values in by_weekday.items()}
        lowest = min(averages, key=averages.get)
        if metric == "vitamins":
            wording = f"On logged {weekday_names[lowest]}s, vitamins were marked taken {averages[lowest] * 100:.0f}% of the time."
            suggestion = "Put the vitamin cue into the 7:00 AM launch briefing on that weekday."
        else:
            unit = "g" if metric == "protein" else "steps" if metric == "steps" else "glasses"
            wording = f"{weekday_names[lowest]} is the lowest logged {metric} day, averaging {averages[lowest]:.0f} {unit}."
            suggestion = {
                "protein": "Pre-select a pantry-backed protein option the night before.",
                "steps": "Use one short walk cue rather than a large end-of-day catch-up.",
                "water": "Stage the first glass with the morning vitamin cue.",
            }[metric]
        insights.append(
            {
                "metric": metric,
                "pattern": wording,
                "suggestion": suggestion,
                "samples": len(rows),
                "confidence": "developing" if len(rows) < 10 else "useful",
                "caution": "Based only on logged data; missing entries are not assumed failures.",
            }
        )
    return insights[:6]


def body_timeline(limit: int = 60) -> list[dict[str, Any]]:
    limit = max(10, min(limit, 200))
    with conn() as c:
        pid = active_profile(c)["id"]
        events = []
        queries = [
            ("meal", "SELECT ts,protein_g value,name label FROM meal_log WHERE profile_id=?", "g protein", "recorded"),
            ("workout", "SELECT ts,minutes value,kind label FROM workouts WHERE profile_id=?", "minutes", "recorded"),
        ]
        for kind, query, unit, quality in queries:
            for row in c.execute(query, (pid,)).fetchall():
                item = dict(row)
                events.append({"kind": kind, "ts": item["ts"], "value": item["value"], "unit": unit, "label": item["label"], "quality": quality})
        for row in c.execute(
            "SELECT ts,weight_lb,source FROM weighins WHERE profile_id=?", (pid,)
        ).fetchall():
            source = str(row["source"] or "manual")
            quality = (
                "measured"
                if source.startswith("home_assistant:")
                else "imported"
                if source == "health_auto_export"
                else "self-reported"
            )
            events.append({"kind": "weight", "ts": row["ts"], "value": row["weight_lb"], "unit": "lb", "label": source, "quality": quality})
        for row in c.execute("SELECT date, count FROM steps WHERE profile_id=?", (pid,)).fetchall():
            events.append({"kind": "steps", "ts": row["date"] + " 20:00:00", "value": row["count"], "unit": "steps", "label": "Daily steps", "quality": "recorded or imported"})
        for row in c.execute("SELECT date, glasses FROM water WHERE profile_id=?", (pid,)).fetchall():
            events.append({"kind": "water", "ts": row["date"] + " 20:01:00", "value": row["glasses"], "unit": "glasses", "label": "Daily hydration", "quality": "self-reported"})
        for row in c.execute("SELECT date,taken FROM vitamins WHERE profile_id=?", (pid,)).fetchall():
            events.append({"kind": "vitamins", "ts": row["date"] + " 07:00:00", "value": bool(row["taken"]), "unit": "", "label": "Vitamins taken" if row["taken"] else "Vitamins pending", "quality": "self-reported"})
        for row in c.execute("SELECT * FROM body_checkins WHERE profile_id=?", (pid,)).fetchall():
            events.append({"kind": "checkin", "ts": row["date"] + " 06:30:00", "value": row["energy"], "unit": "/5 energy", "label": f"Sleep {row['sleep_hours'] or '—'}h · soreness {row['soreness'] or '—'}/5", "quality": "self-reported"})
    events.sort(key=lambda item: item["ts"], reverse=True)
    return events[:limit]


def daily_loop() -> dict[str, Any]:
    readiness = readiness_snapshot()
    targets = adaptive_targets(readiness)
    with conn() as c:
        pid = active_profile(c)["id"]
        today = _daily_metrics(c, pid, _day())
    chef = chef_summary()
    suggestion = chef["suggestions"][0] if chef.get("suggestions") else None
    affirmations = [
        "Consistency beats intensity. One useful choice at a time.",
        "You do not need a perfect day to create momentum.",
        "Protect the routine, and the results will follow.",
        "Start calmly, execute deliberately, and let progress compound.",
        "Today only needs your next good decision.",
    ]
    affirmation = affirmations[date.today().toordinal() % len(affirmations)]
    dinner = None
    if suggestion:
        dinner = {
            "name": suggestion["name"],
            "protein_g": suggestion["protein_g"],
            "stock_coverage": suggestion["stock_coverage"],
            "prep": f"Set aside ingredients for {suggestion['name']} this morning."
            if suggestion["stock_coverage"] >= 60
            else f"{suggestion['name']} needs market-list review before dinner.",
            "why": suggestion["why"],
        }
    completion = {
        "protein_percent": round(min(100, today["protein_g"] / max(1, targets["protein_g"]) * 100)),
        "steps_percent": round(min(100, (today["steps"] or 0) / max(1, targets["steps"]) * 100)),
        "water_percent": round(min(100, (today["water_glasses"] or 0) / max(1, targets["water_glasses"]) * 100)),
        "vitamins_taken": today["vitamins_taken"],
    }
    evening_wins = []
    if completion["protein_percent"] >= 80:
        evening_wins.append("Protein is at least 80% complete.")
    if completion["steps_percent"] >= 80:
        evening_wins.append("Movement is at least 80% complete.")
    if completion["water_percent"] >= 80:
        evening_wins.append("Hydration is at least 80% complete.")
    if today["vitamins_taken"]:
        evening_wins.append("Vitamins were completed.")
    remaining = []
    if not today["vitamins_taken"]:
        remaining.append("Vitamins are not marked taken.")
    if completion["protein_percent"] < 80:
        remaining.append(f"About {max(0, targets['protein_g'] - round(today['protein_g']))} g protein remains to reach the adaptive target.")
    if completion["water_percent"] < 80:
        remaining.append(f"{max(0, targets['water_glasses'] - (today['water_glasses'] or 0))} hydration glasses remain.")
    return {
        "readiness": readiness,
        "targets": targets,
        "today": today,
        "completion": completion,
        "morning": {
            "affirmation": affirmation,
            "vitamins": "Take your vitamins during the 7:00 AM launch window." if not today["vitamins_taken"] else "Vitamins already complete.",
            "hydration": "Begin with one glass of water.",
            "dinner": dinner,
        },
        "evening": {
            "wins": evening_wins or ["The day is still editable; choose one small completion."],
            "remaining": remaining or ["Core Body Ops targets are in good shape."],
            "tomorrow": dinner["prep"] if dinner else "Update the pantry so Jarvis can prepare tomorrow's dinner option.",
        },
        "habits": habit_insights(),
        "timeline": body_timeline(),
        "data_policy": {
            "measured_vs_reported": True,
            "missing_is_not_failure": True,
            "local_storage": True,
            "medical_advice": False,
        },
    }
