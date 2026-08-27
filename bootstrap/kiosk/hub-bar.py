#!/usr/bin/env python3
"""Always-on-top escape controls for the Jarvis X11 kiosk.

This module is import-safe so the guaranteed escape behavior can be tested
without opening a Tk window. Run it directly to construct the kiosk bar.
"""
import json
import os
import signal
import subprocess
import time
import tkinter as tk
import urllib.request
from urllib.parse import quote

DEVTOOLS = "http://127.0.0.1:9222"
DASH_URL = os.environ.get(
    "JARVIS_DASH_URL",
    "http://localhost:8123/jarvis-hub/wall-plus?kiosk",
)
SETTINGS_URL = "http://localhost:8123/config"
STATUS_PATH = "/run/jarvis/perception.json"
STATUS_STALE_S = 45
GESTURE_FEEDBACK_S = 3
TOGGLE_SIGNAL = getattr(signal, "SIGUSR1", 10)
CONTROL_MIN_WIDTH = 78
CONTROL_MIN_HEIGHT = 54
BG = "#07111d"
PANEL = "#0b1b2a"
CYAN = "#78eaff"
GREEN = "#58f58a"
GRAY = "#80909f"
AMBER = "#ffbd59"
KNOWN_APPS = frozenset({"youtube", "spotify", "plex", "browser"})
GESTURE_ACTIONS = {
    "up": "UP",
    "down": "DOWN",
    "forward": "NEXT",
    "back": "BACK",
    "play_pause": "PLAY/PAUSE",
    "mute": "MUTE",
    "seek_forward": "SEEK +10",
    "seek_back": "SEEK -10",
    "volume_up": "VOLUME +",
    "volume_down": "VOLUME -",
}


def devtools(path, method="GET"):
    request = urllib.request.Request(DEVTOOLS + path, method=method)
    with urllib.request.urlopen(request, timeout=3) as response:
        body = response.read()
    return json.loads(body) if body.strip() else None


def open_only(url, request=devtools, process_factory=subprocess.Popen):
    """Open a trusted local destination and close every prior Chromium page."""
    try:
        pages = [page for page in request("/json/list") if page.get("type") == "page"]
        encoded = quote(url, safe="")
        try:
            new_page = request("/json/new?" + encoded, "PUT")
        except Exception:
            new_page = request("/json/new?" + encoded)
        new_id = (new_page or {}).get("id")
        for page in pages:
            if page.get("id") and page["id"] != new_id:
                try:
                    request("/json/close/" + page["id"])
                except Exception:
                    pass
        return True
    except Exception:
        if url == DASH_URL:
            process_factory(["pkill", "-f", "chromium"])
        return False


def chromium_key(*keys, check_output=subprocess.check_output, process_factory=subprocess.Popen):
    try:
        windows = check_output(
            ["xdotool", "search", "--onlyvisible", "--class", "chromium"],
            text=True,
        ).splitlines()
        if not windows:
            return False
        process_factory(["xdotool", "key", "--window", windows[-1], *keys])
        return True
    except Exception:
        return False


def go_home(navigate=open_only):
    return navigate(DASH_URL)


def go_back(key_sender=chromium_key):
    return key_sender("alt+Left")


def go_settings(navigate=open_only):
    return navigate(SETTINGS_URL)


def toggle_keyboard(
    open_file=open,
    kill=os.kill,
    process_factory=subprocess.Popen,
    exists=os.path.exists,
):
    """Toggle the themed keyboard when present, otherwise use Onboard."""
    try:
        with open_file("/tmp/jarvis-keyboard.pid", encoding="utf-8") as pid_file:
            kill(int(pid_file.read().strip()), TOGGLE_SIGNAL)
        return "themed"
    except Exception:
        pass
    try:
        process_factory([
            "dbus-send", "--type=method_call", "--dest=org.onboard.Onboard",
            "/org/onboard/Onboard/Keyboard",
            "org.onboard.Onboard.Keyboard.ToggleVisible",
        ])
        return "onboard"
    except Exception:
        if exists("/opt/jarvis-kiosk/jarvis-keyboard.py"):
            process_factory(["/opt/jarvis-kiosk/jarvis-keyboard.py"])
            return "themed-started"
    return "unavailable"


def perception_display(status, now=None):
    """Return a guarded color/copy pair for the Eyes annunciator."""
    if not isinstance(status, dict):
        raise TypeError("status is not an object")
    current = time.time() if now is None else now
    updated_at = status.get("updated_at")
    if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
        raise ValueError("missing update timestamp")
    age = current - updated_at
    if age > STATUS_STALE_S or age < -5:
        return AMBER, "EYES STALE"
    if status.get("camera_available") is not True:
        return GRAY, "EYES OFF"

    gesture_at = status.get("gesture_timestamp")
    gesture = status.get("last_gesture")
    app = status.get("app")
    gesture_is_fresh = (
        not isinstance(gesture_at, bool)
        and isinstance(gesture_at, (int, float))
        and -1 <= current - gesture_at <= GESTURE_FEEDBACK_S
    )
    if gesture_is_fresh and gesture in GESTURE_ACTIONS and app in KNOWN_APPS:
        return GREEN, f"{GESTURE_ACTIONS[gesture]} · {app.upper()}"
    if status.get("hand_present") is True:
        return GREEN, "HAND SEEN"
    return CYAN, "EYES ONLINE"


def build_bar(tk_module=tk):
    root = tk_module.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=BG)

    def control(label, command, accent=CYAN):
        holder = tk_module.Frame(
            root, width=CONTROL_MIN_WIDTH, height=CONTROL_MIN_HEIGHT, bg=BG,
        )
        holder.pack(side="left", padx=2)
        holder.pack_propagate(False)
        button = tk_module.Button(
            holder, text=label, command=command, bg=PANEL, fg=accent,
            activebackground="#123047", activeforeground="#ffffff",
            bd=0, highlightthickness=1, highlightbackground="#18354a",
            font=("DejaVu Sans", 14, "bold"), takefocus=True,
        )
        button.pack(fill="both", expand=True)
        return button

    control("HOME", go_home)
    control("BACK", go_back)

    status_panel = tk_module.Frame(root, width=252, height=CONTROL_MIN_HEIGHT, bg=BG, takefocus=0)
    status_panel.pack(side="left", padx=2)
    status_panel.pack_propagate(False)
    eye = tk_module.Canvas(status_panel, width=46, height=46, bg=BG, bd=0, highlightthickness=0)
    eye.pack(side="left", padx=(7, 2), pady=4)
    eye.create_oval(7, 7, 39, 39, outline=GRAY, width=3, tags="ring")
    eye.create_oval(18, 18, 28, 28, fill=GRAY, outline="", tags="pupil")
    eye.create_line(23, 1, 23, 11, fill=GRAY, width=2, tags="crosshair")
    eye.create_line(23, 35, 23, 45, fill=GRAY, width=2, tags="crosshair")
    eye.create_line(1, 23, 11, 23, fill=GRAY, width=2, tags="crosshair")
    eye.create_line(35, 23, 45, 23, fill=GRAY, width=2, tags="crosshair")
    status_copy = tk_module.Label(
        status_panel, text="EYES OFF", width=18, anchor="w", bg=BG, fg=GRAY,
        font=("DejaVu Sans Mono", 11, "bold"), bd=0, takefocus=0,
    )
    status_copy.pack(side="left", fill="both", expand=True, padx=(4, 7))

    def show_perception(color, copy):
        eye.itemconfigure("ring", outline=color)
        eye.itemconfigure("pupil", fill=color)
        eye.itemconfigure("crosshair", fill=color)
        status_copy.configure(text=copy[:22], fg=color)

    def poll_perception():
        color, copy = GRAY, "EYES OFF"
        try:
            with open(STATUS_PATH, encoding="utf-8") as status_file:
                status = json.load(status_file)
            color, copy = perception_display(status)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            color, copy = AMBER, "EYES DATA"
        show_perception(color, copy)
        root.after(500, poll_perception)

    control("KEYS", toggle_keyboard)
    control("SETTINGS", go_settings, AMBER)
    root.update_idletasks()
    root.geometry(f"+8+{root.winfo_screenheight() - root.winfo_reqheight() - 8}")

    def stay_visible():
        root.lift()
        root.attributes("-topmost", True)
        root.after(2500, stay_visible)

    poll_perception()
    stay_visible()
    return root


def main():
    build_bar().mainloop()


if __name__ == "__main__":
    main()
