"""Live integration contract for the Jarvis/LifeOS system graph."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _node(
    node_id: str,
    name: str,
    state: str,
    *,
    required: bool,
    reason: str,
    producers: list[str],
    consumers: list[str],
    canonical_state: str,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "name": name,
        "state": state,
        "required": required,
        "reason": reason,
        "producers": producers,
        "consumers": consumers,
        "canonical_state": canonical_state,
    }


def build_integration_fabric(
    snapshot: dict[str, Any],
    *,
    learning: dict[str, Any] | None = None,
    intelligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe subsystem connectivity without probing or mutating services."""
    telemetry = snapshot.get("telemetry") or {}
    sanctuary = snapshot.get("sanctuary") or {}
    voice = snapshot.get("voice") or {}
    perception = snapshot.get("perception") or {}
    supervisor = snapshot.get("supervisor") or {}
    lifeos = snapshot.get("lifeos") or {}
    missing = set(sanctuary.get("missing_capabilities") or [])
    devices = snapshot.get("devices") or {}

    telemetry_state = str(telemetry.get("link_state") or "awaiting_data")
    ha_state = "ready" if telemetry_state == "online" else (
        "awaiting" if telemetry_state == "awaiting_data" else "disconnected"
    )
    sanctuary_ready = bool(sanctuary.get("automations_enabled")) and bool(
        sanctuary.get("calibration_ready")
    )
    supervisor_state = str(supervisor.get("state") or "awaiting")
    supervisor_node_state = "ready" if supervisor_state == "online" else (
        "disconnected" if supervisor_state in {"stale", "degraded"} else "awaiting"
    )
    perception_state = str(perception.get("link_state") or "awaiting_signal")
    vision_state = "ready" if perception_state == "online" else (
        "disconnected" if perception_state in {"stale", "degraded"} else "awaiting"
    )
    media_state = "uncommissioned" if "media" in missing else "ready"
    scale_entities = [
        entity_id for entity_id in devices
        if entity_id.startswith("sensor.") and "weight" in entity_id.lower()
    ]
    scale_state = "ready" if scale_entities else "uncommissioned"
    intelligence_state = str((intelligence or {}).get("status") or "degraded")
    intelligence_node_state = "ready" if intelligence_state in {"nominal", "attention"} else "degraded"
    learning_loaded = learning is not None and "policy" in learning

    nodes = [
        _node(
            "home_assistant", "Home Assistant control plane", ha_state,
            required=True,
            reason=f"Context event link is {telemetry_state.replace('_', ' ')}.",
            producers=["entity state changes", "Sanctuary helpers", "Assist intents"],
            consumers=["context", "sanctuary", "voice", "media"],
            canonical_state="context_events + context_facts",
        ),
        _node(
            "context", "Context and event engine", "ready",
            required=True,
            reason="Durable facts, recent events, and proposals share one context snapshot.",
            producers=["home_assistant", "voice", "vision_gestures", "supervisor"],
            consumers=["intelligence", "command_center", "briefing", "action_policy"],
            canonical_state="/api/context",
        ),
        _node(
            "sanctuary", "Sanctuary state machine", "ready" if sanctuary_ready else "degraded",
            required=False,
            reason=(
                "Mode automation and calibration are ready."
                if sanctuary_ready
                else "Manual control remains available; automation or calibration is not fully enabled."
            ),
            producers=["context", "manual controls", "schedule", "presence"],
            consumers=["home_assistant", "media", "context", "action_audit"],
            canonical_state="input_select.sanctuary_mode + context.sanctuary",
        ),
        _node(
            "body_ops", "Body Ops", "ready",
            required=True,
            reason="Targets and daily measures feed every operating surface.",
            producers=["LifeOS UI", "voice intents", "scale", "manual check-ins"],
            consumers=["today", "intelligence", "briefing", "weekly_review"],
            canonical_state="lifeos_snapshot.body",
        ),
        _node(
            "chef_pantry", "Chef Jarvis and pantry", "ready",
            required=True,
            reason=f"{int((lifeos.get('food') or {}).get('pantry_item_count') or 0)} pantry item(s) are in canonical inventory.",
            producers=["LifeOS UI", "voice intents", "Grocy sync", "meal feedback"],
            consumers=["today", "intelligence", "market_list", "briefing"],
            canonical_state="lifeos_snapshot.food",
        ),
        _node(
            "budget_vault", "Budget and Vault", "ready",
            required=True,
            reason="Accounts, bills, spending, and paycheck state use the shared ledger.",
            producers=["LifeOS UI", "voice intents", "imports", "paycheck funding"],
            consumers=["today", "intelligence", "briefing", "weekly_review"],
            canonical_state="lifeos_snapshot.vault + money command center",
        ),
        _node(
            "learning", "Learning ledger", "ready" if learning_loaded else "degraded",
            required=True,
            reason=(
                "Confirmed guidance and candidates are available to shared consumers."
                if learning_loaded
                else "Learning snapshot was not attached to this assessment."
            ),
            producers=["explicit feedback", "voice memory", "Chef feedback", "Body Ops"],
            consumers=["intelligence", "briefing", "Chef ranking", "Learning UI"],
            canonical_state="/api/learning",
        ),
        _node(
            "intelligence", "Cross-domain intelligence", intelligence_node_state,
            required=True,
            reason=f"Decision support is {intelligence_state} and remains advisory-only.",
            producers=["context", "body_ops", "chef_pantry", "budget_vault", "learning"],
            consumers=["command_center", "briefing", "simulations"],
            canonical_state="/api/intelligence",
        ),
        _node(
            "voice", "Jarvis voice", "ready" if voice.get("ready") else "degraded",
            required=False,
            reason=str(voice.get("reason") or "Voice readiness telemetry is incomplete."),
            producers=["Jabra microphone", "Home Assistant Assist", "wake-word satellite"],
            consumers=["Home Assistant intents", "LifeOS mutations", "context", "speakers"],
            canonical_state="context.voice + conversation bridge",
        ),
        _node(
            "vision_gestures", "Vision and gestures", vision_state,
            required=False,
            reason=f"Local perception link is {perception_state.replace('_', ' ')}; raw frames are not stored.",
            producers=["X1 camera", "local gesture worker"],
            consumers=["context", "media controls", "app navigation"],
            canonical_state="context.perception",
        ),
        _node(
            "media", "Apps and media", media_state,
            required=False,
            reason=(
                "Commissioned media entities feed playback context."
                if media_state == "ready"
                else "No media entity is commissioned; controls remain capability-gated."
            ),
            producers=["voice", "vision_gestures", "Sanctuary modes", "Media Command"],
            consumers=["Fire TV", "X1 player", "context"],
            canonical_state="media_player entities + sanctuary media context",
        ),
        _node(
            "scale", "Tuya smart scale", scale_state,
            required=False,
            reason=(
                f"{scale_entities[0]} feeds Body Ops weigh-ins."
                if scale_entities
                else "No Home Assistant weight sensor is commissioned; manual weigh-ins remain available."
            ),
            producers=["Tuya / Smart Life", "Home Assistant weight sensor"],
            consumers=["body_ops", "weekly_review"],
            canonical_state="weighins",
        ),
        _node(
            "supervisor", "Self-healing supervisor", supervisor_node_state,
            required=False,
            reason=f"Supervisor heartbeat is {supervisor_state}.",
            producers=["service health probes"],
            consumers=["context", "Command Center", "allow-listed recovery"],
            canonical_state="context.supervisor",
        ),
        _node(
            "command_surfaces", "Jarvis command surfaces", "ready",
            required=True,
            reason="Today, Command, Body Ops, To-do, Budget, Learning, and Review use shared APIs and refresh events.",
            producers=["canonical LifeOS APIs", "Home Assistant panel session"],
            consumers=["Giovanni", "data mutation events", "simulation requests"],
            canonical_state="LifeOS UI + Home Assistant dashboards",
        ),
    ]
    edges = []
    node_ids = {node["id"] for node in nodes}
    declared = {
        "home_assistant": ["context", "sanctuary", "voice", "media", "scale"],
        "context": ["intelligence", "command_surfaces", "sanctuary"],
        "sanctuary": ["home_assistant", "media", "context"],
        "body_ops": ["intelligence", "command_surfaces"],
        "chef_pantry": ["intelligence", "body_ops", "command_surfaces"],
        "budget_vault": ["intelligence", "command_surfaces"],
        "learning": ["intelligence", "chef_pantry", "command_surfaces"],
        "intelligence": ["command_surfaces", "voice"],
        "voice": ["home_assistant", "context", "body_ops", "chef_pantry", "budget_vault"],
        "vision_gestures": ["context", "media", "command_surfaces"],
        "media": ["context", "home_assistant"],
        "scale": ["body_ops"],
        "supervisor": ["context", "command_surfaces"],
        "command_surfaces": ["body_ops", "chef_pantry", "budget_vault", "learning", "context"],
    }
    for source, targets in declared.items():
        for target in targets:
            edges.append({"source": source, "target": target})
    connected = {
        node_id: {
            "incoming": sum(edge["target"] == node_id for edge in edges),
            "outgoing": sum(edge["source"] == node_id for edge in edges),
        }
        for node_id in node_ids
    }
    orphaned = sorted(
        node["id"] for node in nodes
        if not node["producers"] or not node["consumers"]
    )
    disconnected = [node for node in nodes if node["state"] == "disconnected"]
    required_disconnected = [node for node in disconnected if node["required"]]
    optional_disconnected = [node for node in disconnected if not node["required"]]
    required_attention = [
        node for node in nodes
        if node["required"] and node["state"] not in {"ready", "uncommissioned"}
    ]
    status = "disconnected" if required_disconnected or orphaned else (
        "degraded" if required_attention or optional_disconnected else "ready"
    )
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "ready": sum(node["state"] == "ready" for node in nodes),
            "degraded": sum(node["state"] in {"degraded", "awaiting"} for node in nodes),
            "disconnected": len(disconnected),
            "uncommissioned": sum(node["state"] == "uncommissioned" for node in nodes),
            "orphaned": len(orphaned),
        },
        "node_states": {node["id"]: node["state"] for node in nodes},
        "nodes": nodes,
        "edges": edges,
        "topology": connected,
        "orphaned_nodes": orphaned,
        "issues": [
            {"id": node["id"], "state": node["state"], "reason": node["reason"]}
            for node in nodes
            if node["state"] not in {"ready", "uncommissioned"}
        ],
        "required_disconnected": [node["id"] for node in required_disconnected],
        "integrity": {
            "all_nodes_have_producers": not orphaned,
            "all_nodes_have_consumers": not orphaned,
            "canonical_state_declared": all(node["canonical_state"] for node in nodes),
            "uncommissioned_is_not_failure": True,
            "actions_remain_policy_gated": True,
        },
    }
