#!/usr/bin/env python3
"""Publish X1 hardware health into the LifeOS context event stream.

Runs on the host so it can inspect PipeWire, BlueZ, V4L2, input devices,
power, and thermals without granting those interfaces to another container.
Only state changes are published, keeping the context timeline useful.
"""
import glob
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

EVENTS_URL = os.environ.get("LIFEOS_EVENTS_URL", "http://127.0.0.1:8090/api/events")
INTERVAL = max(10, int(os.environ.get("JARVIS_HARDWARE_INTERVAL", "30")))
HEARTBEAT_INTERVAL = max(
    60, int(os.environ.get("JARVIS_HARDWARE_HEARTBEAT_INTERVAL", "300"))
)


def command(*args: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=8, check=False
        )
        return result.returncode == 0, result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""


def read_first(pattern: str, default: str = "unknown") -> str:
    paths = glob.glob(pattern)
    if not paths:
        return default
    try:
        return Path(paths[0]).read_text(encoding="utf-8").strip()
    except OSError:
        return default


def audio_state(kind: str) -> tuple[str, dict]:
    selector = "get-default-source" if kind == "microphone" else "get-default-sink"
    ok, device = command("pactl", selector)
    usable = ok and bool(device) and "null" not in device.lower()
    normalized = device.lower()
    jabra = any(marker in normalized for marker in ("jabra", "phs002w", "gn_audio"))
    return (
        "on" if usable else "unavailable",
        {
            "device": device or "none",
            "endpoint": "Jabra PHS002W" if jabra else ("default audio" if usable else "none"),
            "jabra_commissioned": jabra,
        },
    )


def external_storage_state() -> tuple[str, dict]:
    """Report removable/data mounts without moving or deleting any files."""
    mounts = []
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        device, mountpoint, filesystem = fields[:3]
        if not mountpoint.startswith(("/media/", "/mnt/", "/run/media/")):
            continue
        try:
            stats = os.statvfs(mountpoint)
            mounts.append(
                {
                    "device": device,
                    "mountpoint": mountpoint,
                    "filesystem": filesystem,
                    "total_gb": round(stats.f_frsize * stats.f_blocks / 1073741824, 1),
                    "free_gb": round(stats.f_frsize * stats.f_bavail / 1073741824, 1),
                }
            )
        except OSError:
            continue
    return ("on" if mounts else "unavailable", {"mounts": mounts, "count": len(mounts)})


def camera_state() -> tuple[str, dict]:
    devices = []
    for path in sorted(glob.glob("/dev/video*")):
        ok, _ = command("v4l2-ctl", "-d", path, "--get-fmt-video")
        if not ok:
            continue
        name = read_first(
            f"/sys/class/video4linux/{Path(path).name}/name", Path(path).name
        )
        devices.append({"path": path, "name": name})
    return ("on" if devices else "unavailable", {"capture_devices": devices})


def bluetooth_state() -> tuple[str, dict]:
    powered_ok, powered = command("bluetoothctl", "show")
    powered_on = powered_ok and "Powered: yes" in powered
    _, connected = command("bluetoothctl", "devices", "Connected")
    devices = [line.partition(" ")[2] for line in connected.splitlines() if line]
    return (
        "on" if powered_on else "unavailable",
        {"connected_count": len(devices), "connected_devices": devices},
    )


def touchscreen_state() -> tuple[str, dict]:
    try:
        devices = Path("/proc/bus/input/devices").read_text(
            encoding="utf-8", errors="ignore"
        )
    except OSError:
        devices = ""
    present = "touchscreen" in devices.lower() or "wacom" in devices.lower()
    return ("on" if present else "unavailable", {"detected": present})


def snapshot() -> dict[str, tuple[str, dict]]:
    battery = read_first("/sys/class/power_supply/BAT*/capacity")
    mains = read_first("/sys/class/power_supply/A*/online")
    temperatures = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            temperatures.append(float(Path(path).read_text().strip()) / 1000)
        except (OSError, ValueError):
            continue
    return {
        "sensor.x1_battery": (battery, {"unit": "%", "mains_online": mains}),
        "binary_sensor.x1_mains_power": (
            "on" if mains == "1" else "off",
            {"battery_percent": battery},
        ),
        "sensor.x1_cpu_temperature": (
            f"{max(temperatures):.1f}" if temperatures else "unknown",
            {"unit": "°C"},
        ),
        "binary_sensor.x1_microphone": audio_state("microphone"),
        "binary_sensor.x1_speakers": audio_state("speakers"),
        "binary_sensor.x1_camera": camera_state(),
        "binary_sensor.x1_bluetooth": bluetooth_state(),
        "binary_sensor.x1_touchscreen": touchscreen_state(),
        "binary_sensor.x1_external_storage": external_storage_state(),
    }


def publish(entity_id: str, state: str, previous: str | None, attributes: dict) -> bool:
    body = json.dumps(
        {
            "source": "x1_hardware",
            "event_type": "hardware_state_changed",
            "entity_id": entity_id,
            "state": state,
            "previous_state": previous,
            "attributes": attributes,
        }
    ).encode()
    request = urllib.request.Request(
        EVENTS_URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError):
        return False


def main() -> None:
    previous: dict[str, tuple[str, dict]] = {}
    last_heartbeat = 0.0
    while True:
        current = snapshot()
        for entity_id, (state, attributes) in current.items():
            old = previous.get(entity_id)
            if old != (state, attributes):
                if publish(entity_id, state, old[0] if old else None, attributes):
                    previous[entity_id] = (state, attributes)
        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            if publish(
                "binary_sensor.x1_hardware_monitor",
                "on",
                "on" if last_heartbeat else None,
                {"heartbeat_interval_seconds": HEARTBEAT_INTERVAL},
            ):
                last_heartbeat = now
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
