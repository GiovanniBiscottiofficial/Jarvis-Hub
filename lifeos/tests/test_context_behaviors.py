import pytest
from fastapi.testclient import TestClient

from app.context_engine import (
    ACTION_REGISTRY,
    command_center_payload,
    current_context,
    execute_action,
    ingest_event,
    list_proposals,
    simulate_behavior,
)


def event(entity_id, state, previous_state=None, *, evaluate=True):
    return ingest_event(
        {
            "source": "simulation",
            "event_type": "state_changed",
            "entity_id": entity_id,
            "state": state,
            "previous_state": previous_state,
            "attributes": {"simulated": True},
        },
        evaluate=evaluate,
    )


def test_arrival_orchestration_proposes_arrival_scene(fresh_db):
    result = event("person.giovanni", "home", "not_home")

    proposals = list_proposals("pending")
    assert result["proposals_created"]
    assert [proposal["action_id"] for proposal in proposals] == ["scene.arrival"]
    assert proposals[0]["behavior"] == "arrival_orchestration"
    assert current_context()["occupancy"]["people_home"] == ["giovanni"]


def test_departure_detects_open_perimeter_and_proposes_arming(fresh_db):
    event("binary_sensor.front_door", "on", "off", evaluate=False)
    event("person.giovanni", "home", "not_home", evaluate=False)

    result = event("person.giovanni", "not_home", "home")

    action_ids = {proposal["action_id"] for proposal in list_proposals("pending")}
    assert len(result["proposals_created"]) == 2
    assert action_ids == {"notify.departure_anomaly", "security.arm_away"}
    assert current_context()["security"]["open_perimeter"] == ["binary_sensor.front_door"]


def test_nightly_review_reports_findings_and_proposes_night_mode(fresh_db):
    event("cover.garage_door", "open", "closed", evaluate=False)
    event("alarm_control_panel.home", "disarmed", "armed_home", evaluate=False)

    result = ingest_event(
        {
            "source": "simulation",
            "event_type": "schedule.nightly_security_review",
            "attributes": {"simulated": True},
        }
    )

    proposals = list_proposals("pending")
    by_action = {proposal["action_id"]: proposal for proposal in proposals}
    assert len(result["proposals_created"]) == 2
    assert set(by_action) == {"security.nightly_report", "security.arm_night"}
    assert "cover.garage_door" in by_action["security.nightly_report"]["context"]["findings"]
    assert "alarm:disarmed" in by_action["security.nightly_report"]["context"]["findings"]


def test_departure_detects_an_active_hazardous_device(fresh_db):
    event("switch.kitchen_oven", "on", "off", evaluate=False)
    event("person.giovanni", "home", "not_home", evaluate=False)

    event("person.giovanni", "not_home", "home")

    by_action = {proposal["action_id"]: proposal for proposal in list_proposals("pending")}
    assert by_action["notify.departure_anomaly"]["context"]["active_hazards"] == [
        "switch.kitchen_oven"
    ]


def test_action_policy_requires_confirmation_and_blocks_critical_remote_use(fresh_db):
    with pytest.raises(PermissionError, match="confirmation_required"):
        execute_action("security.arm_away", None, False, True, "simulation")

    simulated = execute_action("security.arm_away", None, True, True, "simulation")
    assert simulated["outcome"] == "simulated"

    with pytest.raises(PermissionError, match="local_confirmation_required"):
        execute_action("security.close_garage", None, True, False, "simulation")


def test_duplicate_events_do_not_spam_identical_proposals(fresh_db):
    event("person.giovanni", "home", "not_home")
    event("person.giovanni", "home", "not_home")

    assert len(list_proposals("pending")) == 1


def test_context_api_surface_uses_the_dry_run_policy(fresh_db):
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/auth", json={"token": "test-token"}).status_code == 200
    created = client.post(
        "/api/events",
        json={
            "source": "simulation",
            "entity_id": "person.giovanni",
            "state": "home",
            "previous_state": "not_home",
        },
    )
    assert created.status_code == 201
    assert client.get("/api/context").json()["occupancy"]["occupied"] is True
    assert len(client.get("/api/events").json()) == 1
    assert "scene.arrival" in client.get("/api/actions").json()

    proposal = client.get("/api/proposals?status=pending").json()[0]
    simulated = client.post(
        f"/api/actions/{proposal['action_id']}",
        json={"proposal_id": proposal["id"], "dry_run": True},
    )
    assert simulated.status_code == 200
    assert simulated.json()["outcome"] == "simulated"


def test_x1_hardware_events_are_projected_into_context(fresh_db):
    event("binary_sensor.x1_camera", "on", "unavailable", evaluate=False)
    event("binary_sensor.x1_bluetooth", "on", "off", evaluate=False)
    event("binary_sensor.x1_microphone", "on", "unavailable", evaluate=False)
    event("binary_sensor.x1_speakers", "on", "unavailable", evaluate=False)
    event("sensor.x1_battery", "78", "77", evaluate=False)

    hardware = current_context()["hardware"]
    assert hardware["camera"] == "on"
    assert hardware["bluetooth"] == "on"
    assert hardware["microphone"] == "on"
    assert hardware["speakers"] == "on"
    assert hardware["battery"] == "78"


def test_every_action_has_complete_explainable_policy(fresh_db):
    required = {
        "name",
        "description",
        "risk",
        "scope",
        "reversible",
        "confirmation_policy",
        "requires_confirmation",
        "remote_execution",
    }
    assert all(required <= action.keys() for action in ACTION_REGISTRY.values())
    assert ACTION_REGISTRY["security.close_garage"]["confirmation_policy"] == (
        "local_visual_confirmation"
    )


def test_simulations_predict_actions_without_mutating_the_house(fresh_db):
    before = current_context()
    result = simulate_behavior("departure")
    after = current_context()

    assert result["simulation"] is True
    assert {item["action_id"] for item in result["predicted_actions"]} == {
        "notify.departure_anomaly",
        "security.arm_away",
    }
    assert before["facts"] == after["facts"]
    assert list_proposals() == []


def test_command_center_fuses_house_and_lifeos_state(fresh_db):
    payload = command_center_payload()

    assert payload["context"]["lifeos"]["profile"] == "Giovanni"
    assert "daily_score" in payload["context"]["lifeos"]
    assert {capability["id"] for capability in payload["capabilities"]} >= {
        "context",
        "voice",
        "vision",
        "lifeos",
    }


def test_local_vision_projects_privacy_minimized_perception(fresh_db):
    ingest_event(
        {
            "source": "x1_vision",
            "event_type": "vision.presence_changed",
            "entity_id": "binary_sensor.x1_visual_presence",
            "state": "on",
            "previous_state": "off",
            "confidence": 0.86,
            "attributes": {"signal": "hand_landmarks", "frames_stored": False},
        },
        evaluate=False,
    )
    ingest_event(
        {
            "source": "x1_vision",
            "event_type": "vision.gesture",
            "confidence": 0.82,
            "attributes": {
                "gesture": "forward",
                "app": "plex",
                "frames_stored": False,
            },
        },
        evaluate=False,
    )

    perception = current_context()["perception"]
    assert perception["room_occupied"] is True
    assert perception["confidence"] == pytest.approx(0.86)
    assert perception["last_gesture"] == {"gesture": "forward", "app": "plex"}
    assert perception["privacy"] == {
        "processing": "local",
        "raw_frames_stored": False,
        "identity_recognition": False,
        "metadata_retention_hours": 24,
        "gesture_can_confirm_actions": False,
    }


def test_perception_simulation_is_read_only(fresh_db):
    before = current_context()
    result = simulate_behavior("perception", {"visual_presence": False})
    after = current_context()

    assert result["simulation"] is True
    assert result["predicted_actions"] == []
    assert result["scenario"]["visual_presence"] is False
    assert result["scenario"]["raw_frames_stored"] is False
    assert result["scenario"]["action_execution"] is False
    assert before["facts"] == after["facts"]


def test_simulation_and_proposal_lifecycle_api(fresh_db):
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/auth", json={"token": "test-token"}).status_code == 200
    simulation = client.post("/api/simulations/arrival", json={"overrides": {}})
    assert simulation.status_code == 200
    assert simulation.json()["predicted_actions"][0]["action_id"] == "scene.arrival"
    perception = client.post(
        "/api/simulations/perception",
        json={"overrides": {"visual_presence": True}},
    )
    assert perception.status_code == 200
    assert perception.json()["predicted_actions"] == []
    assert perception.json()["scenario"]["action_execution"] is False

    client.post(
        "/api/events",
        json={
            "source": "simulation",
            "entity_id": "person.giovanni",
            "state": "home",
            "previous_state": "not_home",
        },
    )
    proposal = client.get("/api/proposals?status=pending").json()[0]
    dismissed = client.post(
        f"/api/proposals/{proposal['id']}/dismiss",
        json={"requested_by": "test"},
    )
    assert dismissed.status_code == 200
    assert client.get("/api/proposals?status=pending").json() == []
    assert client.get("/api/actions/audit").json()[0]["outcome"] == "dismissed"
