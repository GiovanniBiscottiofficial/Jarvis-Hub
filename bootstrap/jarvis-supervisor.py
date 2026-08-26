#!/usr/bin/env python3
"""Conservative self-healing supervisor for the Jarvis X1.

Only explicitly allow-listed, reversible service restarts are automatic. Network,
power, security, configuration, and user data are observation-only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl  # Linux deployment; absent during Windows-hosted unit tests.
except ImportError:  # pragma: no cover - exercised only by the Windows test host
    fcntl = None

STATE_PATH = Path(os.environ.get("JARVIS_SUPERVISOR_STATE", "/var/lib/jarvis-supervisor/state.json"))
QUEUE_PATH = STATE_PATH.with_name("event-queue.jsonl")
LOCK_PATH = STATE_PATH.with_name("supervisor.lock")
PAUSE_PATH = STATE_PATH.with_name("maintenance.pause")
EVENTS_URL = os.environ.get("LIFEOS_EVENTS_URL", "http://127.0.0.1:8090/api/events")
API_TOKEN = os.environ.get("LIFEOS_API_TOKEN", "").strip()
FAILURE_THRESHOLD = max(2, int(os.environ.get("JARVIS_SUPERVISOR_FAILURE_THRESHOLD", "3")))
COOLDOWN_SECONDS = max(60, int(os.environ.get("JARVIS_SUPERVISOR_COOLDOWN", "300")))
MAX_REPAIRS_PER_HOUR = max(1, int(os.environ.get("JARVIS_SUPERVISOR_MAX_REPAIRS", "3")))
HEARTBEAT_SECONDS = max(60, int(os.environ.get("JARVIS_SUPERVISOR_HEARTBEAT", "300")))

COMPONENTS: dict[str, dict[str, Any]] = {
    "lifeos": {"probe": ("http", "http://127.0.0.1:8090/healthz"), "repair": ["docker", "restart", "lifeos"], "label": "LifeOS intelligence"},
    "homeassistant": {"probe": ("http", "http://127.0.0.1:8123/"), "repair": ["docker", "restart", "homeassistant"], "label": "Home Assistant control plane"},
    "openwakeword": {"probe": ("tcp", "127.0.0.1", 10400), "repair": ["docker", "restart", "openwakeword"], "label": "Hey Jarvis wake word"},
    "whisper": {"probe": ("tcp", "127.0.0.1", 10300), "repair": ["docker", "restart", "whisper"], "label": "Speech recognition"},
    "piper": {"probe": ("tcp", "127.0.0.1", 10200), "repair": ["docker", "restart", "piper"], "label": "Jarvis voice synthesis"},
    "voice_satellite": {"probe": ("systemd", "wyoming-satellite.service"), "repair": ["systemctl", "restart", "wyoming-satellite.service"], "label": "X1 voice satellite"},
    "hardware_monitor": {"probe": ("systemd", "jarvis-hardware-monitor.service"), "repair": ["systemctl", "restart", "jarvis-hardware-monitor.service"], "label": "X1 hardware telemetry"},
    "camera_stream": {"probe": ("http", "http://127.0.0.1:1984/"), "repair": ["systemctl", "restart", "go2rtc.service"], "label": "Local camera stream"},
    "gestures": {"probe": ("container", "gestures"), "repair": ["docker", "restart", "gestures"], "label": "Gesture perception"},
    "kiosk": {"probe": ("process", "hub", "chromium.*--kiosk"), "repair": ["systemctl", "restart", "getty@tty1.service"], "label": "X1 command surface"},
    "network": {"probe": ("route",), "repair": None, "label": "Network route", "guidance": "Check Wi-Fi or Tailscale; automatic network changes are prohibited."},
    "storage": {"probe": ("disk", "/", 95), "repair": None, "label": "System storage", "guidance": "Free storage manually; the supervisor never deletes files."},
}


def run(args: list[str], timeout: int = 15) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode == 0, (result.stdout or result.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__


def probe(spec: tuple[Any, ...]) -> tuple[bool, str]:
    kind = spec[0]
    try:
        if kind == "http":
            request = urllib.request.Request(str(spec[1]), headers={"User-Agent": "Jarvis-Supervisor/1"})
            try:
                with urllib.request.urlopen(request, timeout=4) as response:
                    return response.status < 500, f"HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                return exc.code < 500, f"HTTP {exc.code}"
        if kind == "tcp":
            with socket.create_connection((str(spec[1]), int(spec[2])), timeout=3):
                return True, f"TCP {spec[2]} accepting"
        if kind == "systemd":
            ok, output = run(["systemctl", "is-active", str(spec[1])])
            return ok and output == "active", output or "inactive"
        if kind == "container":
            ok, output = run(["docker", "inspect", "-f", "{{.State.Running}}", str(spec[1])])
            return ok and output == "true", output or "not running"
        if kind == "process":
            ok, output = run(["pgrep", "-u", str(spec[1]), "-f", str(spec[2])])
            return ok and bool(output), "process present" if ok else "process missing"
        if kind == "route":
            ok, output = run(["ip", "route", "show", "default"])
            return ok and bool(output), "default route present" if output else "no default route"
        if kind == "disk":
            usage = shutil.disk_usage(str(spec[1]))
            percent = round(usage.used * 100 / usage.total, 1)
            return percent < float(spec[2]), f"{percent}% used"
    except (OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return False, "unknown probe"


def empty_state() -> dict[str, Any]:
    return {"components": {}, "last_heartbeat": 0.0, "version": 1}


def load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else empty_state()
    except (OSError, json.JSONDecodeError):
        return empty_state()


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, STATE_PATH)


def evaluate_component(component: str, healthy: bool, detail: str, record: dict[str, Any], now: float) -> dict[str, Any]:
    config = COMPONENTS[component]
    previous = record.get("status", "unknown")
    history = [float(value) for value in record.get("repair_history", []) if now - float(value) < 3600]
    record.update({"checked_at": now, "detail": detail, "repair_history": history})
    decision = "healthy"
    if healthy:
        record["failures"] = 0
        record["status"] = "healthy"
        decision = "recovered" if previous in {"failed", "repairing", "quarantined"} else "healthy"
    else:
        record["failures"] = int(record.get("failures", 0)) + 1
        record["status"] = "failed"
        if record["failures"] < FAILURE_THRESHOLD:
            decision = "observing"
        elif not config.get("repair"):
            decision = "guidance_required"
        elif len(history) >= MAX_REPAIRS_PER_HOUR:
            record["status"] = "quarantined"
            decision = "quarantined"
        elif now - float(record.get("last_repair", 0)) < COOLDOWN_SECONDS:
            decision = "cooldown"
        else:
            decision = "repair"
    return {"component": component, "healthy": healthy, "detail": detail, "decision": decision, "previous": previous}


def event_for(result: dict[str, Any], record: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    component = result["component"]
    config = COMPONENTS[component]
    return {
        "source": "x1_supervisor",
        "event_type": "supervisor.decision",
        "entity_id": f"binary_sensor.jarvis_{component}",
        "state": record.get("status", "unknown"),
        "previous_state": result.get("previous"),
        "attributes": {
            "component": component,
            "label": config["label"],
            "decision": result["decision"],
            "detail": result["detail"],
            "failure_count": record.get("failures", 0),
            "repair_count_last_hour": len(record.get("repair_history", [])),
            "automatic_repair": bool(config.get("repair")),
            "dry_run": dry_run,
            "guidance": config.get("guidance"),
            "protected_boundaries": ["network", "internet_power", "locks", "alarms", "configuration", "user_data"],
        },
    }


def post_event(event: dict[str, Any]) -> bool:
    headers = {"Content-Type": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    request = urllib.request.Request(EVENTS_URL, data=json.dumps(event).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status < 300
    except (urllib.error.URLError, TimeoutError):
        return False


def queue_event(event: dict[str, Any]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, separators=(",", ":")) + "\n")


def flush_events(events: list[dict[str, Any]]) -> None:
    pending: list[dict[str, Any]] = []
    if QUEUE_PATH.exists():
        for line in QUEUE_PATH.read_text(encoding="utf-8").splitlines()[-200:]:
            try:
                pending.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    pending.extend(events)
    unsent = []
    for event in pending:
        if not post_event(event):
            unsent.append(event)
    if unsent:
        temporary = QUEUE_PATH.with_suffix(".tmp")
        temporary.write_text("".join(json.dumps(item) + "\n" for item in unsent[-200:]), encoding="utf-8")
        os.replace(temporary, QUEUE_PATH)
    else:
        QUEUE_PATH.unlink(missing_ok=True)


def supervise(checks: dict[str, tuple[bool, str]] | None = None, *, dry_run: bool = False, repairs_enabled: bool | None = None, now: float | None = None, action_runner: Callable[[list[str]], tuple[bool, str]] = run) -> dict[str, Any]:
    timestamp = now if now is not None else time.time()
    if repairs_enabled is None:
        repairs_enabled = not PAUSE_PATH.exists()
    state = load_state()
    records = state.setdefault("components", {})
    results = []
    events = []
    for component, config in COMPONENTS.items():
        healthy, detail = checks[component] if checks and component in checks else probe(config["probe"])
        record = records.setdefault(component, {})
        result = evaluate_component(component, healthy, detail, record, timestamp)
        if result["decision"] == "repair":
            if dry_run:
                result["decision"] = "would_repair"
            elif not repairs_enabled:
                result["decision"] = "maintenance_paused"
            else:
                ok, output = action_runner(list(config["repair"]))
                record["last_repair"] = timestamp
                record.setdefault("repair_history", []).append(timestamp)
                record["status"] = "repairing" if ok else "failed"
                result["decision"] = "repair_started" if ok else "repair_failed"
                result["repair_output"] = output[-300:]
        changed = result["previous"] != record.get("status")
        notable = result["decision"] not in {"healthy", "observing", "cooldown"}
        if changed or notable:
            events.append(event_for(result, record, dry_run))
        results.append(result)
    heartbeat_due = timestamp - float(state.get("last_heartbeat", 0)) >= HEARTBEAT_SECONDS
    if heartbeat_due:
        state["last_heartbeat"] = timestamp
        events.append({
            "source": "x1_supervisor", "event_type": "supervisor.heartbeat",
            "entity_id": "binary_sensor.jarvis_supervisor", "state": "on",
            "attributes": {
                "healthy": sum(item["healthy"] for item in results),
                "total": len(results),
                "automatic_repairs_enabled": bool(repairs_enabled and not dry_run),
                "protected_boundaries": ["network", "internet_power", "locks", "alarms", "configuration", "user_data"],
            },
        })
    if not dry_run:
        save_state(state)
        flush_events(events)
    return {"dry_run": dry_run, "repairs_enabled": bool(repairs_enabled), "results": results, "events": events, "state": state}


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis self-healing supervisor")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate but never repair or persist")
    parser.add_argument("--simulate", type=Path, help="Read component health from a JSON fixture")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pause", action="store_true", help="Pause automatic repairs for maintenance")
    parser.add_argument("--resume", action="store_true", help="Resume automatic repairs")
    args = parser.parse_args()
    if args.pause and args.resume:
        parser.error("--pause and --resume are mutually exclusive")
    if args.pause:
        PAUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PAUSE_PATH.write_text("Automatic repairs paused for maintenance.\n", encoding="utf-8")
        print("Jarvis automatic repairs paused; health observation remains active.")
        return 0
    if args.resume:
        PAUSE_PATH.unlink(missing_ok=True)
        print("Jarvis automatic repairs resumed.")
        return 0
    checks = None
    if args.simulate:
        fixture = json.loads(args.simulate.read_text(encoding="utf-8"))
        checks = {key: (bool(value.get("healthy")), str(value.get("detail", "simulated"))) for key, value in fixture.items()}
        args.dry_run = True
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock:
        if fcntl is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
        result = supervise(checks, dry_run=args.dry_run)
    if args.json or args.dry_run:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
