"""Durable house context, behavior evaluation, and action safety policy."""
import json
import os
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .db import active_profile, conn, get_setting


ACTION_REGISTRY: dict[str, dict[str, Any]] = {
    "scene.arrival": {
        "name": "Prepare the house for arrival",
        "description": "Activate the configured arrival scene.",
        "risk": "low",
        "scope": "comfort",
        "reversible": True,
        "confirmation_policy": "automatic_or_simulated",
        "requires_confirmation": False,
        "remote_execution": True,
        "service": "scene.turn_on",
        "target": "scene.arrival",
    },
    "notify.departure_anomaly": {
        "name": "Report a departure anomaly",
        "description": "Notify the owner about open entries or active devices.",
        "risk": "low",
        "scope": "notification",
        "reversible": True,
        "confirmation_policy": "automatic_or_simulated",
        "requires_confirmation": False,
        "remote_execution": True,
        "service": "notify.mobile_app_phone",
        "target": None,
    },
    "security.arm_away": {
        "name": "Arm the perimeter in away mode",
        "description": "Arm the configured Home Assistant alarm panel.",
        "risk": "high",
        "scope": "security",
        "reversible": True,
        "confirmation_policy": "explicit_confirmation",
        "requires_confirmation": True,
        "remote_execution": True,
        "service": "alarm_control_panel.alarm_arm_away",
        "target": "alarm_control_panel.home",
    },
    "security.arm_night": {
        "name": "Arm the perimeter in night mode",
        "description": "Arm perimeter sensors while preserving interior movement.",
        "risk": "high",
        "scope": "security",
        "reversible": True,
        "confirmation_policy": "explicit_confirmation",
        "requires_confirmation": True,
        "remote_execution": True,
        "service": "alarm_control_panel.alarm_arm_night",
        "target": "alarm_control_panel.home",
    },
    "security.lock_perimeter": {
        "name": "Lock perimeter doors",
        "description": "Lock the configured exterior locks.",
        "risk": "high",
        "scope": "security",
        "reversible": True,
        "confirmation_policy": "explicit_confirmation",
        "requires_confirmation": True,
        "remote_execution": True,
        "service": "lock.lock",
        "target": "lock.perimeter",
    },
    "security.close_garage": {
        "name": "Close the garage",
        "description": "Close a motorized garage door after local inspection.",
        "risk": "critical",
        "scope": "physical_safety",
        "reversible": True,
        "confirmation_policy": "local_visual_confirmation",
        "requires_confirmation": True,
        "remote_execution": False,
        "service": "cover.close_cover",
        "target": "cover.garage_door",
    },
    "security.nightly_report": {
        "name": "Deliver the nightly security review",
        "description": "Send a read-only perimeter and alarm summary.",
        "risk": "low",
        "scope": "notification",
        "reversible": True,
        "confirmation_policy": "automatic_or_simulated",
        "requires_confirmation": False,
        "remote_execution": True,
        "service": "notify.mobile_app_phone",
        "target": None,
    },
}

OPEN_STATES = {"on", "open", "opening", "unlocked", "unsafe"}
PERIMETER_MARKERS = ("door", "window", "garage", "gate", "lock")
HAZARD_MARKERS = ("stove", "oven", "cooktop", "iron", "space_heater")
HA_URL = os.environ.get("HOME_ASSISTANT_URL", "http://homeassistant:8123").rstrip("/")
HA_TOKEN = os.environ.get("HOME_ASSISTANT_TOKEN", "")


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _decode(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def set_fact(key: str, value: Any, source: str, confidence: float = 1.0) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO context_facts(key,value_json,confidence,source) VALUES(?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,"
            " confidence=excluded.confidence, source=excluded.source,"
            " updated_at=datetime('now','localtime')",
            (key, _json(value), max(0.0, min(1.0, confidence)), source),
        )


def facts() -> dict[str, dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT * FROM context_facts ORDER BY key").fetchall()
    return {
        row["key"]: {
            "value": _decode(row["value_json"]),
            "confidence": row["confidence"],
            "source": row["source"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


def _entity_facts(prefix: str) -> dict[str, Any]:
    return {
        key.removeprefix(prefix): item["value"]
        for key, item in facts().items()
        if key.startswith(prefix)
    }


def lifeos_snapshot() -> dict[str, Any]:
    """Return the personal operating picture Jarvis uses alongside house state."""
    today = date.today()
    today_iso = today.isoformat()
    month = today.strftime("%Y-%m")
    with conn() as c:
        profile = dict(active_profile(c))
        pid = profile["id"]
        protein = c.execute(
            "SELECT COALESCE(SUM(protein_g),0) value FROM meal_log"
            " WHERE date(ts)=? AND profile_id=?",
            (today_iso, pid),
        ).fetchone()["value"]
        steps_row = c.execute(
            "SELECT count FROM steps WHERE date=? AND profile_id=?", (today_iso, pid)
        ).fetchone()
        water_row = c.execute(
            "SELECT glasses FROM water WHERE date=? AND profile_id=?", (today_iso, pid)
        ).fetchone()
        vitamins_row = c.execute(
            "SELECT taken FROM vitamins WHERE date=? AND profile_id=?", (today_iso, pid)
        ).fetchone()
        workouts = [
            dict(row)
            for row in c.execute(
                "SELECT id,kind,minutes,done,source FROM workout_plan"
                " WHERE date=? AND profile_id=? ORDER BY done,id",
                (today_iso, pid),
            ).fetchall()
        ]
        accounts_total = c.execute(
            "SELECT COALESCE(SUM(balance),0) value FROM accounts"
        ).fetchone()["value"]
        bills = [
            dict(row)
            for row in c.execute(
                "SELECT id,name,amount,due_day FROM bills"
                " WHERE paid_month IS NULL OR paid_month<>? ORDER BY due_day",
                (month,),
            ).fetchall()
        ]
        nudges = [
            dict(row)
            for row in c.execute(
                "SELECT id,kind,text FROM nudges WHERE resolved=0 ORDER BY id DESC LIMIT 5"
            ).fetchall()
        ]

    steps = steps_row["count"] if steps_row else 0
    water = water_row["glasses"] if water_row else 0
    vitamins = bool(vitamins_row and vitamins_row["taken"])
    water_target = int(get_setting("water_target_glasses") or 8)
    due_soon = [
        bill
        for bill in bills
        if bill["due_day"] < today.day or bill["due_day"] <= today.day + 7
    ]
    bills_total = sum(bill["amount"] for bill in due_soon)
    priorities: list[dict[str, str]] = []
    if not vitamins:
        priorities.append({"domain": "body", "label": "Take daily vitamins"})
    if protein < profile["protein_target_g"]:
        priorities.append(
            {
                "domain": "body",
                "label": f"{round(profile['protein_target_g'] - protein)} g protein remaining",
            }
        )
    if water < water_target:
        priorities.append(
            {"domain": "body", "label": f"{water_target - water} glasses of water remaining"}
        )
    if due_soon:
        priorities.append(
            {"domain": "vault", "label": f"{len(due_soon)} bill(s) need attention"}
        )
    if any(not workout["done"] for workout in workouts):
        priorities.append({"domain": "body", "label": "Planned workout remains open"})
    priorities.extend(
        {"domain": nudge["kind"], "label": nudge["text"]} for nudge in nudges[:2]
    )

    progress = [
        min(1.0, protein / max(1, profile["protein_target_g"])),
        min(1.0, steps / max(1, profile["step_target"])),
        min(1.0, water / max(1, water_target)),
        1.0 if vitamins else 0.0,
    ]
    return {
        "date": today_iso,
        "profile": profile["name"],
        "daily_score": round(sum(progress) / len(progress) * 100),
        "body": {
            "protein_g": protein,
            "protein_target_g": profile["protein_target_g"],
            "steps": steps,
            "step_target": profile["step_target"],
            "water": water,
            "water_target": water_target,
            "vitamins_taken": vitamins,
            "workouts": workouts,
        },
        "vault": {
            "accounts_total": accounts_total,
            "bills_due_soon": due_soon,
            "bills_due_total": bills_total,
            "left_after_due_bills": accounts_total - bills_total,
        },
        "priorities": priorities[:6],
    }


def current_context(event_limit: int = 20) -> dict[str, Any]:
    all_facts = facts()
    people = _entity_facts("person.")
    perimeter = _entity_facts("perimeter.")
    devices = _entity_facts("device.")
    open_perimeter = sorted(
        entity for entity, state in perimeter.items() if str(state).lower() in OPEN_STATES
    )
    people_home = sorted(
        entity for entity, state in people.items() if str(state).lower() in {"home", "on"}
    )
    active_hazards = sorted(
        entity
        for entity, state in devices.items()
        if any(marker in entity.lower() for marker in HAZARD_MARKERS)
        and str(state).lower() in {"on", "heating"}
    )
    with conn() as c:
        recent = [
            dict(row)
            for row in c.execute(
                "SELECT id,ts,source,event_type,entity_id,state,previous_state,"
                " attributes_json FROM context_events ORDER BY id DESC LIMIT ?",
                (max(1, min(event_limit, 100)),),
            ).fetchall()
        ]
        pending = c.execute(
            "SELECT COUNT(*) n FROM action_proposals WHERE status='pending'"
        ).fetchone()["n"]
    for event in recent:
        event["attributes"] = _decode(event.pop("attributes_json"))
    mode = all_facts.get("house.mode", {}).get("value", "normal")
    alarm = all_facts.get("security.alarm", {}).get("value", "unknown")
    hardware = {
        "battery": devices.get("sensor.x1_battery", "unknown"),
        "temperature": devices.get("sensor.x1_cpu_temperature", "unknown"),
        "mains": devices.get("binary_sensor.x1_mains_power", "unknown"),
        "microphone": devices.get("binary_sensor.x1_microphone", "unknown"),
        "speakers": devices.get("binary_sensor.x1_speakers", "unknown"),
        "camera": devices.get("binary_sensor.x1_camera", "unknown"),
        "bluetooth": devices.get("binary_sensor.x1_bluetooth", "unknown"),
        "touchscreen": devices.get("binary_sensor.x1_touchscreen", "unknown"),
        "monitor": devices.get("binary_sensor.x1_hardware_monitor", "unknown"),
    }
    latest_event = recent[0]["ts"] if recent else None
    link_state = "awaiting_data"
    if latest_event:
        try:
            age = datetime.now() - datetime.strptime(latest_event, "%Y-%m-%d %H:%M:%S")
            link_state = "online" if age <= timedelta(minutes=10) else "stale"
        except ValueError:
            link_state = "degraded"
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "house_mode": mode,
        "occupancy": {
            "occupied": bool(people_home),
            "people_home": people_home,
            "known_people": people,
        },
        "security": {
            "alarm": alarm,
            "open_perimeter": open_perimeter,
            "active_hazards": active_hazards,
            "secure": not open_perimeter
            and not active_hazards
            and alarm not in {"unknown", "triggered", "unavailable"},
        },
        "devices": devices,
        "hardware": hardware,
        "telemetry": {
            "last_event_at": latest_event,
            "event_count": len(recent),
            "link_state": link_state,
        },
        "lifeos": lifeos_snapshot(),
        "facts": all_facts,
        "pending_proposals": pending,
        "recent_events": recent,
    }


def capability_manifest(snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Expose what Jarvis can do now and what dependency unlocks the rest."""
    snapshot = snapshot or current_context(event_limit=1)
    hardware = snapshot["hardware"]
    capabilities = [
        ("context", "Context engine", True, "Durable events, facts, and explainable state"),
        ("voice", "Voice I/O", hardware["microphone"] == "on" and hardware["speakers"] == "on", "Microphone and speakers"),
        ("vision", "Local vision", hardware["camera"] == "on", "Linux V4L2 camera device"),
        ("proximity", "Bluetooth proximity", hardware["bluetooth"] == "on", "Powered Bluetooth adapter"),
        ("touch", "Touch command surface", hardware["touchscreen"] == "on", "Touchscreen input"),
        ("automation", "Home control", bool(HA_TOKEN), "HOME_ASSISTANT_TOKEN"),
        ("lifeos", "LifeOS intelligence", True, "Body Ops and Vault Flow data"),
    ]
    return [
        {"id": key, "name": name, "ready": ready, "dependency": dependency}
        for key, name, ready, dependency in capabilities
    ]


def command_center_payload(event_limit: int = 40) -> dict[str, Any]:
    snapshot = current_context(event_limit=event_limit)
    return {
        "context": snapshot,
        "proposals": list_proposals("pending"),
        "actions": ACTION_REGISTRY,
        "capabilities": capability_manifest(snapshot),
    }


def simulate_behavior(behavior: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Predict a behavior without writing facts, proposals, audits, or HA state."""
    if behavior not in {"arrival", "departure", "nightly"}:
        raise ValueError(behavior)
    scenario = deepcopy(overrides or {})
    actions: list[dict[str, Any]] = []
    if behavior == "arrival":
        person = scenario.setdefault("person", "person.simulated_resident")
        actions.append({"action_id": "scene.arrival", "reason": f"{person} arrived home"})
    elif behavior == "departure":
        open_entries = scenario.setdefault(
            "open_perimeter", ["binary_sensor.simulated_front_door"]
        )
        hazards = scenario.setdefault("active_hazards", [])
        if open_entries or hazards:
            actions.append(
                {
                    "action_id": "notify.departure_anomaly",
                    "reason": "Open perimeter or active hazard detected",
                }
            )
        actions.append({"action_id": "security.arm_away", "reason": "Last person left"})
    else:
        open_entries = scenario.setdefault("open_perimeter", [])
        alarm = scenario.setdefault("alarm", "disarmed")
        actions.append(
            {
                "action_id": "security.nightly_report",
                "reason": "Nightly perimeter review",
            }
        )
        if alarm not in {"armed_night", "armed_home"}:
            actions.append({"action_id": "security.arm_night", "reason": "Alarm is not armed"})
        scenario["secure"] = not open_entries and alarm in {"armed_night", "armed_home"}
    for item in actions:
        item["policy"] = ACTION_REGISTRY[item["action_id"]]
    return {"simulation": True, "behavior": behavior, "scenario": scenario, "predicted_actions": actions}


def _fact_key(entity_id: str) -> str:
    domain, _, name = entity_id.partition(".")
    lowered = entity_id.lower()
    if domain == "person":
        return f"person.{name}"
    if domain in {"binary_sensor", "cover", "lock"} and any(
        marker in lowered for marker in PERIMETER_MARKERS
    ):
        return f"perimeter.{entity_id}"
    if domain == "alarm_control_panel":
        return "security.alarm"
    if domain == "input_boolean" and name.endswith("_mode"):
        return f"mode.{name}"
    return f"device.{entity_id}"


def ingest_event(event: dict[str, Any], evaluate: bool = True) -> dict[str, Any]:
    source = str(event.get("source") or "unknown")
    event_type = str(event.get("event_type") or "state_changed")
    entity_id = event.get("entity_id")
    state = event.get("state")
    previous = event.get("previous_state")
    attributes = event.get("attributes") or {}
    with conn() as c:
        cursor = c.execute(
            "INSERT INTO context_events(source,event_type,entity_id,state,previous_state,"
            " attributes_json,correlation_id) VALUES(?,?,?,?,?,?,?)",
            (
                source,
                event_type,
                entity_id,
                None if state is None else str(state),
                None if previous is None else str(previous),
                _json(attributes),
                event.get("correlation_id"),
            ),
        )
        event_id = cursor.lastrowid
    if entity_id and state is not None:
        set_fact(_fact_key(str(entity_id)), str(state), source, float(event.get("confidence", 1)))
    _derive_house_mode()
    created = evaluate_behaviors(trigger_event=event) if evaluate else []
    return {"id": event_id, "proposals_created": created}


def _derive_house_mode() -> None:
    all_facts = facts()
    modes = {
        key.removeprefix("mode."): str(value["value"]).lower()
        for key, value in all_facts.items()
        if key.startswith("mode.")
    }
    people = _entity_facts("person.")
    if modes.get("vacation_mode") == "on":
        mode = "vacation"
    elif modes.get("sleep_mode") == "on":
        mode = "night"
    elif modes.get("guest_mode") == "on":
        mode = "guest"
    elif people and not any(str(value).lower() == "home" for value in people.values()):
        mode = "away"
    else:
        mode = "normal"
    set_fact("house.mode", mode, "context_engine")


def _proposal_exists(behavior: str, action_id: str, hours: int = 6) -> bool:
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with conn() as c:
        return bool(
            c.execute(
                "SELECT 1 FROM action_proposals WHERE behavior=? AND action_id=?"
                " AND created_at>=? AND status IN ('pending','executed') LIMIT 1",
                (behavior, action_id, cutoff),
            ).fetchone()
        )


def create_proposal(
    behavior: str, action_id: str, summary: str, reason: str, context: dict[str, Any]
) -> int | None:
    action = ACTION_REGISTRY[action_id]
    if _proposal_exists(behavior, action_id):
        return None
    with conn() as c:
        cursor = c.execute(
            "INSERT INTO action_proposals(behavior,action_id,summary,reason,risk,"
            " requires_confirmation,context_json,expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                behavior,
                action_id,
                summary,
                reason,
                action["risk"],
                int(action["requires_confirmation"]),
                _json(context),
                (datetime.now() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        return int(cursor.lastrowid)


def evaluate_behaviors(
    behavior: str | None = None, trigger_event: dict[str, Any] | None = None
) -> list[int]:
    snapshot = current_context(event_limit=5)
    created: list[int] = []
    requested = {behavior} if behavior else {"arrival", "departure", "nightly"}
    event = trigger_event or {}
    entity = str(event.get("entity_id") or "")
    old = str(event.get("previous_state") or "").lower()
    new = str(event.get("state") or "").lower()

    if "arrival" in requested and entity.startswith("person.") and old != "home" and new == "home":
        proposal = create_proposal(
            "arrival_orchestration",
            "scene.arrival",
            f"Prepare the house for {entity.split('.', 1)[1].replace('_', ' ').title()}",
            "A known person transitioned from away to home.",
            {"person": entity, "previous_state": old},
        )
        if proposal:
            created.append(proposal)

    last_departure = (
        entity.startswith("person.")
        and old == "home"
        and new != "home"
        and not snapshot["occupancy"]["occupied"]
    )
    if "departure" in requested and last_departure:
        open_perimeter = snapshot["security"]["open_perimeter"]
        active_hazards = snapshot["security"]["active_hazards"]
        if open_perimeter or active_hazards:
            proposal = create_proposal(
                "departure_anomaly_detection",
                "notify.departure_anomaly",
                "Departure anomaly detected",
                "Everyone left while an entry is open or a risky device is active.",
                {
                    "open_perimeter": open_perimeter,
                    "active_hazards": active_hazards,
                },
            )
            if proposal:
                created.append(proposal)
        proposal = create_proposal(
            "departure_anomaly_detection",
            "security.arm_away",
            "Arm the house in away mode",
            "The last known person left the house.",
            {
                "open_perimeter": open_perimeter,
                "active_hazards": active_hazards,
            },
        )
        if proposal:
            created.append(proposal)

    nightly_trigger = event.get("event_type") == "schedule.nightly_security_review"
    if "nightly" in requested and (nightly_trigger or behavior == "nightly"):
        problems = list(snapshot["security"]["open_perimeter"])
        alarm = snapshot["security"]["alarm"]
        if alarm not in {"armed_night", "armed_home"}:
            problems.append(f"alarm:{alarm}")
        proposal = create_proposal(
            "nightly_security_review",
            "security.nightly_report",
            "Nightly security review ready",
            "Perimeter and alarm state were evaluated for bedtime.",
            {"findings": problems, "secure": not problems},
        )
        if proposal:
            created.append(proposal)
        if alarm not in {"armed_night", "armed_home"}:
            proposal = create_proposal(
                "nightly_security_review",
                "security.arm_night",
                "Arm the perimeter for the night",
                "Night mode is not currently armed.",
                {"findings": problems},
            )
            if proposal:
                created.append(proposal)
    return created


def list_proposals(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with conn() as c:
        c.execute(
            "UPDATE action_proposals SET status='expired' WHERE status='pending'"
            " AND expires_at IS NOT NULL AND expires_at<datetime('now','localtime')"
        )
    query = "SELECT * FROM action_proposals"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 200)))
    with conn() as c:
        rows = [dict(row) for row in c.execute(query, params).fetchall()]
    for row in rows:
        row["requires_confirmation"] = bool(row["requires_confirmation"])
        row["context"] = _decode(row.pop("context_json"))
    return rows


def dismiss_proposal(proposal_id: int, requested_by: str) -> bool:
    with conn() as c:
        proposal = c.execute(
            "SELECT action_id,status FROM action_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if not proposal or proposal["status"] != "pending":
            return False
        c.execute(
            "UPDATE action_proposals SET status='dismissed' WHERE id=?", (proposal_id,)
        )
        c.execute(
            "INSERT INTO action_audit(action_id,proposal_id,requested_by,outcome,details_json)"
            " VALUES(?,?,?,?,?)",
            (proposal["action_id"], proposal_id, requested_by, "dismissed", "{}"),
        )
    return True


def action_audit(limit: int = 50) -> list[dict[str, Any]]:
    with conn() as c:
        rows = [
            dict(row)
            for row in c.execute(
                "SELECT id,ts,action_id,proposal_id,requested_by,outcome,details_json"
                " FROM action_audit ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        ]
    for row in rows:
        row["details"] = _decode(row.pop("details_json"))
    return rows


def execute_action(
    action_id: str,
    proposal_id: int | None,
    confirmed: bool,
    dry_run: bool,
    requested_by: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action = ACTION_REGISTRY.get(action_id)
    if action is None:
        raise KeyError(action_id)
    if action["requires_confirmation"] and not confirmed:
        raise PermissionError("confirmation_required")
    if not action["remote_execution"] and not dry_run:
        raise PermissionError("local_confirmation_required")
    payload = dict(data or {})
    if action["target"]:
        payload.setdefault("entity_id", action["target"])
    outcome = "simulated"
    if not dry_run:
        if not HA_TOKEN:
            raise RuntimeError("HOME_ASSISTANT_TOKEN is not configured")
        domain, service = action["service"].split(".", 1)
        response = httpx.post(
            f"{HA_URL}/api/services/{domain}/{service}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        outcome = "executed"
    with conn() as c:
        c.execute(
            "INSERT INTO action_audit(action_id,proposal_id,requested_by,outcome,"
            " details_json) VALUES(?,?,?,?,?)",
            (action_id, proposal_id, requested_by, outcome, _json(payload)),
        )
        if proposal_id:
            c.execute(
                "UPDATE action_proposals SET status=?,executed_at=datetime('now','localtime')"
                " WHERE id=? AND action_id=?",
                (outcome, proposal_id, action_id),
            )
    return {"ok": True, "action_id": action_id, "outcome": outcome, "payload": payload}
