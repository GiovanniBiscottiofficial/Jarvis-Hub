import importlib.util
from pathlib import Path


def _load_monitor(monkeypatch, token=""):
    monkeypatch.setenv("LIFEOS_API_TOKEN", token)
    path = Path(__file__).resolve().parents[2] / "bootstrap" / "hardware-monitor.py"
    spec = importlib.util.spec_from_file_location("jarvis_hardware_monitor_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_hardware_events_use_lifeos_bearer_token(monkeypatch):
    monitor = _load_monitor(monkeypatch, "local-test-token")
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(monitor.urllib.request, "urlopen", urlopen)
    assert monitor.publish("binary_sensor.x1_microphone", "on", "off", {}) is True
    assert captured["request"].get_header("Authorization") == "Bearer local-test-token"
    assert captured["timeout"] == 8
