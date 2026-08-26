import importlib.util
from pathlib import Path


def load_audio():
    path = Path(__file__).resolve().parents[2] / "bootstrap" / "jarvis-audio.py"
    spec = importlib.util.spec_from_file_location("jarvis_audio_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_jabra_is_preferred_over_other_usb_audio():
    audio = load_audio()
    names = ["alsa_input.usb-generic", "alsa_input.usb-Jabra_SPEAK_510_USB"]
    assert audio.preferred(names) == "alsa_input.usb-Jabra_SPEAK_510_USB"


def test_wake_word_rejects_room_audio_false_positives():
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")

    assert "${WAKE_WORD_THRESHOLD:-0.70}" in compose
    assert "${WAKE_WORD_TRIGGER_LEVEL:-2}" in compose
    assert "${WAKE_WORD_REFRACTORY_SECONDS:-5}" in compose


def test_status_distinguishes_privacy_mute(monkeypatch):
    audio = load_audio()
    monkeypatch.setattr(audio, "default_endpoint", lambda kind: f"jabra-{kind}")
    monkeypatch.setattr(audio, "volume", lambda kind: (85.0, kind == "source"))
    monkeypatch.setattr(audio, "service_active", lambda _name: True)
    result = audio.status(False)
    assert result["ready"] is False
    assert result["microphone_muted"] is True
    assert result["reason"] == "Microphone privacy mute is on"
    assert result["privacy"] == {"raw_audio_stored": False, "probe_retained": False}


def test_signal_probe_uses_memory_and_reports_level(monkeypatch):
    audio = load_audio()
    payload = (1000).to_bytes(2, "little", signed=True) * 8000
    monkeypatch.setattr(audio, "run", lambda *args, **kwargs: (True, payload))
    result = audio.probe_signal(0.5)
    assert result["tested"] is True
    assert result["signal"] == "detected"
    assert -31 < result["dbfs"] < -29


def test_status_rejects_digital_silence(monkeypatch):
    audio = load_audio()
    monkeypatch.setattr(audio, "default_endpoint", lambda kind: f"jabra-{kind}")
    monkeypatch.setattr(audio, "volume", lambda _kind: (100.0, False))
    monkeypatch.setattr(audio, "service_active", lambda _name: True)
    monkeypatch.setattr(
        audio,
        "probe_signal",
        lambda: {"tested": True, "signal": "quiet", "dbfs": -96.0},
    )
    result = audio.status(True)
    assert result["ready"] is False
    assert result["reason"] == "No microphone signal detected; check the Jabra mute button"


def test_selector_changes_only_nonpreferred_defaults(monkeypatch):
    audio = load_audio()
    monkeypatch.setattr(
        audio,
        "endpoints",
        lambda kind: [f"generic-{kind}", f"Jabra-SPEAK-510-{kind}"],
    )
    monkeypatch.setattr(audio, "default_endpoint", lambda kind: f"generic-{kind}")
    monkeypatch.setattr(audio, "status", lambda _probe: {"ready": True})
    calls = []
    monkeypatch.setattr(audio, "run", lambda *args, **kwargs: (calls.append(args) or True, ""))
    result = audio.select_endpoints()
    assert result["source_changed"] is True
    assert result["sink_changed"] is True
    assert ("pactl", "set-default-source", "Jabra-SPEAK-510-source") in calls
    assert ("pactl", "set-default-sink", "Jabra-SPEAK-510-sink") in calls
