"""Ephemeral Linux Voice Assistant conversation telemetry.

The bridge retains only the latest STT/TTS exchange in process memory so the X1
can show Giovanni what Jarvis heard. It never stores audio or transcripts in the
LifeOS database, logs, browser storage, or context event history.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import json
import os
from threading import Lock
from typing import Any

import websockets

LVA_PERIPHERAL_URI = os.environ.get(
    "LVA_PERIPHERAL_URI", "ws://host.docker.internal:6055"
)
MAX_TRANSCRIPT_CHARS = 600

_lock = Lock()
_state: dict[str, Any] = {
    "connected": False,
    "phase": "unavailable",
    "last_user": "",
    "last_assistant": "",
    "user_at": None,
    "assistant_at": None,
    "updated_at": None,
    "error": "Waiting for the local voice runtime.",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_TRANSCRIPT_CHARS]


def apply_voice_event(message: dict[str, Any]) -> None:
    """Apply one documented LVA peripheral event to the memory-only view."""
    event = str(message.get("event") or "")
    data = message.get("data") if isinstance(message.get("data"), dict) else {}
    now = _now()
    with _lock:
        _state["updated_at"] = now
        if event == "snapshot":
            _state["connected"] = bool(data.get("ha_connected"))
            _state["phase"] = "idle" if _state["connected"] else "connecting"
            user = _clean_text(data.get("last_stt_text"))
            assistant = _clean_text(data.get("last_tts_text"))
            if user:
                _state["last_user"] = user
                _state["user_at"] = now
            if assistant:
                _state["last_assistant"] = assistant
                _state["assistant_at"] = now
            _state["error"] = None
        elif event == "zeroconf":
            connected = data.get("status") == "connected"
            _state["connected"] = connected
            _state["phase"] = "idle" if connected else "connecting"
            _state["error"] = None if connected else "Connecting to Home Assistant."
        elif event == "stt_text":
            _state["last_user"] = _clean_text(data.get("text"))
            _state["user_at"] = now
            _state["phase"] = "thinking"
            _state["error"] = None
        elif event == "tts_text":
            _state["last_assistant"] = _clean_text(data.get("text"))
            _state["assistant_at"] = now
            _state["phase"] = "speaking"
            _state["error"] = None
        elif event in {
            "wake_word_detected", "listening", "thinking", "tts_speaking", "idle"
        }:
            _state["phase"] = event
            _state["error"] = None
        elif event == "tts_finished":
            _state["phase"] = "idle"
        elif event == "pipeline_error":
            _state["phase"] = "error"
            _state["error"] = _clean_text(data.get("reason")) or "Voice pipeline error."
        elif event == "disconnected":
            _state["connected"] = False
            _state["phase"] = "disconnected"
            _state["error"] = "The voice runtime lost Home Assistant."


def conversation_status() -> dict[str, Any]:
    with _lock:
        snapshot = dict(_state)
    snapshot["privacy"] = {
        "raw_audio_stored": False,
        "transcript_persisted": False,
        "retention": "latest exchange in memory until LifeOS restarts",
    }
    return snapshot


async def watch_voice_events() -> None:
    """Reconnect forever without affecting the voice runtime when unavailable."""
    while True:
        try:
            async with websockets.connect(
                LVA_PERIPHERAL_URI, open_timeout=5, ping_interval=20, ping_timeout=10
            ) as socket:
                with _lock:
                    _state["connected"] = True
                    _state["phase"] = "connecting"
                    _state["error"] = None
                    _state["updated_at"] = _now()
                async for raw in socket:
                    try:
                        message = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(message, dict):
                        apply_voice_event(message)
        except asyncio.CancelledError:
            raise
        except (OSError, websockets.WebSocketException, TimeoutError) as exc:
            with _lock:
                _state["connected"] = False
                _state["phase"] = "unavailable"
                _state["error"] = f"Voice telemetry unavailable: {type(exc).__name__}."
                _state["updated_at"] = _now()
            await asyncio.sleep(3)


async def stop_voice_events(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
