"""Cross-domain, evidence-backed habit patterns for Giovanni's Learning Ledger."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import json
from statistics import median
from typing import Any

from .db import active_profile, conn


def _clock(minutes: int) -> str:
    minutes %= 1440
    hour, minute = divmod(minutes, 60)
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def _time_minutes(value: str) -> int | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.hour * 60 + parsed.minute
    except (TypeError, ValueError):
        return None


def _confidence(samples: int, consistency: float = 1.0) -> float:
    depth = min(1.0, samples / 8)
    return round(min(0.95, 0.25 + depth * 0.5 + max(0, min(1, consistency)) * 0.2), 2)


def _pattern(
    pattern_id: str,
    domain: str,
    subject: str,
    conclusion: str,
    samples: int,
    evidence: str,
    *,
    consistency: float = 1.0,
) -> dict[str, Any]:
    return {
        "id": pattern_id,
        "domain": domain,
        "subject": subject,
        "conclusion": conclusion,
        "sample_count": samples,
        "confidence": _confidence(samples, consistency),
        "status": "established" if samples >= 3 else "emerging",
        "evidence": evidence,
        "authority": "advisory_only",
    }


def record_commute_observation(profile_id: int, commute: dict | None) -> None:
    """Keep one revisable commute observation per workday, never raw location history."""
    if not commute or not commute.get("minutes") or not commute.get("source"):
        return
    with conn() as database:
        database.execute(
            "INSERT INTO commute_history(date,profile_id,minutes,miles,source,"
            "traffic_live,planned_departure,observed_at)"
            " VALUES(?,?,?,?,?,?,?,datetime('now','localtime'))"
            " ON CONFLICT(date,profile_id) DO UPDATE SET"
            " minutes=excluded.minutes,miles=excluded.miles,source=excluded.source,"
            " traffic_live=excluded.traffic_live,"
            " planned_departure=excluded.planned_departure,"
            " observed_at=datetime('now','localtime')",
            (
                date.today().isoformat(), profile_id, float(commute["minutes"]),
                commute.get("miles"), str(commute["source"]),
                int(bool(commute.get("traffic_live"))),
                commute.get("planned_departure"),
            ),
        )
        database.execute(
            "DELETE FROM commute_history WHERE profile_id=? AND date<?",
            (profile_id, (date.today() - timedelta(days=180)).isoformat()),
        )


def _routine_patterns(database) -> list[dict]:
    rows = database.execute(
        "SELECT ts,state,previous_state FROM context_events"
        " WHERE entity_id LIKE 'person.%' AND ts>=datetime('now','-90 days')"
        " ORDER BY ts",
    ).fetchall()
    buckets: dict[str, list[int]] = {"arrival": [], "departure": []}
    for row in rows:
        state = str(row["state"] or "").lower()
        previous = str(row["previous_state"] or "").lower()
        kind = None
        if state == "home" and previous == "not_home":
            kind = "arrival"
        elif state == "not_home" and previous == "home":
            kind = "departure"
        minute = _time_minutes(row["ts"])
        if kind and minute is not None:
            buckets[kind].append(minute)
    patterns = []
    for kind, values in buckets.items():
        if not values:
            continue
        center = round(median(values))
        spread = round(median(abs(value - center) for value in values)) if len(values) > 1 else 0
        patterns.append(_pattern(
            f"routine:{kind}", "routine", f"Typical {kind}",
            f"Usually around {_clock(center)}",
            len(values), f"{len(values)} presence transition(s) in 90 days; median variation {spread} minutes.",
            consistency=max(0.1, 1 - spread / 180),
        ))
    return patterns


def _commute_patterns(database, profile_id: int) -> list[dict]:
    rows = database.execute(
        "SELECT * FROM commute_history WHERE profile_id=? AND date>=? ORDER BY date",
        (profile_id, (date.today() - timedelta(days=60)).isoformat()),
    ).fetchall()
    if not rows:
        return []
    minutes = [float(row["minutes"]) for row in rows]
    center = round(median(minutes))
    departures = [row["planned_departure"] for row in rows if row["planned_departure"]]
    traffic_days = sum(bool(row["traffic_live"]) for row in rows)
    variation = round(max(minutes) - min(minutes)) if len(minutes) > 1 else 0
    conclusion = f"Typical drive is about {center} minutes"
    if departures:
        conclusion += f"; recent leave-by time is {departures[-1]}"
    return [_pattern(
        "commute:home_to_work", "commute", "Home-to-work timing", conclusion,
        len(rows),
        f"{len(rows)} workday route sample(s), {traffic_days} with live traffic; range {variation} minutes.",
        consistency=max(0.1, 1 - variation / 60),
    )]


def _food_patterns(database, profile_id: int) -> list[dict]:
    rows = database.execute(
        "SELECT name,COUNT(*) samples,COUNT(DISTINCT date(ts)) days"
        " FROM meal_log WHERE profile_id=? AND ts>=datetime('now','-60 days')"
        " GROUP BY lower(name) HAVING COUNT(*)>=2 ORDER BY samples DESC LIMIT 5",
        (profile_id,),
    ).fetchall()
    return [
        _pattern(
            f"food:{str(row['name']).lower()}", "food", "Repeated meal",
            f"{row['name']} is part of your recurring food pattern",
            int(row["samples"]), f"Logged {row['samples']} time(s) across {row['days']} day(s) in 60 days.",
        )
        for row in rows
    ]


def _body_patterns(database, profile_id: int) -> list[dict]:
    patterns = []
    steps = database.execute(
        "SELECT count FROM steps WHERE profile_id=? AND date>=?",
        (profile_id, (date.today() - timedelta(days=28)).isoformat()),
    ).fetchall()
    if steps:
        average = round(sum(int(row["count"]) for row in steps) / len(steps))
        patterns.append(_pattern(
            "body:steps", "body_ops", "Recent movement baseline",
            f"Average recorded day is {average:,} steps", len(steps),
            f"Derived from {len(steps)} recorded day(s) in the last 28 days.",
        ))
    vitamins = database.execute(
        "SELECT COUNT(*) days,SUM(taken) taken FROM vitamins"
        " WHERE profile_id=? AND date>=?",
        (profile_id, (date.today() - timedelta(days=28)).isoformat()),
    ).fetchone()
    if vitamins and vitamins["days"]:
        rate = round(100 * int(vitamins["taken"] or 0) / int(vitamins["days"]))
        patterns.append(_pattern(
            "body:vitamins", "body_ops", "Vitamin consistency",
            f"Vitamins were logged on {rate}% of recorded days", int(vitamins["days"]),
            f"{vitamins['taken']} taken day(s) out of {vitamins['days']} recorded.",
            consistency=rate / 100,
        ))
    workouts = database.execute(
        "SELECT ts FROM workouts WHERE profile_id=? AND ts>=datetime('now','-60 days')",
        (profile_id,),
    ).fetchall()
    times = [minute for row in workouts if (minute := _time_minutes(row["ts"])) is not None]
    if times:
        center = round(median(times))
        patterns.append(_pattern(
            "body:workout_time", "body_ops", "Workout timing",
            f"Workouts tend to happen around {_clock(center)}", len(times),
            f"Derived from {len(times)} completed workout(s) in 60 days.",
        ))
    return patterns


def _finance_patterns(database) -> list[dict]:
    rows = database.execute(
        "SELECT merchant,COUNT(*) samples,AVG(amount) average,SUM(amount) total"
        " FROM spending WHERE ts>=datetime('now','-90 days') AND trim(merchant)<>''"
        " GROUP BY lower(merchant) HAVING COUNT(*)>=2 ORDER BY samples DESC LIMIT 4",
    ).fetchall()
    return [
        _pattern(
            f"finance:{str(row['merchant']).lower()}", "finance", "Recurring merchant",
            f"{row['merchant']} averages ${float(row['average']):,.2f} per logged purchase",
            int(row["samples"]),
            f"{row['samples']} purchase(s), ${float(row['total']):,.2f} total in 90 days.",
        )
        for row in rows
    ]


def _home_media_patterns(database) -> list[dict]:
    patterns = []
    modes = database.execute(
        "SELECT state,COUNT(*) samples FROM context_events"
        " WHERE ts>=datetime('now','-90 days')"
        " AND (event_type='sanctuary_mode_changed' OR entity_id='input_select.sanctuary_mode')"
        " AND trim(COALESCE(state,''))<>'' GROUP BY state HAVING COUNT(*)>=2"
        " ORDER BY samples DESC LIMIT 4",
    ).fetchall()
    for row in modes:
        patterns.append(_pattern(
            f"home:mode:{str(row['state']).lower()}", "home", "Frequently used Sanctuary mode",
            f"{row['state']} is a recurring apartment mode", int(row["samples"]),
            f"Observed {row['samples']} mode transition(s) in 90 days.",
        ))
    media_rows = database.execute(
        "SELECT attributes_json FROM context_events WHERE ts>=datetime('now','-90 days')"
        " AND entity_id LIKE 'media_player.%' AND state='playing' LIMIT 500",
    ).fetchall()
    sources = Counter()
    for row in media_rows:
        try:
            attributes = json.loads(row["attributes_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        source = attributes.get("source") or attributes.get("app_name")
        if source:
            sources[str(source)] += 1
    for source, samples in sources.most_common(4):
        if samples < 2:
            continue
        patterns.append(_pattern(
            f"media:{source.lower()}", "media", "Frequently used media source",
            f"{source} appears regularly in playback", samples,
            f"Observed {samples} playing-state event(s) in 90 days.",
        ))
    return patterns


def pattern_snapshot() -> dict[str, Any]:
    with conn() as database:
        profile = active_profile(database)
        patterns = [
            *_routine_patterns(database),
            *_commute_patterns(database, profile["id"]),
            *_food_patterns(database, profile["id"]),
            *_body_patterns(database, profile["id"]),
            *_finance_patterns(database),
            *_home_media_patterns(database),
        ]
    patterns.sort(key=lambda item: (item["status"] != "established", -item["confidence"], item["domain"]))
    observed = sorted({item["domain"] for item in patterns})
    expected = ["routine", "commute", "food", "body_ops", "finance", "home", "media"]
    return {
        "patterns": patterns,
        "summary": {
            "total": len(patterns),
            "established": sum(item["status"] == "established" for item in patterns),
            "emerging": sum(item["status"] == "emerging" for item in patterns),
        },
        "coverage": {
            "observed_domains": observed,
            "awaiting_evidence": [domain for domain in expected if domain not in observed],
        },
        "policy": {
            "derived_locally": True,
            "minimum_samples_for_established": 3,
            "patterns_authorize_actions": False,
            "raw_audio_stored": False,
            "camera_frames_stored": False,
            "identity_recognition": False,
            "exact_location_history_stored": False,
        },
    }
