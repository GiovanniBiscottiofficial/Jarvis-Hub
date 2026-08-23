from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.context_engine import (
    ACTION_REGISTRY,
    SANCTUARY_MODES,
    current_context,
    ingest_event,
    simulate_sanctuary_mode,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_home_assistant_session_exchange_uses_signed_http_only_cookie(
    fresh_db, monkeypatch
):
    from app import main

    async def verified(token):
        assert token == "ha-browser-token"
        return {"id": "owner-1", "name": "Giovanni"}

    monkeypatch.setattr(main, "_verify_home_assistant_token", verified)
    client = TestClient(main.app)
    response = client.post(
        "/api/auth/home-assistant", json={"token": "ha-browser-token"}
    )

    assert response.status_code == 200
    cookie = response.cookies.get("lifeos_session")
    assert cookie and cookie != "test-token" and "." in cookie
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/context").status_code == 200


def test_invalid_home_assistant_session_is_rejected(fresh_db, monkeypatch):
    from app import main

    async def rejected(_token):
        raise ValueError("invalid")

    monkeypatch.setattr(main, "_verify_home_assistant_token", rejected)
    client = TestClient(main.app)
    response = client.post("/api/auth/home-assistant", json={"token": "bad"})
    assert response.status_code == 401
    assert "lifeos_session" not in response.cookies


def test_raw_lifeos_secret_is_not_accepted_as_a_browser_cookie(fresh_db):
    from app import main

    client = TestClient(main.app)
    client.cookies.set("lifeos_session", "test-token")
    assert client.get("/api/context").status_code == 401


@pytest.mark.parametrize("mode", sorted(SANCTUARY_MODES))
def test_every_sanctuary_mode_is_side_effect_free_in_simulation(fresh_db, mode):
    before = current_context()
    result = simulate_sanctuary_mode(mode)
    after = current_context()

    assert result["simulation"] is True
    assert result["house_state_mutated"] is False
    assert result["action_execution"] is False
    assert before["facts"] == after["facts"]
    assert all(action["domains"] == ["light"] for action in result["predicted_actions"])


def test_scheduled_modes_respect_disable_and_manual_hold(fresh_db):
    disabled = simulate_sanctuary_mode(
        "Morning", {"source": "schedule", "automations_enabled": False}
    )
    held = simulate_sanctuary_mode(
        "Welcome", {"source": "schedule", "manual_hold": True}
    )
    uncalibrated = simulate_sanctuary_mode(
        "Morning", {"source": "schedule", "calibration_ready": False}
    )
    manual = simulate_sanctuary_mode(
        "Welcome",
        {"source": "floor_plan", "automations_enabled": False, "manual_hold": True},
    )

    assert disabled["status"] == "skipped"
    assert disabled["skip_reason"] == "automations_disabled"
    assert held["status"] == "skipped"
    assert held["skip_reason"] == "manual_hold"
    assert uncalibrated["status"] == "skipped"
    assert uncalibrated["skip_reason"] == "calibration_required"
    assert manual["status"] == "ready"


def test_emergency_bypasses_lifestyle_gate_but_stays_lighting_only(fresh_db):
    result = simulate_sanctuary_mode(
        "Emergency",
        {"source": "schedule", "automations_enabled": False, "manual_hold": True},
    )
    assert result["status"] == "ready"
    assert result["predicted_actions"][0]["action_id"] == (
        "sanctuary.emergency_lighting"
    )
    assert result["predicted_actions"][0]["domains"] == ["light"]


def test_shower_protects_recording_guest_date_night_and_away(fresh_db):
    recording = simulate_sanctuary_mode("Shower", {"recording": True})
    guest = simulate_sanctuary_mode("Shower", {"guest_mode": True})
    date_night = simulate_sanctuary_mode(
        "Shower", {"current_mode": "Date Night"}
    )
    away = simulate_sanctuary_mode("Shower", {"current_mode": "Away"})

    assert recording["status"] == "skipped"
    assert recording["skip_reason"] == "recording_protection"
    for result in (guest, date_night, away):
        assert result["status"] == "skipped"
        assert result["skip_reason"] == "mode_protection"


def test_late_arrival_and_non_workday_remain_valid_context(fresh_db):
    arrival = simulate_sanctuary_mode(
        "Welcome", {"person_home": True, "workday": False, "hour": 23}
    )
    assert arrival["status"] == "ready"
    assert set(arrival["predicted_actions"][0]["rooms"]) == {
        "Entry",
        "Living Room",
        "Dining Area",
        "Hallway",
    }


def test_sanctuary_context_tracks_rooms_transition_and_missing_capabilities(fresh_db):
    ingest_event(
        {
            "source": "home_assistant",
            "event_type": "inventory.snapshot",
            "entity_id": "light.bedroom_bedroom_light_1",
            "state": "on",
            "attributes": {
                "friendly_name": "Bedroom Light 1",
                "sanctuary_area_id": "bedroom",
            },
        },
        evaluate=False,
    )
    ingest_event(
        {
            "source": "home_assistant",
            "event_type": "sanctuary.changed",
            "entity_id": "input_select.sanctuary_mode",
            "state": "Thunderstorm",
            "attributes": {
                "mode": "Thunderstorm",
                "previous_mode": "Wind Down",
                "reason": "Protected nightly state",
                "source": "schedule",
            },
        },
        evaluate=False,
    )

    sanctuary = current_context()["sanctuary"]
    bedroom = next(room for room in sanctuary["rooms"] if room["name"] == "Bedroom")
    assert sanctuary["mode"] == "Thunderstorm"
    assert sanctuary["reason"] == "Protected nightly state"
    assert bedroom["active_lights"] == 1
    assert "bedroom_light_2" in sanctuary["missing_capabilities"]
    assert sanctuary["protected_targets"] == ["entry_internet_power_switch"]


def test_action_policies_cover_sanctuary_authority(fresh_db):
    assert ACTION_REGISTRY["sanctuary.activate_lighting_mode"]["risk"] == "low"
    assert ACTION_REGISTRY["sanctuary.activate_away"]["requires_confirmation"] is True
    assert ACTION_REGISTRY["sanctuary.emergency_lighting"]["scope"] == (
        "physical_safety"
    )
    assert ACTION_REGISTRY["scene.arrival"]["service"] == (
        "script.sanctuary_activate_mode"
    )


def test_sanctuary_yaml_never_targets_protected_domains():
    script = (REPO_ROOT / "ha-config/scripts/sanctuary.yaml").read_text()
    forbidden_services = (
        "switch.turn_",
        "lock.",
        "alarm_control_panel.",
        "cover.",
        "climate.",
        "vacuum.",
    )
    assert not any(service in script for service in forbidden_services)
    assert "light.turn_on" in script
    assert "light.turn_off" in script
    assert "entry_internet_power" not in script


def test_timeline_and_calibration_gate_are_declared():
    automation = (REPO_ROOT / "ha-config/automations/sanctuary.yaml").read_text()
    for time in ("06:30:00", "06:40:00", "06:50:00", "07:00:00", "07:20:00"):
        assert time in automation
    for time in ("20:00:00", "21:00:00", "21:30:00", "22:00:00", "22:30:00"):
        assert time in automation
    assert "input_boolean.sanctuary_automations_enabled" in automation
    assert "input_boolean.sanctuary_calibration_ready" in automation
    assert "lighting_persists_until: weekday_sunrise" in automation
