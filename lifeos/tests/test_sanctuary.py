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


def test_embedded_browser_auth_uses_home_assistant_handshake_without_prompt():
    app_script = (REPO_ROOT / "lifeos/app/static/app.js").read_text(encoding="utf-8")
    wrapper = (REPO_ROOT / "ha-config/www/lifeos.html").read_text(encoding="utf-8")
    panel = (REPO_ROOT / "ha-config/www/lifeos-panel.js").read_text(encoding="utf-8")
    configuration = (REPO_ROOT / "ha-config/configuration.yaml").read_text(encoding="utf-8")

    assert "lifeos-auth-request" in app_script
    assert "lifeos-auth-request" in wrapper
    assert "lifeos-auth-request" in panel
    assert "lifeos-ha-auth" in app_script
    assert "lifeos-ha-auth" in wrapper
    assert "lifeos-ha-auth" in panel
    assert "hass.connection && hass.connection.options" in wrapper
    assert "hass.connection && hass.connection.options" in panel
    assert "auth.refreshAccessToken()" in wrapper
    assert "auth.refreshAccessToken()" in panel
    assert "auth.expired" in wrapper
    assert "auth.expired" in panel
    assert "window.prompt" not in app_script
    assert "localStorage" not in wrapper
    assert "sessionStorage" not in wrapper
    assert "localStorage" not in panel
    assert "sessionStorage" not in panel
    assert "?embedded=home-assistant" in wrapper
    assert "name: jarvis-lifeos-panel" in configuration
    assert "url_path: lifeos-app" in configuration

    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "HOME_ASSISTANT_URL=${HOME_ASSISTANT_URL:-http://host.docker.internal:8123}" in compose
    assert "host.docker.internal:host-gateway" in compose


def test_embedded_briefing_uses_authenticated_home_assistant_voice_bridge():
    app_script = (REPO_ROOT / "lifeos/app/static/app.js").read_text(encoding="utf-8")
    wrapper = (REPO_ROOT / "ha-config/www/lifeos.html").read_text(encoding="utf-8")
    panel = (REPO_ROOT / "ha-config/www/lifeos-panel.js").read_text(encoding="utf-8")
    configuration = (REPO_ROOT / "ha-config/configuration.yaml").read_text(encoding="utf-8")

    for source in (app_script, wrapper, panel):
        assert "lifeos-speak-request" in source
    for host in (wrapper, panel):
        assert "jarvis_say" in host
        assert "urgent: true" in host
    assert "lifeos-speak-result" in app_script
    assert "requestId" in app_script
    assert "speakThroughHomeAssistant(speech)" in app_script
    assert 'allow="camera; microphone; autoplay"' in wrapper
    assert 'this._frame.allow = "camera; microphone; autoplay"' in panel
    assert "parent_origin=" in wrapper
    assert "parent_origin=" in panel
    assert 'get("parent_origin")' in app_script
    assert "app.js?v=3" in (REPO_ROOT / "lifeos/app/static/index.html").read_text(encoding="utf-8")
    assert "Promise.resolve(speechJob).catch" in wrapper
    assert "Promise.resolve(speechJob).catch" in panel
    assert "lifeos-panel.js?v=6" in configuration


def test_spoken_briefing_refreshes_live_and_accepts_natural_requests():
    intents = (REPO_ROOT / "ha-config/intents.yaml").read_text(encoding="utf-8")
    sentences = (
        REPO_ROOT / "ha-config/custom_sentences/en/lifeos.yaml"
    ).read_text(encoding="utf-8")
    configuration = (
        REPO_ROOT / "ha-config/configuration.yaml"
    ).read_text(encoding="utf-8")

    briefing_intent = intents.split("LifeOSBriefing:", 1)[1].split("\nLifeOS", 1)[0]
    assert "homeassistant.update_entity" in briefing_intent
    assert "sensor.lifeos_morning_briefing" in briefing_intent
    assert '"catch me up"' in sentences
    assert '"what do I need to know [right now]"' in sentences
    assert "briefing_period" in configuration
    assert "sections" in configuration


def test_jarvis_voice_profile_is_local_tuned_and_name_safe():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    speech = (REPO_ROOT / "ha-config/scripts/jarvis_say.yaml").read_text(encoding="utf-8")
    personality = (REPO_ROOT / "docs/jarvis-personality.txt").read_text(encoding="utf-8")

    assert "${PIPER_VOICE:-en_GB-alan-medium}" in compose
    assert "${PIPER_LENGTH_SCALE:-1.03}" in compose
    assert "${PIPER_SENTENCE_SILENCE:-0.18}" in compose
    assert "spoken_message" in speech
    assert "regex_replace('(?i)\\\\bsir\\\\b', 'Giovanni')" in speech
    assert "preannounce:" in speech
    assert "Never call him \"sir.\"" in personality


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
    media = ACTION_REGISTRY["sanctuary.thunderstorm_media"]
    assert media["risk"] == "low"
    assert media["reversible"] is True
    assert media["default_data"]["force"] is False


def test_thunderstorm_media_is_available_but_protects_scheduled_playback():
    configuration = (REPO_ROOT / "ha-config/configuration.yaml").read_text()
    script = (REPO_ROOT / "ha-config/scripts/sanctuary.yaml").read_text()

    assert "sanctuary_thunderstorm_tv_enabled:" in configuration
    assert "https://www.youtube.com/watch?v=oebqrILXLWs" in configuration
    assert "sanctuary_start_thunderstorm_media:" in script
    assert "media_player.fire_tv_192_168_1_62" in script
    assert "active_playback_protected" in script
    assert "tv_state in ['playing', 'paused']" in script
    assert "transition_source in ['voice', 'manual', 'floor_plan']" in script
    assert "androidtv.adb_command" in script
    assert script.count("continue_on_error: true") >= 2


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


def test_room_lighting_wakes_unknown_bulbs_but_skips_unavailable_devices():
    script = (REPO_ROOT / "ha-config/scripts/sanctuary.yaml").read_text()
    apply_room = script.split("sanctuary_apply_room_lighting:", 1)[1].split(
        "sanctuary_start_thunderstorm_media:", 1
    )[0]

    assert "rejectattr('state', 'eq', 'unavailable')" in apply_room
    assert "['unavailable', 'unknown']" not in apply_room
    assert apply_room.count("continue_on_error: true") >= 3


def test_tuya_color_calls_keep_the_requested_dim_level():
    script = (REPO_ROOT / "ha-config/scripts/sanctuary.yaml").read_text()
    apply_room = script.split("sanctuary_apply_room_lighting:", 1)[1].split(
        "sanctuary_start_thunderstorm_media:", 1
    )[0]
    assert apply_room.count("brightness_pct:") >= 3
    assert "color_temp_kelvin:" in apply_room
    assert "hs_color:" in apply_room


def test_room_lighting_zero_means_off_and_cannot_be_relit_by_color_calls():
    script = (REPO_ROOT / "ha-config/scripts/sanctuary.yaml").read_text()
    apply_room = script.split("sanctuary_apply_room_lighting:", 1)[1].split(
        "sanctuary_apply_deep_wind_down:", 1
    )[0]
    assert "brightness_value | int <= 0" in apply_room
    assert "service: light.turn_off" in apply_room
    assert apply_room.count("brightness_value | int > 0") >= 3


def test_deep_wind_down_covers_every_lit_area_and_clears_task_lighting():
    script = (REPO_ROOT / "ha-config/scripts/sanctuary.yaml").read_text()
    profile = script.split("sanctuary_apply_deep_wind_down:", 1)[1].split(
        "sanctuary_start_thunderstorm_media:", 1
    )[0]
    expected = {
        "bedroom": 4,
        "bathroom": 3,
        "hallway": 1,
        "dinning_room": 3,
        "entry": 0,
        "office": 0,
    }
    for area, brightness in expected.items():
        assert f"area: {area}, brightness: {brightness}" in profile


def test_thunderstorm_uses_exact_protected_overnight_layout():
    script = (REPO_ROOT / "ha-config/scripts/sanctuary.yaml").read_text()
    thunderstorm = script.split("sanctuary_apply_thunderstorm_lighting:", 1)[1].split(
        "sanctuary_start_thunderstorm_media:", 1
    )[0]
    for entity in (
        "light.bathroom_bathroom_light_1",
        "light.bathroom_bathroom_light_2",
        "light.bathroom_bathroom_light_3",
        "light.bathroom_bathroom_light_4",
        "light.hallway_hall_light",
        "light.entry_entry_light",
        "light.office_light_1",
        "light.office_office_light_2",
    ):
        assert entity in thunderstorm.split("service: light.turn_on", 1)[0]
    night_lights = thunderstorm.split("service: light.turn_on", 1)[1]
    assert "light.bathroom_bathroom_light_5" in night_lights
    assert "light.dinning_room_dinning_room_light" in night_lights
    assert "brightness_pct: 1" in night_lights
    assert "area: bedroom" in thunderstorm
    assert "[requested, 2] | min" in thunderstorm


def test_evening_profiles_avoid_tuya_hs_mode_brightness_reset():
    script = (REPO_ROOT / "ha-config/scripts/sanctuary.yaml").read_text()
    automation = (REPO_ROOT / "ha-config/automations/sanctuary.yaml").read_text()
    recipes = script.split("room_recipes:", 1)[1].split(
        "- condition: template", 1
    )[0]
    shower = script.split("requested_mode == 'Shower'", 1)[1].split(
        "requested_mode in room_recipes", 1
    )[0]

    assert "hue:" not in recipes
    assert "hue:" not in shower
    stronger_wind_down = automation.split("trigger.id == 'stronger_wind_down'", 1)[1].split(
        "trigger.id == 'thunderstorm'", 1
    )[0]
    assert "hue:" not in stronger_wind_down
    assert "script.sanctuary_apply_deep_wind_down" in stronger_wind_down


def test_timeline_and_calibration_gate_are_declared():
    automation = (REPO_ROOT / "ha-config/automations/sanctuary.yaml").read_text()
    for time in ("06:30:00", "06:40:00", "06:50:00", "07:00:00", "07:20:00"):
        assert time in automation
    for time in ("20:00:00", "21:00:00", "21:30:00", "22:00:00", "22:30:00"):
        assert time in automation
    assert "input_boolean.sanctuary_automations_enabled" in automation
    assert "input_boolean.sanctuary_calibration_ready" in automation
    assert "lighting_persists_until: weekday_sunrise" in automation


def test_night_profile_recovers_after_tuya_bulbs_reconnect():
    automation = (REPO_ROOT / "ha-config/automations/sanctuary.yaml").read_text()
    recovery = automation.split("sanctuary_night_light_reconnect_recovery", 1)[1].split(
        "sanctuary_sunday_cleaning", 1
    )[0]
    assert "old.state in ['unknown', 'unavailable']" in recovery
    assert "new.state in ['on', 'off']" in recovery
    assert "['Wind Down', 'Thunderstorm']" in recovery
    assert "mode: restart" in recovery
    assert "script.sanctuary_apply_deep_wind_down" in recovery
    assert "mode: Thunderstorm" in recovery


def test_schedule_commissioning_persists_and_missed_phases_are_recovered():
    configuration = (REPO_ROOT / "ha-config/configuration.yaml").read_text()
    automation = (REPO_ROOT / "ha-config/automations/sanctuary.yaml").read_text()
    enabled_block = configuration.split("sanctuary_automations_enabled:", 1)[1].split(
        "sanctuary_calibration_ready:", 1
    )[0]
    calibrated_block = configuration.split("sanctuary_calibration_ready:", 1)[1].split(
        "sanctuary_manual_hold:", 1
    )[0]

    assert "initial: false" not in enabled_block
    assert "initial: false" not in calibrated_block
    assert "id: sanctuary_schedule_reconciliation" in automation
    assert "event: start" in automation
    assert "to: \"on\"" in automation
    for boundary in ("'06:30'", "'07:20'", "'20:00'", "'21:00'", "'22:00'"):
        assert boundary in automation


def test_context_event_bridge_is_authenticated_and_json_safe():
    configuration = (REPO_ROOT / "ha-config/configuration.yaml").read_text()
    mirror = (REPO_ROOT / "ha-config/automations/context_engine.yaml").read_text()
    rest_block = configuration.split("lifeos_context_event:", 1)[1].split(
        "# Toggles", 1
    )[0]

    assert "Authorization: !secret lifeos_api_authorization" in rest_block
    assert "state | string | tojson" in rest_block
    assert "previous_state | string | tojson" in rest_block
    assert "trigger.event.data.new_state.attributes" not in mirror
    assert "sanctuary_area_id" in mirror
