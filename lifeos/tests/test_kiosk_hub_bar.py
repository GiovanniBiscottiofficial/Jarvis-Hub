import importlib.util
import io
from pathlib import Path
from unittest.mock import Mock

import pytest


REPO = Path(__file__).resolve().parents[2]
BAR_PATH = REPO / "bootstrap" / "kiosk" / "hub-bar.py"


def load_bar():
    spec = importlib.util.spec_from_file_location("jarvis_hub_bar", BAR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_both_installers_use_the_canonical_hub_bar():
    for name in ("setup-kiosk.sh", "revert-kiosk-to-x11.sh"):
        script = (REPO / "bootstrap" / name).read_text(encoding="utf-8")
        assert 'install -m 0755 "${SCRIPT_DIR}/kiosk/hub-bar.py"' in script
        assert "--incognito" not in script
        assert "--user-data-dir" not in script


def test_home_opens_dashboard_and_closes_every_prior_page():
    bar = load_bar()
    calls = []

    def request(path, method="GET"):
        calls.append((path, method))
        if path == "/json/list":
            return [{"type": "page", "id": "retailer"}, {"type": "page", "id": "extra"}]
        if path.startswith("/json/new?"):
            return {"type": "page", "id": "home"}
        return None

    assert bar.open_only(bar.DASH_URL, request=request, process_factory=Mock()) is True
    assert any(path.startswith("/json/new?") and method == "PUT" for path, method in calls)
    assert ("/json/close/retailer", "GET") in calls
    assert ("/json/close/extra", "GET") in calls


def test_home_settings_and_back_callbacks_dispatch_expected_destinations():
    bar = load_bar()
    navigate = Mock(return_value=True)
    key_sender = Mock(return_value=True)

    assert bar.go_home(navigate) is True
    assert bar.go_settings(navigate) is True
    assert bar.go_back(key_sender) is True
    assert navigate.call_args_list[0].args == (bar.DASH_URL,)
    assert navigate.call_args_list[1].args == (bar.SETTINGS_URL,)
    key_sender.assert_called_once_with("alt+Left")


def test_back_targets_the_visible_chromium_window():
    bar = load_bar()
    process = Mock()
    assert bar.chromium_key(
        "alt+Left",
        check_output=Mock(return_value="101\n202\n"),
        process_factory=process,
    ) is True
    process.assert_called_once_with(["xdotool", "key", "--window", "202", "alt+Left"])


def test_keyboard_uses_running_themed_keyboard_then_onboard_fallback():
    bar = load_bar()
    kill = Mock()
    process = Mock()
    assert bar.toggle_keyboard(
        open_file=lambda *_args, **_kwargs: io.StringIO("81"),
        kill=kill,
        process_factory=process,
    ) == "themed"
    kill.assert_called_once_with(81, bar.TOGGLE_SIGNAL)
    process.assert_not_called()

    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    assert bar.toggle_keyboard(open_file=missing, process_factory=process) == "onboard"
    assert process.call_args.args[0][0] == "dbus-send"


def test_keyboard_can_start_themed_fallback_when_dbus_is_unavailable():
    bar = load_bar()
    calls = []

    def process(command):
        calls.append(command)
        if command[0] == "dbus-send":
            raise OSError("no session bus")

    assert bar.toggle_keyboard(
        open_file=Mock(side_effect=FileNotFoundError),
        process_factory=process,
        exists=lambda path: path == "/opt/jarvis-kiosk/jarvis-keyboard.py",
    ) == "themed-started"
    assert calls[-1] == ["/opt/jarvis-kiosk/jarvis-keyboard.py"]


def test_eyes_preserves_guarded_gesture_app_telemetry():
    bar = load_bar()
    now = 100.0
    base = {"updated_at": now, "camera_available": True, "gesture_timestamp": now}
    assert bar.perception_display({**base, "last_gesture": "play_pause", "app": "youtube"}, now) == (
        bar.GREEN,
        "PLAY/PAUSE · YOUTUBE",
    )
    assert bar.perception_display({**base, "last_gesture": "seek_forward", "app": "plex"}, now)[1] == "SEEK +10 · PLEX"
    assert bar.perception_display({**base, "last_gesture": "volume_up", "app": "spotify"}, now)[1] == "VOLUME + · SPOTIFY"
    assert bar.perception_display({**base, "last_gesture": "launch", "app": "youtube"}, now)[1] == "EYES ONLINE"
    assert bar.perception_display({**base, "last_gesture": "mute", "app": "unknown"}, now)[1] == "EYES ONLINE"
    assert bar.perception_display({**base, "gesture_timestamp": True, "last_gesture": "mute", "app": "browser"}, now)[1] == "EYES ONLINE"
    with pytest.raises(ValueError):
        bar.perception_display({"updated_at": True, "camera_available": True}, now)


class FakeWidget:
    created = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.created.append(self)

    def pack(self, *args, **kwargs):
        self.pack_args = (args, kwargs)

    def pack_propagate(self, *_args):
        pass

    def configure(self, **_kwargs):
        pass

    def itemconfigure(self, *_args, **_kwargs):
        pass

    def create_oval(self, *_args, **_kwargs):
        pass

    def create_line(self, *_args, **_kwargs):
        pass


class FakeRoot(FakeWidget):
    def overrideredirect(self, *_args):
        pass

    def attributes(self, *_args):
        pass

    def after(self, *_args):
        pass

    def update_idletasks(self):
        pass

    def winfo_screenheight(self):
        return 1000

    def winfo_reqheight(self):
        return 54

    def geometry(self, *_args):
        pass

    def lift(self):
        pass


class FakeTk:
    Tk = FakeRoot
    Frame = type("Frame", (FakeWidget,), {})
    Button = type("Button", (FakeWidget,), {})
    Canvas = type("Canvas", (FakeWidget,), {})
    Label = type("Label", (FakeWidget,), {})


def test_every_constructed_escape_control_has_explicit_44px_minimum():
    bar = load_bar()
    FakeWidget.created = []
    bar.build_bar(FakeTk)
    control_frames = [
        widget for widget in FakeWidget.created
        if widget.kwargs.get("width") == bar.CONTROL_MIN_WIDTH
        and widget.kwargs.get("height") == bar.CONTROL_MIN_HEIGHT
    ]
    assert bar.CONTROL_MIN_HEIGHT >= 44
    assert len(control_frames) == 4
    labels = [widget.kwargs.get("text") for widget in FakeWidget.created if "text" in widget.kwargs]
    assert {"HOME", "BACK", "KEYS", "SETTINGS"}.issubset(labels)


def test_hub_bar_only_navigates_to_local_home_and_settings():
    bar = BAR_PATH.read_text(encoding="utf-8")
    assert "http://localhost:8123/jarvis-hub/wall-plus?kiosk" in bar
    assert 'SETTINGS_URL = "http://localhost:8123/config"' in bar
    assert all(host not in bar for host in ("foodlion.com", "instacart.com", "walmart.com", "amazon.com"))
