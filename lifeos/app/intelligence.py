"""Explainable cross-domain decision support for Jarvis.

This module deliberately does not execute actions.  It turns the existing
house, body, food, finance, and learning snapshots into a small prioritized
operating picture.  Every conclusion carries its evidence and authority
boundary so a missing or stale signal cannot quietly become an automation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from statistics import median
from typing import Any


SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _clock(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clock_distance(left: int, right: int) -> int:
    distance = abs(left - right)
    return min(distance, 24 * 60 - distance)


def temporal_patterns(
    snapshot: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Extract cautious routine windows from retained context events.

    Three observations are required before a routine is called established.
    Patterns remain advisory metadata and are never converted into occupancy or
    action authorization.
    """
    now = now or datetime.now().astimezone()
    events = list(snapshot.get("recent_events") or [])
    buckets: dict[tuple[str, str], list[tuple[datetime, int]]] = {}
    for event in events:
        entity_id = str(event.get("entity_id") or "")
        if not entity_id.startswith("person."):
            continue
        state = str(event.get("state") or "").lower()
        previous = str(event.get("previous_state") or "").lower()
        transition = None
        if state in {"home", "on"} and previous in {"not_home", "off"}:
            transition = "arrival"
        elif state in {"not_home", "off"} and previous in {"home", "on"}:
            transition = "departure"
        if not transition:
            continue
        timestamp = _parse_timestamp(event.get("ts"))
        if not timestamp:
            continue
        minutes = timestamp.hour * 60 + timestamp.minute
        buckets.setdefault((entity_id, transition), []).append((timestamp, minutes))

    patterns = []
    deviations = []
    for (entity_id, transition), observations in sorted(buckets.items()):
        values = [item[1] for item in observations]
        center = int(round(median(values)))
        dispersion = int(round(median([_clock_distance(value, center) for value in values])))
        spread = max(30, min(120, max(1, dispersion) * 2))
        established = len(values) >= 3
        confidence = round(min(0.92, 0.28 + len(values) * 0.12), 2) if established else round(0.18 + len(values) * 0.08, 2)
        latest_timestamp, latest_minutes = max(observations, key=lambda item: item[0])
        distance = _clock_distance(latest_minutes, center)
        pattern_id = f"{entity_id}:{transition}"
        pattern = {
            "id": pattern_id,
            "entity_id": entity_id,
            "kind": transition,
            "sample_count": len(values),
            "established": established,
            "confidence": confidence,
            "usual_time": _clock(center),
            "usual_window": {
                "start": _clock(center - spread),
                "end": _clock(center + spread),
            },
            "last_observed_at": latest_timestamp.isoformat(sep=" ", timespec="seconds"),
            "last_observed_time": _clock(latest_minutes),
        }
        patterns.append(pattern)
        threshold = max(90, spread * 2)
        if established and distance > threshold:
            deviations.append({
                "id": f"routine_deviation:{pattern_id}",
                "pattern_id": pattern_id,
                "severity": "medium",
                "minutes_from_usual": distance,
                "claim": (
                    f"Latest {transition} at {_clock(latest_minutes)} is {distance} minutes "
                    f"outside the learned {_clock(center - spread)}–{_clock(center + spread)} window."
                ),
                "confidence": confidence,
                "observed_at": latest_timestamp.isoformat(sep=" ", timespec="seconds"),
            })
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "patterns": patterns,
        "deviations": deviations,
        "summary": {
            "candidates": len(patterns),
            "established": sum(item["established"] for item in patterns),
            "deviations": len(deviations),
        },
        "policy": {
            "minimum_samples": 3,
            "advisory_only": True,
            "patterns_authorize_actions": False,
            "absence_of_events_is_not_evidence": True,
        },
    }


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _evidence(
    domain: str,
    fact: str,
    value: Any,
    *,
    source: str,
    observed_at: str | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "domain": domain,
        "fact": fact,
        "value": value,
        "source": source,
        "observed_at": observed_at,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
    }


def _signal(
    signal_id: str,
    domain: str,
    severity: str,
    title: str,
    detail: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    confidence = min((item["confidence"] for item in evidence), default=0.5)
    return {
        "id": signal_id,
        "domain": domain,
        "severity": severity,
        "title": title,
        "detail": detail,
        "confidence": round(confidence, 2),
        "evidence": evidence,
    }


def _recommendation(
    recommendation_id: str,
    priority: str,
    title: str,
    rationale: str,
    evidence: list[dict[str, Any]],
    *,
    action_id: str | None = None,
    horizon: str = "now",
) -> dict[str, Any]:
    confidence = min((item["confidence"] for item in evidence), default=0.5)
    return {
        "id": recommendation_id,
        "priority": priority,
        "horizon": horizon,
        "title": title,
        "rationale": rationale,
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "action_id": action_id,
        "authority": {
            "mode": "advisory_only",
            "can_execute": False,
            "requires_registered_action": action_id is not None,
            "gesture_can_confirm": False,
            "reason": "Decision support cannot authorize or execute a real-world action.",
        },
    }


def build_intelligence(
    snapshot: dict[str, Any],
    *,
    learning: dict[str, Any] | None = None,
    proposals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a deterministic, read-only intelligence assessment."""
    now = now or datetime.now().astimezone()
    proposals = proposals or []
    telemetry = snapshot.get("telemetry") or {}
    telemetry_state = str(telemetry.get("link_state") or "awaiting_data")
    telemetry_confidence = {
        "online": 0.96,
        "awaiting_data": 0.55,
        "stale": 0.35,
        "degraded": 0.25,
    }.get(telemetry_state, 0.4)
    observed_at = telemetry.get("last_event_at")
    occupancy = snapshot.get("occupancy") or {}
    security = snapshot.get("security") or {}
    sanctuary = snapshot.get("sanctuary") or {}
    perception = snapshot.get("perception") or {}
    voice = snapshot.get("voice") or {}
    lifeos = snapshot.get("lifeos") or {}
    body = lifeos.get("body") or {}
    vault = lifeos.get("vault") or {}
    food = lifeos.get("food") or {}

    signals: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    telemetry_evidence = _evidence(
        "system",
        "Home Assistant context link",
        telemetry_state,
        source="context_events",
        observed_at=observed_at,
        confidence=telemetry_confidence,
    )
    if telemetry_state != "online":
        evidence = [telemetry_evidence]
        signals.append(_signal(
            "telemetry_not_current", "system", "high", "Live context is not current",
            "Jarvis is suppressing confident situational conclusions until fresh Home Assistant events arrive.",
            evidence,
        ))
        recommendations.append(_recommendation(
            "restore_context_link", "high", "Restore the live context link",
            "Fresh telemetry is required before presence, security, or mode conflicts can be trusted.",
            evidence,
        ))

    open_perimeter = list(security.get("open_perimeter") or [])
    hazards = list(security.get("active_hazards") or [])
    if open_perimeter or hazards:
        evidence = [
            _evidence(
                "security", "open perimeter", open_perimeter,
                source="home_assistant", observed_at=observed_at,
                confidence=telemetry_confidence,
            ),
            _evidence(
                "security", "active hazards", hazards,
                source="home_assistant", observed_at=observed_at,
                confidence=telemetry_confidence,
            ),
        ]
        signals.append(_signal(
            "security_attention", "security", "critical", "Residence needs a security review",
            f"{len(open_perimeter)} perimeter opening(s) and {len(hazards)} active hazard(s) are reported.",
            evidence,
        ))
        recommendations.append(_recommendation(
            "review_security", "critical", "Review the residence before changing modes",
            "Security findings outrank atmosphere, media, and scheduled routines.",
            evidence,
            action_id="notify.departure_anomaly",
        ))

    mode = str(sanctuary.get("mode") or snapshot.get("house_mode") or "unknown")
    people_home = list(occupancy.get("people_home") or [])
    occupied = bool(occupancy.get("occupied"))
    visual_current = perception.get("link_state") == "online"
    visually_occupied = bool(perception.get("room_occupied")) if visual_current else None
    if mode in {"Away", "Vacation"} and (occupied or visually_occupied is True):
        evidence = [
            _evidence("home", "Sanctuary mode", mode, source="sanctuary", confidence=telemetry_confidence),
            _evidence("presence", "people home", people_home, source="home_assistant", confidence=telemetry_confidence),
        ]
        if visual_current:
            evidence.append(_evidence(
                "presence", "local visual presence", visually_occupied,
                source="x1_vision", observed_at=perception.get("last_observation_at"),
                confidence=_number(perception.get("confidence"), 0.5),
            ))
        conflict = {
            "id": "away_presence_conflict",
            "severity": "high",
            "claim": f"{mode} mode conflicts with current presence evidence.",
            "evidence": evidence,
        }
        conflicts.append(conflict)
        signals.append(_signal(
            conflict["id"], "presence", "high", "Presence and house mode disagree",
            conflict["claim"], evidence,
        ))
        recommendations.append(_recommendation(
            "resolve_away_presence", "high", "Confirm occupancy before Away actions",
            "Jarvis will not infer that the residence is vacant while a trusted presence source disagrees.",
            evidence,
        ))

    if visual_current and visually_occupied is not None and visually_occupied != occupied:
        evidence = [
            _evidence("presence", "Home Assistant occupancy", occupied, source="home_assistant", confidence=telemetry_confidence),
            _evidence(
                "presence", "local visual presence", visually_occupied,
                source="x1_vision", observed_at=perception.get("last_observation_at"),
                confidence=_number(perception.get("confidence"), 0.5),
            ),
        ]
        conflicts.append({
            "id": "presence_source_disagreement",
            "severity": "medium",
            "claim": "Phone presence and local visual presence disagree.",
            "evidence": evidence,
        })
        signals.append(_signal(
            "presence_source_disagreement", "presence", "medium",
            "Presence sources disagree", "Jarvis will wait for corroboration instead of changing occupancy-sensitive modes.",
            evidence,
        ))

    protein = _number(body.get("protein_g"))
    protein_target = max(1.0, _number(body.get("protein_target_g"), 100.0))
    protein_remaining = max(0.0, protein_target - protein)
    pantry_count = int(_number(food.get("pantry_item_count")))
    out_of_stock = list(food.get("out_of_stock") or [])
    low_stock = list(food.get("low_stock") or [])
    market_list = list(food.get("market_list") or [])
    if protein_remaining >= 25 and now.hour >= 15:
        evidence = [
            _evidence("body", "protein remaining grams", round(protein_remaining), source="meal_log"),
            _evidence("food", "pantry items", pantry_count, source="pantry"),
            _evidence("food", "out or low stock", out_of_stock + low_stock, source="pantry"),
        ]
        constrained = pantry_count == 0 or bool(out_of_stock)
        title = "Resolve dinner and the protein gap together" if constrained else "Use dinner to close the protein gap"
        rationale = (
            "Protein is still materially below target and pantry availability may constrain the dinner plan."
            if constrained
            else "The remaining protein target is large enough that dinner is the most efficient place to address it."
        )
        signals.append(_signal(
            "evening_fuel_gap", "body_food", "medium", title, rationale, evidence,
        ))
        recommendations.append(_recommendation(
            "plan_protein_dinner", "medium", title, rationale,
            evidence, horizon="next",
        ))

    vitamins_taken = bool(body.get("vitamins_taken"))
    if not vitamins_taken and not occupied and 5 <= now.hour <= 11:
        evidence = [
            _evidence("body", "vitamins logged", False, source="vitamins"),
            _evidence("presence", "residence occupied", False, source="home_assistant", confidence=telemetry_confidence),
        ]
        signals.append(_signal(
            "left_before_vitamins", "body_presence", "medium",
            "Morning routine may have been missed",
            "Giovanni appears away and vitamins have not been logged; Jarvis treats this as a reminder, not proof.",
            evidence,
        ))
        recommendations.append(_recommendation(
            "remind_vitamins", "medium", "Send a private vitamin reminder",
            "The morning cue is still incomplete after departure, but the evidence is not strong enough to mark it missed automatically.",
            evidence,
        ))

    bills = list(vault.get("bills_due_soon") or [])
    runway = _number(vault.get("left_after_due_bills"))
    if bills and runway < 0:
        evidence = [
            _evidence("finance", "bills due within seven days", len(bills), source="bills"),
            _evidence("finance", "runway after due bills", round(runway, 2), source="accounts"),
        ]
        signals.append(_signal(
            "negative_bill_runway", "finance", "high", "Upcoming bills exceed current cash",
            "The current account snapshot cannot cover every bill due soon.", evidence,
        ))
        recommendations.append(_recommendation(
            "review_bill_runway", "high", "Review paycheck timing before spending",
            "A read-only cash-flow review should happen before any discretionary allocation.",
            evidence, action_id="finance.simulate_cashflow",
        ))

    if (out_of_stock or low_stock) and not market_list:
        evidence = [
            _evidence("food", "out of stock", out_of_stock, source="pantry"),
            _evidence("food", "low stock", low_stock, source="pantry"),
            _evidence("food", "open market-list items", 0, source="grocery_list"),
        ]
        recommendations.append(_recommendation(
            "reconcile_market_list", "low", "Review pantry shortages for the market list",
            "Stock shortages exist but no corresponding open market-list item is visible.", evidence, horizon="later",
        ))

    urgent = any(item["priority"] in {"critical", "high"} for item in recommendations)
    if urgent and not bool(voice.get("ready")):
        evidence = [
            _evidence("voice", "voice output ready", False, source="x1_hardware"),
            _evidence("decision", "urgent recommendation count", sum(
                item["priority"] in {"critical", "high"} for item in recommendations
            ), source="intelligence"),
        ]
        signals.append(_signal(
            "urgent_voice_channel_unavailable", "voice", "medium",
            "Urgent guidance cannot use the primary voice channel",
            "Jarvis should keep urgent guidance visible until voice readiness recovers.", evidence,
        ))

    confirmed_preferences = [
        item for item in (learning or {}).get("preferences", [])
        if item.get("status") == "confirmed"
    ]
    if confirmed_preferences:
        signals.append(_signal(
            "confirmed_guidance_loaded", "learning", "info",
            "Confirmed personal guidance is loaded",
            f"{len(confirmed_preferences)} confirmed preference(s) may shape wording and ranking but cannot authorize actions.",
            [_evidence(
                "learning", "confirmed preferences", len(confirmed_preferences),
                source="learning_ledger",
            )],
        ))

    automatic_patterns = list((learning or {}).get("automatic_patterns", {}).get("patterns", []))
    established_patterns = [item for item in automatic_patterns if item.get("status") == "established"]
    if established_patterns:
        domains = sorted({str(item.get("domain") or "general").replace("_", " ") for item in established_patterns})
        signals.append(_signal(
            "established_patterns_loaded", "learning", "info",
            "Evidence-backed personal patterns are available",
            f"{len(established_patterns)} established pattern(s) across {', '.join(domains)} may shape timing and suggestions but cannot authorize actions.",
            [_evidence(
                "learning", "established automatic patterns", len(established_patterns),
                source="pattern_ledger",
            )],
        ))

    if proposals:
        signals.append(_signal(
            "pending_policy_proposals", "policy", "info",
            "Policy proposals await review",
            f"{len(proposals)} proposal(s) are pending in the existing action-policy queue.",
            [_evidence("policy", "pending proposals", len(proposals), source="action_proposals")],
        ))

    temporal = temporal_patterns(snapshot, now=now)
    if telemetry_state == "online":
        for deviation in temporal["deviations"][:2]:
            evidence = [_evidence(
                "routine",
                deviation["pattern_id"],
                deviation["minutes_from_usual"],
                source="context_history",
                observed_at=deviation["observed_at"],
                confidence=deviation["confidence"],
            )]
            signals.append(_signal(
                deviation["id"], "routine", "medium",
                "Current timing differs from the established routine",
                deviation["claim"], evidence,
            ))
            recommendations.append(_recommendation(
                f"review:{deviation['id']}", "medium",
                "Treat this routine deviation as context, not a conclusion",
                "Jarvis noticed an unusual transition time but will wait for direct evidence before changing any mode.",
                evidence, horizon="now",
            ))

    signals.sort(key=lambda item: (SEVERITY_RANK.get(item["severity"], 9), item["id"]))
    recommendations.sort(key=lambda item: (SEVERITY_RANK.get(item["priority"], 9), item["id"]))
    recommendations = recommendations[:6]
    top = recommendations[0] if recommendations else None
    degraded = telemetry_state != "online"
    status = "degraded" if degraded else (
        "attention" if top and top["priority"] in {"critical", "high"} else "nominal"
    )
    if top:
        headline = top["title"]
        brief = f"{top['title']}. {top['rationale']}"
    else:
        headline = "No cross-domain conflicts detected"
        brief = "Jarvis found no urgent conflict across home, body, food, finance, and policy state."
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "status": status,
        "headline": headline,
        "briefing_line": brief,
        "confidence": round(min(
            [telemetry_confidence] + [item["confidence"] for item in signals]
        ), 2) if signals else round(telemetry_confidence, 2),
        "summary": {
            "signals": len(signals),
            "conflicts": len(conflicts),
            "recommendations": len(recommendations),
            "urgent": sum(item["priority"] in {"critical", "high"} for item in recommendations),
        },
        "signals": signals,
        "conflicts": conflicts,
        "recommendations": recommendations,
        "temporal": temporal,
        "data_quality": {
            "telemetry": telemetry_state,
            "fresh": telemetry_state == "online",
            "last_event_at": observed_at,
            "confidence": round(telemetry_confidence, 2),
        },
        "policy": {
            "read_only": True,
            "deterministic": True,
            "inferences_authorize_actions": False,
            "stale_data_can_authorize_actions": False,
            "confirmed_preferences_are_guidance_only": True,
            "automatic_patterns_are_guidance_only": True,
            "gesture_can_confirm_actions": False,
        },
    }


SIMULATION_OVERRIDES = {
    "sanctuary_mode",
    "occupied",
    "people_home",
    "visual_presence",
    "visual_confidence",
    "telemetry_state",
    "open_perimeter",
    "active_hazards",
    "protein_g",
    "vitamins_taken",
    "pantry_item_count",
    "out_of_stock",
    "low_stock",
    "market_list",
    "runway_after_bills",
    "bills_due_soon",
    "voice_ready",
    "hour",
}


def simulate_intelligence(
    snapshot: dict[str, Any],
    overrides: dict[str, Any] | None = None,
    *,
    learning: dict[str, Any] | None = None,
    proposals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run a cross-domain counterfactual without mutating retained state."""
    scenario = deepcopy(overrides or {})
    unknown = sorted(set(scenario) - SIMULATION_OVERRIDES)
    if unknown:
        raise ValueError(f"unsupported intelligence override: {', '.join(unknown)}")
    baseline_snapshot = deepcopy(snapshot)
    candidate = deepcopy(snapshot)
    candidate.setdefault("sanctuary", {})
    candidate.setdefault("occupancy", {})
    candidate.setdefault("perception", {})
    candidate.setdefault("telemetry", {})
    candidate.setdefault("security", {})
    candidate.setdefault("voice", {})
    candidate.setdefault("lifeos", {}).setdefault("body", {})
    candidate["lifeos"].setdefault("vault", {})
    candidate["lifeos"].setdefault("food", {})

    if "sanctuary_mode" in scenario:
        candidate["sanctuary"]["mode"] = str(scenario["sanctuary_mode"])
    if "occupied" in scenario:
        candidate["occupancy"]["occupied"] = bool(scenario["occupied"])
    if "people_home" in scenario:
        candidate["occupancy"]["people_home"] = list(scenario["people_home"] or [])
    if "visual_presence" in scenario:
        candidate["perception"].update({
            "room_occupied": bool(scenario["visual_presence"]),
            "link_state": "online",
        })
    if "visual_confidence" in scenario:
        candidate["perception"]["confidence"] = max(
            0.0, min(1.0, _number(scenario["visual_confidence"], 0.5))
        )
    if "telemetry_state" in scenario:
        candidate["telemetry"]["link_state"] = str(scenario["telemetry_state"])
    if "open_perimeter" in scenario:
        candidate["security"]["open_perimeter"] = list(scenario["open_perimeter"] or [])
    if "active_hazards" in scenario:
        candidate["security"]["active_hazards"] = list(scenario["active_hazards"] or [])
    if "protein_g" in scenario:
        candidate["lifeos"]["body"]["protein_g"] = max(0.0, _number(scenario["protein_g"]))
    if "vitamins_taken" in scenario:
        candidate["lifeos"]["body"]["vitamins_taken"] = bool(scenario["vitamins_taken"])
    if "pantry_item_count" in scenario:
        candidate["lifeos"]["food"]["pantry_item_count"] = max(0, int(_number(scenario["pantry_item_count"])))
    for key in ("out_of_stock", "low_stock", "market_list"):
        if key in scenario:
            candidate["lifeos"]["food"][key] = list(scenario[key] or [])
    if "runway_after_bills" in scenario:
        candidate["lifeos"]["vault"]["left_after_due_bills"] = _number(scenario["runway_after_bills"])
    if "bills_due_soon" in scenario:
        candidate["lifeos"]["vault"]["bills_due_soon"] = list(scenario["bills_due_soon"] or [])
    if "voice_ready" in scenario:
        candidate["voice"]["ready"] = bool(scenario["voice_ready"])

    simulation_now = now or datetime.now().astimezone()
    if "hour" in scenario:
        hour = int(_number(scenario["hour"], simulation_now.hour))
        if not 0 <= hour <= 23:
            raise ValueError("hour must be between 0 and 23")
        simulation_now = simulation_now.replace(hour=hour, minute=0, second=0, microsecond=0)
    baseline = build_intelligence(
        baseline_snapshot,
        learning=learning,
        proposals=proposals,
        now=simulation_now,
    )
    assessment = build_intelligence(
        candidate,
        learning=learning,
        proposals=proposals,
        now=simulation_now,
    )
    baseline_ids = {item["id"] for item in baseline["recommendations"]}
    scenario_ids = {item["id"] for item in assessment["recommendations"]}
    return {
        "simulation": True,
        "behavior": "intelligence",
        "scenario": scenario,
        "baseline": {
            "status": baseline["status"],
            "headline": baseline["headline"],
            "recommendation_ids": sorted(baseline_ids),
        },
        "assessment": assessment,
        "changes": {
            "added_recommendations": sorted(scenario_ids - baseline_ids),
            "removed_recommendations": sorted(baseline_ids - scenario_ids),
            "status_changed": baseline["status"] != assessment["status"],
        },
        "predicted_actions": [],
        "house_state_mutated": False,
        "database_mutated": False,
        "action_execution": False,
    }
