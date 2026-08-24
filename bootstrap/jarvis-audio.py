#!/usr/bin/env python3
"""Manage and diagnose the X1 voice endpoints without retaining audio.

The command intentionally keeps capture probes in memory. No waveform, transcript,
or recording is written to disk.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import subprocess
import sys
from typing import Any

JABRA_MARKERS = ("jabra", "phs002w", "gn_audio", "speak_510")


def run(*args: str, timeout: int = 8, binary: bool = False) -> tuple[bool, Any]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=not binary,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0, result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, b"" if binary else ""


def endpoints(kind: str) -> list[str]:
    noun = "sources" if kind == "source" else "sinks"
    ok, output = run("pactl", "list", "short", noun)
    if not ok:
        return []
    names = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or (kind == "source" and fields[1].endswith(".monitor")):
            continue
        names.append(fields[1])
    return names


def preferred(names: list[str]) -> str | None:
    for name in names:
        if any(marker in name.lower() for marker in JABRA_MARKERS):
            return name
    for name in names:
        if "usb" in name.lower():
            return name
    return names[0] if names else None


def default_endpoint(kind: str) -> str:
    selector = "get-default-source" if kind == "source" else "get-default-sink"
    ok, value = run("pactl", selector)
    return value.strip() if ok else ""


def volume(kind: str) -> tuple[float | None, bool | None]:
    node = "@DEFAULT_AUDIO_SOURCE@" if kind == "source" else "@DEFAULT_AUDIO_SINK@"
    ok, output = run("wpctl", "get-volume", node)
    if not ok:
        return None, None
    match = re.search(r"Volume:\s*([0-9.]+)", output)
    return (round(float(match.group(1)) * 100, 1) if match else None, "[MUTED]" in output)


def service_active(name: str) -> bool:
    ok, output = run("systemctl", "is-active", name)
    return ok and output.strip() == "active"


def probe_signal(seconds: float = 0.75) -> dict[str, Any]:
    frames = max(4000, int(16000 * min(max(seconds, 0.25), 2.0)))
    ok, payload = run(
        "arecord", "-q", "-D", "default", "-r", "16000", "-c", "1",
        "-f", "S16_LE", "-t", "raw", "--samples", str(frames),
        timeout=5,
        binary=True,
    )
    if not ok or not payload:
        return {"tested": True, "signal": "unavailable", "dbfs": None}
    count = len(payload) // 2
    if not count:
        return {"tested": True, "signal": "unavailable", "dbfs": None}
    samples = struct.unpack(f"<{count}h", payload[: count * 2])
    rms = math.sqrt(sum(sample * sample for sample in samples) / count)
    dbfs = -96.0 if rms == 0 else max(-96.0, 20 * math.log10(rms / 32768.0))
    return {
        "tested": True,
        "signal": "detected" if dbfs > -60 else "quiet",
        "dbfs": round(dbfs, 1),
    }


def select_endpoints() -> dict[str, Any]:
    chosen_source = preferred(endpoints("source"))
    chosen_sink = preferred(endpoints("sink"))
    previous_source = default_endpoint("source")
    previous_sink = default_endpoint("sink")
    if chosen_source and chosen_source != previous_source:
        run("pactl", "set-default-source", chosen_source)
    if chosen_sink and chosen_sink != previous_sink:
        run("pactl", "set-default-sink", chosen_sink)
    return status(False) | {
        "source_changed": bool(chosen_source and chosen_source != previous_source),
        "sink_changed": bool(chosen_sink and chosen_sink != previous_sink),
    }


def set_mute(muted: bool) -> bool:
    ok, _ = run(
        "wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "1" if muted else "0"
    )
    return ok


def status(probe: bool) -> dict[str, Any]:
    source = default_endpoint("source")
    sink = default_endpoint("sink")
    source_volume, source_muted = volume("source")
    sink_volume, sink_muted = volume("sink")
    pipewire = bool(source or sink)
    satellite = service_active("wyoming-satellite.service")
    source_present = bool(source and "null" not in source.lower())
    sink_present = bool(sink and "null" not in sink.lower())
    data: dict[str, Any] = {
        "ready": source_present and sink_present and not bool(source_muted) and satellite,
        "pipewire": "online" if pipewire else "unavailable",
        "source": source or "none",
        "sink": sink or "none",
        "endpoint": "Jabra Speak 510" if any(m in source.lower() for m in JABRA_MARKERS) else ("USB audio" if "usb" in source.lower() else "default audio"),
        "source_present": source_present,
        "sink_present": sink_present,
        "microphone_muted": source_muted,
        "microphone_volume_percent": source_volume,
        "speaker_muted": sink_muted,
        "speaker_volume_percent": sink_volume,
        "satellite": "online" if satellite else "unavailable",
        "wake_word": "hey_jarvis",
        "sample_rate_hz": 16000,
        "channels": 1,
        "privacy": {"raw_audio_stored": False, "probe_retained": False},
        "signal": {"tested": False, "signal": "not_tested", "dbfs": None},
    }
    if probe and source_present and not source_muted:
        data["signal"] = probe_signal()
    if not source_present:
        data["reason"] = "No usable microphone endpoint"
    elif source_muted:
        data["reason"] = "Microphone privacy mute is on"
    elif not satellite:
        data["reason"] = "Wyoming voice satellite is offline"
    elif not sink_present:
        data["reason"] = "No usable response speaker"
    else:
        data["reason"] = "Listening locally for Hey Jarvis"
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis local voice audio manager")
    sub = parser.add_subparsers(dest="command", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--probe", action="store_true")
    sub.add_parser("select")
    sub.add_parser("mute")
    sub.add_parser("unmute")
    args = parser.parse_args()
    if args.command == "select":
        result = select_endpoints()
    elif args.command in {"mute", "unmute"}:
        if not set_mute(args.command == "mute"):
            return 1
        result = status(False)
    else:
        result = status(args.probe)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
