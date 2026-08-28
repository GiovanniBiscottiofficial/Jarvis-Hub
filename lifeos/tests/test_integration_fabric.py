from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.integration_fabric import build_integration_fabric


AUTH = {"Authorization": "Bearer test-token"}
REPO_ROOT = Path(__file__).resolve().parents[2]


def _connected_snapshot():
    return {
        "telemetry": {"link_state": "online"},
        "sanctuary": {
            "automations_enabled": True,
            "calibration_ready": True,
            "missing_capabilities": ["vacuum"],
        },
        "voice": {"ready": True, "reason": "Conversation bridge is online."},
        "perception": {"link_state": "online"},
        "supervisor": {"state": "online"},
        "lifeos": {"food": {"pantry_item_count": 4}},
        "devices": {"sensor.ihome_weight": {"state": "185.2"}},
    }


def test_every_subsystem_declares_both_sides_and_canonical_state():
    fabric = build_integration_fabric(
        _connected_snapshot(),
        learning={"policy": {}},
        intelligence={"status": "nominal"},
    )

    assert fabric["status"] == "ready"
    assert fabric["orphaned_nodes"] == []
    assert fabric["integrity"] == {
        "all_nodes_have_producers": True,
        "all_nodes_have_consumers": True,
        "canonical_state_declared": True,
        "uncommissioned_is_not_failure": True,
        "actions_remain_policy_gated": True,
    }
    for node in fabric["nodes"]:
        assert node["producers"]
        assert node["consumers"]
        assert node["canonical_state"]


def test_optional_disconnection_is_visible_without_claiming_core_disconnect():
    snapshot = _connected_snapshot()
    snapshot["perception"]["link_state"] = "stale"
    snapshot["supervisor"]["state"] = "stale"

    fabric = build_integration_fabric(
        snapshot,
        learning={"policy": {}},
        intelligence={"status": "nominal"},
    )

    assert fabric["status"] == "degraded"
    assert fabric["required_disconnected"] == []
    assert fabric["node_states"]["vision_gestures"] == "disconnected"
    assert fabric["node_states"]["supervisor"] == "disconnected"


def test_required_control_plane_disconnection_is_authoritative():
    snapshot = _connected_snapshot()
    snapshot["telemetry"]["link_state"] = "stale"

    fabric = build_integration_fabric(
        snapshot,
        learning={"policy": {}},
        intelligence={"status": "nominal"},
    )

    assert fabric["status"] == "disconnected"
    assert fabric["required_disconnected"] == ["home_assistant"]


def test_integration_api_is_in_command_center_and_home_assistant(fresh_db):
    client = TestClient(main.app, headers=AUTH)

    response = client.get("/api/integrations")
    command = client.get("/api/command-center?event_limit=1")

    assert response.status_code == 200
    assert command.status_code == 200
    assert command.json()["integrations"]["node_states"] == response.json()["node_states"]

    configuration = (REPO_ROOT / "ha-config/configuration.yaml").read_text(
        encoding="utf-8"
    )
    assert "resource: http://localhost:8090/api/integrations" in configuration
    assert "unique_id: jarvis_integration_fabric" in configuration


def test_command_center_renders_integration_health_with_safe_dom():
    static = REPO_ROOT / "lifeos/app/static"
    markup = (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "integration-state",
        "integration-summary",
        "integration-list",
        "integration-issues",
    ):
        assert f'id="{element_id}"' in markup
    assert "renderIntegrationFabric(payload.integrations || {})" in script
    assert "integration-list\").innerHTML" not in script
