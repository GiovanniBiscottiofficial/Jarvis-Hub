"""Durable house context, behavior evaluation, and action safety policy."""

import json
import os
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .db import active_profile, conn, get_setting
from .paydays import scheduled_bill_due_date


ACTION_REGISTRY: dict[str, dict[str, Any]] = {
    "scene.arrival": {
        "name": "Prepare the house for arrival",
        "description": "Activate the approved Sanctuary Welcome mode.",
        "risk": "low",
        "scope": "comfort",
        "reversible": True,
        "confirmation_policy": "automatic_or_simulated",
        "requires_confirmation": False,
        "remote_execution": True,
        "service": "script.sanctuary_activate_mode",
        "target": None,
        "default_data": {
            "mode": "Welcome",
            "source": "lifeos",
            "reason": "Approved arrival orchestration proposal",
        },
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
        "service": "notify.jarvis_phone",
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
        "service": "notify.jarvis_phone",
        "target": None,
    },
    "sanctuary.activate_lighting_mode": {
        "name": "Activate a Sanctuary lighting mode",
        "description": "Apply a reversible, lighting-only Sanctuary mode through Home Assistant.",
        "risk": "low",
        "scope": "comfort",
        "reversible": True,
        "confirmation_policy": "automatic_or_simulated",
        "requires_confirmation": False,
        "remote_execution": True,
        "service": "script.sanctuary_activate_mode",
        "target": None,
        "default_data": {"source": "lifeos"},
    },
    "sanctuary.activate_away": {
        "name": "Activate Sanctuary Away mode",
        "description": "Turn off commissioned lights after confirmed departure.",
        "risk": "medium",
        "scope": "energy_and_presence",
        "reversible": True,
        "confirmation_policy": "explicit_confirmation",
        "requires_confirmation": True,
        "remote_execution": True,
        "service": "script.sanctuary_activate_mode",
        "target": None,
        "default_data": {
            "mode": "Away",
            "source": "lifeos",
            "reason": "Confirmed departure proposal",
        },
    },
    "sanctuary.manual_hold": {
        "name": "Protect manual lighting changes",
        "description": "Pause scheduled lifestyle transitions until manually resumed.",
        "risk": "low",
        "scope": "automation_control",
        "reversible": True,
        "confirmation_policy": "automatic_or_simulated",
        "requires_confirmation": False,
        "remote_execution": True,
        "service": "script.sanctuary_hold",
        "target": None,
        "default_data": {"reason": "Manual Hold requested from LifeOS"},
    },
    "sanctuary.resume": {
        "name": "Resume Sanctuary transitions",
        "description": "Release Manual Hold and restore the previously active mode.",
        "risk": "low",
        "scope": "automation_control",
        "reversible": True,
        "confirmation_policy": "automatic_or_simulated",
        "requires_confirmation": False,
        "remote_execution": True,
        "service": "script.sanctuary_resume",
        "target": None,
        "default_data": {},
    },
    "sanctuary.emergency_lighting": {
        "name": "Activate emergency path lighting",
        "description": "Override decorative modes with bright light-only egress guidance.",
        "risk": "medium",
        "scope": "physical_safety",
        "reversible": True,
        "confirmation_policy": "explicit_confirmation_or_sensor_trigger",
        "requires_confirmation": True,
        "remote_execution": True,
        "service": "script.sanctuary_activate_mode",
        "target": None,
        "default_data": {
            "mode": "Emergency",
            "source": "lifeos",
            "reason": "Confirmed emergency lighting request",
        },
    },
    "sanctuary.thunderstorm_media": {
        "name": "Start bedroom thunderstorm media",
        "description": (
            "Launch the approved black-screen YouTube storm on the commissioned "
            "Fire TV while protecting existing playback by default."
        ),
        "risk": "low",
        "scope": "comfort_media",
        "reversible": True,
        "confirmation_policy": "automatic_when_idle_or_explicit_voice_request",
        "requires_confirmation": False,
        "remote_execution": True,
        "service": "script.sanctuary_start_thunderstorm_media",
        "target": None,
        "default_data": {"force": False, "source": "lifeos"},
    },
}

SANCTUARY_MODES = {
    "Home Base",
    "Morning",
    "Away",
    "Welcome",
    "Work",
    "Focus",
    "Create",
    "Studio",
    "Shower",
    "Wind Down",
    "Thunderstorm",
    "Date Night",
    "Cleaning",
    "Guest",
    "Cinema",
    "Vacation",
    "Emergency",
    "Manual Hold",
}
SANCTUARY_ROOM_ALIASES = {
    "entry": "Entry",
    "living_room": "Living Room",
    "dinning_room": "Dining Area",
    "dining_room": "Dining Area",
    "dining_area": "Dining Area",
    "kitchen": "Kitchen",
    "hallway": "Hallway",
    "bathroom": "Bathroom",
    "bedroom": "Bedroom",
    "office": "Office",
    "patio": "Patio",
}

OPEN_STATES = {"on", "open", "opening", "unlocked", "unsafe"}
PERIMETER_MARKERS = ("door", "window", "garage", "gate", "lock")
HAZARD_MARKERS = ("stove", "oven", "cooktop", "iron", "space_heater")
HA_URL = os.environ.get("HOME_ASSISTANT_URL", "http://homeassistant:8123").rstrip("/")
HA_TOKEN = os.environ.get("HOME_ASSISTANT_TOKEN", "")


def _vision_retention_hours() -> int:
    try:
        return max(1, int(os.environ.get("LIFEOS_VISION_EVENT_RETENTION_HOURS", "24")))
    except ValueError:
        return 24


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
                "SELECT id,name,amount,due_day,paycheck,start_period FROM bills"
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
        pantry_items = [
            dict(row) for row in c.execute(
                "SELECT name,qty,unit,category,low_stock_threshold,last_depleted_at"
                " FROM pantry ORDER BY name"
            ).fetchall()
        ]
        market_list = [
            dict(row) for row in c.execute(
                "SELECT item,qty,unit,department,reason FROM grocery_list"
                " WHERE done=0 ORDER BY department,item LIMIT 50"
            ).fetchall()
        ]

    steps = steps_row["count"] if steps_row else 0
    water = water_row["glasses"] if water_row else 0
    vitamins = bool(vitamins_row and vitamins_row["taken"])
    water_target = int(get_setting("water_target_glasses") or 8)
    due_soon = [bill for bill in bills if 0 <= (
        scheduled_bill_due_date(
            bill["paycheck"] or 1, bill["due_day"], today, bill["start_period"]
        ) - today
    ).days <= 7]
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
        priorities.append({"domain": "vault", "label": f"{len(due_soon)} bill(s) need attention"})
    if any(not workout["done"] for workout in workouts):
        priorities.append({"domain": "body", "label": "Planned workout remains open"})
    depleted = [item for item in pantry_items if float(item["qty"]) <= 0]
    low_stock = [
        item for item in pantry_items
        if 0 < float(item["qty"]) <= float(item["low_stock_threshold"] or 1)
    ]
    if depleted:
        priorities.append({"domain": "food", "label": f"{len(depleted)} pantry item(s) are out"})
    priorities.extend({"domain": nudge["kind"], "label": nudge["text"]} for nudge in nudges[:2])

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
        "food": {
            "pantry_item_count": len(pantry_items),
            "out_of_stock": [item["name"] for item in depleted],
            "low_stock": [item["name"] for item in low_stock],
            "market_list": market_list,
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
    sanctuary_mode = all_facts.get("sanctuary.mode", {}).get("value", "Home Base")
    sanctuary_reason = all_facts.get("sanctuary.last_transition", {}).get("value", {})
    room_entities: dict[str, list[dict[str, Any]]] = {
        name: [] for name in SANCTUARY_ROOM_ALIASES.values()
    }
    unassigned_lights: list[dict[str, Any]] = []
    for key, fact in all_facts.items():
        if not key.startswith("sanctuary.room_entity."):
            continue
        value = fact.get("value") if isinstance(fact.get("value"), dict) else {}
        area_id = str(value.get("area_id") or "")
        room_name = SANCTUARY_ROOM_ALIASES.get(area_id)
        item = {
            "entity_id": key.removeprefix("sanctuary.room_entity."),
            "name": value.get("friendly_name"),
            "state": value.get("state", "unknown"),
            "updated_at": fact.get("updated_at"),
        }
        if room_name:
            room_entities[room_name].append(item)
        else:
            unassigned_lights.append(item)
    rooms = []
    for room_name, entities in room_entities.items():
        rooms.append(
            {
                "name": room_name,
                "lights": entities,
                "active_lights": sum(item["state"] == "on" for item in entities),
                "unavailable_lights": sum(
                    item["state"] in {"unavailable", "unknown"} for item in entities
                ),
                "commissioned": bool(entities),
            }
        )
    sanctuary_devices = list(devices)
    missing_capabilities = []
    for domain, label in (
        ("vacuum.", "vacuum"),
        ("media_player.", "media"),
        ("lock.", "locks"),
        ("climate.", "climate"),
        ("camera.", "cameras"),
    ):
        if not any(entity.startswith(domain) for entity in sanctuary_devices):
            missing_capabilities.append(label)
    bedroom = next(room for room in rooms if room["name"] == "Bedroom")
    if len(bedroom["lights"]) < 2:
        missing_capabilities.append("bedroom_light_2")
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
        "external_storage": devices.get("binary_sensor.x1_external_storage", "unknown"),
        "monitor": devices.get("binary_sensor.x1_hardware_monitor", "unknown"),
    }
    microphone_fact = all_facts.get("hardware.microphone_details", {})
    microphone_details = microphone_fact.get("value", {})
    if not isinstance(microphone_details, dict):
        microphone_details = {}
    speaker_fact = all_facts.get("hardware.speaker_details", {})
    speaker_details = speaker_fact.get("value", {})
    if not isinstance(speaker_details, dict):
        speaker_details = {}
    satellite_state = devices.get("assist_satellite.x1", "unknown")
    if satellite_state in {"unknown", "unavailable"}:
        satellite_state = next(
            (
                state
                for entity, state in devices.items()
                if entity.startswith("assist_satellite.")
                and state not in {"unknown", "unavailable"}
            ),
            satellite_state,
        )
    ha_mute_state = devices.get("switch.x1_mute", "unknown")
    microphone_muted = bool(microphone_details.get("muted")) or ha_mute_state == "on"
    voice_ready = (
        hardware["microphone"] == "on"
        and hardware["speakers"] == "on"
        and not microphone_muted
        and microphone_details.get("ready", True) is True
        and microphone_details.get("satellite", "online") == "online"
        and (
            microphone_details.get("continuous_conversation") is True
            or satellite_state != "unavailable"
        )
    )
    voice = {
        "ready": voice_ready,
        "state": "muted" if microphone_muted else ("ready" if voice_ready else "degraded"),
        "reason": microphone_details.get("reason")
        or ("Microphone privacy mute is on" if microphone_muted else "Voice telemetry awaiting detail"),
        "endpoint": microphone_details.get("endpoint", "unknown"),
        "device": microphone_details.get("device", "unknown"),
        "microphone_muted": microphone_muted,
        "microphone_volume_percent": microphone_details.get("volume_percent"),
        "speaker_muted": speaker_details.get("muted"),
        "speaker_volume_percent": speaker_details.get("volume_percent"),
        "pipewire": microphone_details.get("pipewire", "unknown"),
        "satellite": microphone_details.get("satellite", satellite_state),
        "assistant_state": satellite_state,
        "runtime": microphone_details.get("voice_runtime", "unavailable"),
        "continuous_conversation": bool(
            microphone_details.get("continuous_conversation")
        ),
        "interrupt_word": microphone_details.get("interrupt_word"),
        "wake_word": microphone_details.get("wake_word", "hey_jarvis"),
        "signal": microphone_details.get("signal", {}),
        "last_checked_at": microphone_fact.get("updated_at"),
        "privacy": microphone_details.get(
            "privacy", {"raw_audio_stored": False, "probe_retained": False}
        ),
    }
    supervisor_facts = {
        key.removeprefix("supervisor.component."): fact
        for key, fact in all_facts.items()
        if key.startswith("supervisor.component.")
    }
    supervisor_components = []
    for component, fact in sorted(supervisor_facts.items()):
        value = fact.get("value", {})
        if not isinstance(value, dict):
            value = {}
        supervisor_components.append(
            {
                "id": component,
                "label": value.get("label", component.replace("_", " ").title()),
                "state": value.get("state", "unknown"),
                "decision": value.get("decision", "unknown"),
                "detail": value.get("detail"),
                "automatic_repair": bool(value.get("automatic_repair")),
                "failure_count": int(value.get("failure_count") or 0),
                "updated_at": fact.get("updated_at"),
            }
        )
    supervisor_heartbeat = all_facts.get("supervisor.heartbeat", {})
    supervisor_heartbeat_value = supervisor_heartbeat.get("value", {})
    if not isinstance(supervisor_heartbeat_value, dict):
        supervisor_heartbeat_value = {}
    supervisor_heartbeat_at = supervisor_heartbeat.get("updated_at")
    supervisor_link = "awaiting"
    if supervisor_heartbeat_at:
        try:
            heartbeat_age = datetime.now() - datetime.strptime(
                supervisor_heartbeat_at, "%Y-%m-%d %H:%M:%S"
            )
            supervisor_link = "online" if heartbeat_age <= timedelta(minutes=12) else "stale"
        except ValueError:
            supervisor_link = "degraded"
    supervisor = {
        "state": supervisor_link,
        "last_heartbeat_at": supervisor_heartbeat_at,
        "repairs_enabled": supervisor_heartbeat_value.get("automatic_repairs_enabled"),
        "healthy_count": sum(item["state"] == "healthy" for item in supervisor_components),
        "attention_count": sum(item["state"] in {"failed", "quarantined"} for item in supervisor_components),
        "components": supervisor_components,
        "protected_boundaries": [
            "network",
            "internet_power",
            "locks",
            "alarms",
            "configuration",
            "user_data",
        ],
        "policy": "Allow-listed reversible service restarts only",
    }
    visual_presence = devices.get("binary_sensor.x1_visual_presence", "unknown")
    visual_fact = all_facts.get("device.binary_sensor.x1_visual_presence", {})
    observation = all_facts.get("perception.last_observation", {}).get("value", {})
    if not isinstance(observation, dict):
        observation = {}
    gesture = all_facts.get("perception.last_gesture", {})
    perception_link = "awaiting_signal"
    observation_at = visual_fact.get("updated_at")
    if observation_at:
        try:
            observation_age = datetime.now() - datetime.strptime(
                observation_at, "%Y-%m-%d %H:%M:%S"
            )
            perception_link = "online" if observation_age <= timedelta(minutes=10) else "stale"
        except ValueError:
            perception_link = "degraded"
    perception = {
        "room_occupied": perception_link == "online" and str(visual_presence).lower() == "on",
        "visual_presence": visual_presence,
        "link_state": perception_link,
        "confidence": visual_fact.get("confidence", 0.0),
        "last_observation_at": observation_at,
        "presence_source": observation.get("signal"),
        "face_count": int(observation.get("face_count") or 0),
        "last_gesture": gesture.get("value"),
        "last_gesture_at": gesture.get("updated_at"),
        "privacy": {
            "processing": "local",
            "raw_frames_stored": False,
            "identity_recognition": False,
            "metadata_retention_hours": _vision_retention_hours(),
            "gesture_can_confirm_actions": False,
        },
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
        "sanctuary": {
            "mode": sanctuary_mode,
            "reason": sanctuary_reason.get("reason")
            if isinstance(sanctuary_reason, dict)
            else None,
            "source": sanctuary_reason.get("source")
            if isinstance(sanctuary_reason, dict)
            else None,
            "changed_at": all_facts.get("sanctuary.last_transition", {}).get(
                "updated_at"
            ),
            "automations_enabled": str(
                all_facts.get("sanctuary.automations_enabled", {}).get("value", "off")
            ).lower()
            == "on",
            "calibration_ready": str(
                all_facts.get("sanctuary.calibration_ready", {}).get("value", "off")
            ).lower()
            == "on",
            "manual_hold": str(
                all_facts.get("sanctuary.manual_hold", {}).get("value", "off")
            ).lower()
            == "on",
            "calibration": {
                key.removeprefix("sanctuary.calibration."): fact["value"]
                for key, fact in all_facts.items()
                if key.startswith("sanctuary.calibration.")
            },
            "rooms": rooms,
            "unassigned_lights": unassigned_lights,
            "missing_capabilities": sorted(set(missing_capabilities)),
            "protected_targets": ["entry_internet_power_switch"],
        },
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
        "voice": voice,
        "supervisor": supervisor,
        "perception": perception,
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
        (
            "voice",
            "Voice I/O",
            bool(snapshot.get("voice", {}).get("ready")),
            snapshot.get("voice", {}).get("reason", "Microphone, speakers, and voice satellite"),
        ),
        (
            "vision",
            "Local perception",
            hardware["camera"] == "on"
            or snapshot["perception"]["visual_presence"] in {"on", "off"},
            "Camera stream and local perception worker",
        ),
        (
            "proximity",
            "Bluetooth proximity",
            hardware["bluetooth"] == "on",
            "Powered Bluetooth adapter",
        ),
        ("touch", "Touch command surface", hardware["touchscreen"] == "on", "Touchscreen input"),
        ("storage", "External storage", hardware["external_storage"] == "on", "Mounted external drive"),
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
        "sanctuary": snapshot["sanctuary"],
        "proposals": list_proposals("pending"),
        "actions": ACTION_REGISTRY,
        "capabilities": capability_manifest(snapshot),
    }


def simulate_sanctuary_mode(
    mode: str, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Exercise the Sanctuary gate without calling HA or writing the database."""
    if mode not in SANCTUARY_MODES:
        raise ValueError(mode)
    scenario = deepcopy(overrides or {})
    source = str(scenario.setdefault("source", "schedule"))
    automations_enabled = bool(scenario.setdefault("automations_enabled", True))
    calibration_ready = bool(scenario.setdefault("calibration_ready", True))
    manual_hold = bool(scenario.setdefault("manual_hold", False))
    scenario.setdefault("person_home", True)
    scenario.setdefault("workday", True)
    scenario.setdefault("guest_mode", False)
    scenario.setdefault("recording", False)
    scenario.setdefault("current_mode", "Home Base")
    scenario.setdefault("unavailable_lights", [])
    allowed = (
        mode == "Emergency"
        or source != "schedule"
        or (automations_enabled and calibration_ready and not manual_hold)
    )
    rooms_by_mode = {
        "Home Base": ["Entry", "Living Room", "Dining Area", "Hallway"],
        "Morning": ["Bedroom", "Bathroom", "Office", "Hallway", "Dining Area", "Entry"],
        "Away": [],
        "Welcome": ["Entry", "Living Room", "Dining Area", "Hallway"],
        "Work": ["Office", "Hallway"],
        "Focus": ["Office", "Hallway"],
        "Create": ["Office", "Hallway"],
        "Studio": ["Office", "Hallway"],
        "Shower": ["Bathroom"]
        if scenario["guest_mode"]
        else ["Bathroom", "Entry", "Living Room", "Dining Area", "Hallway", "Bedroom", "Office"],
        "Wind Down": ["Bedroom", "Bathroom", "Hallway", "Dining Area"],
        "Thunderstorm": ["Bedroom", "Bathroom"],
        "Date Night": ["Dining Area", "Living Room", "Hallway", "Bedroom"],
        "Cleaning": [
            "Entry",
            "Living Room",
            "Dining Area",
            "Kitchen",
            "Hallway",
            "Bathroom",
            "Bedroom",
            "Office",
        ],
        "Guest": ["Entry", "Living Room", "Dining Area", "Hallway", "Bathroom"],
        "Cinema": ["Living Room", "Hallway"],
        "Vacation": [],
        "Emergency": [
            "Entry",
            "Living Room",
            "Dining Area",
            "Hallway",
            "Bathroom",
            "Bedroom",
            "Office",
        ],
        "Manual Hold": [],
    }
    predicted_actions = []
    shower_protected = mode == "Shower" and (
        scenario["recording"]
        or scenario["guest_mode"]
        or scenario["current_mode"] in {"Away", "Guest", "Date Night", "Vacation"}
    )
    if allowed and not shower_protected:
        predicted_actions.append(
            {
                "action_id": "sanctuary.emergency_lighting"
                if mode == "Emergency"
                else "sanctuary.activate_lighting_mode",
                "reason": f"Apply {mode} through the lighting-only state machine",
                "rooms": rooms_by_mode[mode],
                "domains": ["light"],
            }
        )
    status = "ready" if predicted_actions else "skipped"
    if shower_protected:
        skip_reason = "recording_protection" if scenario["recording"] else "mode_protection"
    elif not allowed:
        if manual_hold:
            skip_reason = "manual_hold"
        elif not calibration_ready:
            skip_reason = "calibration_required"
        else:
            skip_reason = "automations_disabled"
    else:
        skip_reason = None
    for item in predicted_actions:
        item["policy"] = ACTION_REGISTRY[item["action_id"]]
    return {
        "simulation": True,
        "behavior": "sanctuary",
        "mode": mode,
        "status": status,
        "skip_reason": skip_reason,
        "scenario": scenario,
        "predicted_actions": predicted_actions,
        "house_state_mutated": False,
        "action_execution": False,
    }


def simulate_behavior(behavior: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Predict a behavior without writing facts, proposals, audits, or HA state."""
    if behavior not in {"arrival", "departure", "nightly", "perception", "sanctuary"}:
        raise ValueError(behavior)
    scenario = deepcopy(overrides or {})
    if behavior == "sanctuary":
        return simulate_sanctuary_mode(str(scenario.pop("mode", "Home Base")), scenario)
    actions: list[dict[str, Any]] = []
    if behavior == "arrival":
        person = scenario.setdefault("person", "person.simulated_resident")
        actions.append({"action_id": "scene.arrival", "reason": f"{person} arrived home"})
    elif behavior == "departure":
        open_entries = scenario.setdefault("open_perimeter", ["binary_sensor.simulated_front_door"])
        hazards = scenario.setdefault("active_hazards", [])
        if open_entries or hazards:
            actions.append(
                {
                    "action_id": "notify.departure_anomaly",
                    "reason": "Open perimeter or active hazard detected",
                }
            )
        actions.append({"action_id": "security.arm_away", "reason": "Last person left"})
    elif behavior == "nightly":
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
    else:
        scenario.setdefault("visual_presence", True)
        scenario.setdefault("confidence", 0.86)
        scenario.setdefault("raw_frames_stored", False)
        scenario.setdefault("identity_recognition", False)
        scenario.setdefault("action_execution", False)
    for item in actions:
        item["policy"] = ACTION_REGISTRY[item["action_id"]]
    return {
        "simulation": True,
        "behavior": behavior,
        "scenario": scenario,
        "predicted_actions": actions,
    }


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
    if entity_id == "input_select.sanctuary_mode":
        return "sanctuary.mode"
    if entity_id == "input_boolean.sanctuary_automations_enabled":
        return "sanctuary.automations_enabled"
    if entity_id == "input_boolean.sanctuary_calibration_ready":
        return "sanctuary.calibration_ready"
    if entity_id == "input_boolean.sanctuary_manual_hold":
        return "sanctuary.manual_hold"
    if domain == "input_number" and name.startswith("sanctuary_"):
        return f"sanctuary.calibration.{name.removeprefix('sanctuary_')}"
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
    if isinstance(attributes, str):
        try:
            decoded_attributes = json.loads(attributes)
        except json.JSONDecodeError:
            decoded_attributes = {}
        attributes = decoded_attributes if isinstance(decoded_attributes, dict) else {}
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
        if source == "x1_hardware" and str(entity_id) == "binary_sensor.x1_microphone":
            set_fact("hardware.microphone_details", attributes, source)
        if source == "x1_hardware" and str(entity_id) == "binary_sensor.x1_speakers":
            set_fact("hardware.speaker_details", attributes, source)
        if source == "x1_supervisor" and str(entity_id).startswith("binary_sensor.jarvis_"):
            component = str(entity_id).removeprefix("binary_sensor.jarvis_")
            if component == "supervisor":
                set_fact("supervisor.heartbeat", attributes, source)
            else:
                set_fact(
                    f"supervisor.component.{component}",
                    {"state": str(state), **attributes},
                    source,
                )
        if str(entity_id).startswith("light."):
            set_fact(
                f"sanctuary.room_entity.{entity_id}",
                {
                    "state": str(state),
                    "area_id": attributes.get("sanctuary_area_id"),
                    "friendly_name": attributes.get("friendly_name"),
                },
                source,
                float(event.get("confidence", 1)),
            )
    if event_type == "sanctuary.changed":
        set_fact(
            "sanctuary.last_transition",
            {
                "mode": str(attributes.get("mode") or state or "unknown"),
                "previous_mode": attributes.get("previous_mode"),
                "reason": attributes.get("reason"),
                "source": attributes.get("source"),
            },
            source,
        )
        if attributes.get("mode"):
            set_fact("sanctuary.mode", str(attributes["mode"]), source)
    elif event_type == "sanctuary.skipped":
        set_fact("sanctuary.last_skipped", attributes, source)
    elif event_type == "sanctuary.calibration":
        set_fact("sanctuary.last_calibration_test", attributes, source)
    if event_type == "vision.gesture" and attributes.get("gesture"):
        set_fact(
            "perception.last_gesture",
            {
                "gesture": str(attributes["gesture"]),
                "app": str(attributes.get("app") or "unknown"),
            },
            source,
            float(event.get("confidence", 1)),
        )
    if event_type in {
        "vision.presence_changed",
        "vision.presence_heartbeat",
    }:
        set_fact(
            "perception.last_observation",
            {
                "state": str(state or "unknown"),
                "signal": attributes.get("signal"),
                "face_count": int(attributes.get("face_count") or 0),
                "identity_recognition": False,
            },
            source,
            float(event.get("confidence", 1)),
        )
    if source == "x1_vision":
        retention = _vision_retention_hours()
        with conn() as c:
            c.execute(
                "DELETE FROM context_events WHERE source='x1_vision' AND ts<datetime('now', ?)",
                (f"-{retention} hours",),
            )
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
    sanctuary = str(all_facts.get("sanctuary.mode", {}).get("value", ""))
    if sanctuary in SANCTUARY_MODES and sanctuary not in {"Home Base", "Manual Hold"}:
        mode = sanctuary.lower().replace(" ", "_")
    elif modes.get("vacation_mode") == "on":
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
        c.execute("UPDATE action_proposals SET status='dismissed' WHERE id=?", (proposal_id,))
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
    payload = deepcopy(action.get("default_data", {}))
    payload.update(data or {})
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
