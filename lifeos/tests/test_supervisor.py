import importlib.util
from pathlib import Path


def load_supervisor(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[2] / "bootstrap" / "jarvis-supervisor.py"
    spec = importlib.util.spec_from_file_location("jarvis_supervisor_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.STATE_PATH = tmp_path / "state.json"
    module.QUEUE_PATH = tmp_path / "queue.jsonl"
    module.LOCK_PATH = tmp_path / "supervisor.lock"
    module.PAUSE_PATH = tmp_path / "maintenance.pause"
    monkeypatch.setattr(module, "post_event", lambda _event: True)
    return module


def all_healthy(supervisor):
    return {component: (True, "simulated healthy") for component in supervisor.COMPONENTS}


def test_transient_failure_is_observed_without_restart(monkeypatch, tmp_path):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    checks = all_healthy(supervisor)
    checks["lifeos"] = (False, "HTTP timeout")
    actions = []
    result = supervisor.supervise(checks, now=1000, action_runner=lambda action: (actions.append(action) or True, ""))
    lifeos = next(item for item in result["results"] if item["component"] == "lifeos")
    assert lifeos["decision"] == "observing"
    assert actions == []


def test_three_failures_trigger_one_allowlisted_repair(monkeypatch, tmp_path):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    checks = all_healthy(supervisor)
    checks["lifeos"] = (False, "HTTP timeout")
    actions = []
    def runner(action):
        return actions.append(action) or True, "restarted"
    supervisor.supervise(checks, now=1000, action_runner=runner)
    supervisor.supervise(checks, now=1031, action_runner=runner)
    result = supervisor.supervise(checks, now=1062, action_runner=runner)
    lifeos = next(item for item in result["results"] if item["component"] == "lifeos")
    assert lifeos["decision"] == "repair_started"
    assert actions == [["docker", "restart", "lifeos"]]


def test_voice_probe_prefers_continuous_runtime_without_reviving_wyoming(
    monkeypatch, tmp_path
):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    calls = []

    def fake_run(action, timeout=15):
        calls.append(action)
        if action[:2] == ["docker", "inspect"]:
            return True, "healthy"
        return False, "inactive"

    monkeypatch.setattr(supervisor, "run", fake_run)
    healthy, detail = supervisor.probe(("voice_runtime",))

    assert healthy is True
    assert detail == "Linux Voice Assistant healthy"
    assert not any("wyoming-satellite.service" in action for action in calls)
    assert supervisor.repair_command("voice_satellite", ["voice_runtime"]) == [
        "docker", "restart", "linux-voice-assistant"
    ]


def test_voice_repair_falls_back_to_wyoming_only_when_lva_is_absent(
    monkeypatch, tmp_path
):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    monkeypatch.setattr(supervisor, "run", lambda _action: (False, "missing"))

    assert supervisor.repair_command("voice_satellite", ["voice_runtime"]) == [
        "systemctl", "restart", "wyoming-satellite.service"
    ]


def test_network_and_storage_never_auto_repair(monkeypatch, tmp_path):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    checks = all_healthy(supervisor)
    checks["network"] = (False, "no default route")
    checks["storage"] = (False, "97% used")
    actions = []
    for tick in (1000, 1031, 1062, 1093):
        result = supervisor.supervise(checks, now=tick, action_runner=lambda action: (actions.append(action) or True, ""))
    decisions = {item["component"]: item["decision"] for item in result["results"]}
    assert decisions["network"] == "guidance_required"
    assert decisions["storage"] == "guidance_required"
    assert actions == []


def test_repair_storm_enters_quarantine(monkeypatch, tmp_path):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    record = {
        "failures": 3,
        "status": "failed",
        "repair_history": [1000, 1100, 1200],
        "last_repair": 1200,
    }
    decision = supervisor.evaluate_component("piper", False, "port closed", record, 1301)
    assert decision["decision"] == "quarantined"
    assert record["status"] == "quarantined"


def test_simulation_never_runs_repairs_or_persists(monkeypatch, tmp_path):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    checks = all_healthy(supervisor)
    checks["whisper"] = (False, "simulated port closed")
    actions = []
    # Preload a threshold-crossing record; dry-run reads it but cannot mutate disk.
    supervisor.save_state({
        "components": {"whisper": {"failures": 2, "status": "failed", "repair_history": []}},
        "last_heartbeat": 0,
        "version": 1,
    })
    before = supervisor.STATE_PATH.read_text(encoding="utf-8")
    result = supervisor.supervise(checks, dry_run=True, now=1000, action_runner=lambda action: (actions.append(action) or True, ""))
    whisper = next(item for item in result["results"] if item["component"] == "whisper")
    assert whisper["decision"] == "would_repair"
    assert actions == []
    assert supervisor.STATE_PATH.read_text(encoding="utf-8") == before


def test_events_publish_safety_boundaries(monkeypatch, tmp_path):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    record = {"status": "failed", "failures": 3, "repair_history": []}
    result = {"component": "network", "decision": "guidance_required", "detail": "offline", "previous": "healthy"}
    event = supervisor.event_for(result, record, False)
    boundaries = event["attributes"]["protected_boundaries"]
    assert {"network", "internet_power", "locks", "alarms", "configuration", "user_data"} <= set(boundaries)
    assert event["attributes"]["automatic_repair"] is False


def test_maintenance_pause_observes_but_never_repairs(monkeypatch, tmp_path):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    checks = all_healthy(supervisor)
    checks["homeassistant"] = (False, "maintenance restart")
    supervisor.save_state({
        "components": {"homeassistant": {"failures": 2, "status": "failed", "repair_history": []}},
        "last_heartbeat": 0,
        "version": 1,
    })
    actions = []
    result = supervisor.supervise(
        checks,
        repairs_enabled=False,
        now=1000,
        action_runner=lambda action: (actions.append(action) or True, ""),
    )
    homeassistant = next(item for item in result["results"] if item["component"] == "homeassistant")
    assert homeassistant["decision"] == "maintenance_paused"
    assert result["repairs_enabled"] is False
    assert actions == []


def test_heartbeat_republishes_complete_snapshot(monkeypatch, tmp_path):
    supervisor = load_supervisor(monkeypatch, tmp_path)
    checks = all_healthy(supervisor)
    result = supervisor.supervise(checks, dry_run=True, now=1000)
    entities = {event.get("entity_id") for event in result["events"]}
    assert "binary_sensor.jarvis_supervisor" in entities
    for component in supervisor.COMPONENTS:
        assert f"binary_sensor.jarvis_{component}" in entities
