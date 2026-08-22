#!/usr/bin/env bash
# Replace the unstable Weston/Wayland kiosk with an X11/Openbox kiosk.
# Preserves Chromium's profile, HA login, and all Jarvis/Home Assistant data.
set -euo pipefail

KIOSK_USER="${KIOSK_USER:-$USER}"
KIOSK_SCALE="${KIOSK_SCALE:-2.25}"
DASH_URL="${DASH_URL:-http://localhost:8123/jarvis-hub/wall-plus?kiosk}"
KIOSK_URL="${KIOSK_URL:-http://localhost:8123/local/jarvis-splash.html?next=/jarvis-hub/wall-plus%3Fkiosk}"
KIOSK_HOME=$(eval echo "~${KIOSK_USER}")
PROFILE="${KIOSK_HOME}/.bash_profile"
XINIT="${KIOSK_HOME}/.xinitrc"
stamp=$(date +%Y%m%d-%H%M%S)

echo "==> Installing X11/Openbox kiosk packages..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  xorg xserver-xorg openbox x11-xserver-utils chromium-browser \
  unclutter dbus-x11 curl python3-tk xdotool

echo "==> Installing the floating Home and keyboard controls..."
sudo mkdir -p /opt/jarvis-kiosk
sudo tee /opt/jarvis-kiosk/hub-bar.py >/dev/null <<'PYEOF'
#!/usr/bin/env python3
"""Always-on-top controls for returning to Jarvis and opening the keyboard."""
import json
import os
import signal
import subprocess
import tkinter as tk
import urllib.request
from urllib.parse import quote

DEVTOOLS = "http://127.0.0.1:9222"
DASH_URL = os.environ.get(
    "JARVIS_DASH_URL",
    "http://localhost:8123/jarvis-hub/wall-plus?kiosk",
)


def request(path, method="GET"):
    req = urllib.request.Request(DEVTOOLS + path, method=method)
    with urllib.request.urlopen(req, timeout=3) as response:
        body = response.read()
    return json.loads(body) if body.strip() else None


def go_home():
    try:
        pages = [page for page in request("/json/list") if page.get("type") == "page"]
        try:
            new_page = request("/json/new?" + quote(DASH_URL, safe=""), "PUT")
        except Exception:
            new_page = request("/json/new?" + quote(DASH_URL, safe=""))
        new_id = (new_page or {}).get("id")
        for page in pages:
            if page.get("id") and page["id"] != new_id:
                try:
                    request("/json/close/" + page["id"])
                except Exception:
                    pass
    except Exception:
        pass


def toggle_keyboard():
    try:
        with open("/tmp/jarvis-keyboard.pid", encoding="utf-8") as pid_file:
            os.kill(int(pid_file.read().strip()), signal.SIGUSR1)
    except Exception:
        subprocess.Popen(["/opt/jarvis-kiosk/jarvis-keyboard.py"])


root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg="#0b1220")
style = {
    "bg": "#0b1220", "fg": "#38e1ff", "activebackground": "#122036",
    "activeforeground": "#38e1ff", "bd": 0, "highlightthickness": 0,
    "font": ("DejaVu Sans", 30), "width": 2, "height": 1,
}
tk.Button(root, text="\u2302", command=go_home, **style).pack(side="left")
keys_style = {
    **style,
    "font": ("DejaVu Sans", 16, "bold"),
    "width": 5,
}
tk.Button(root, text="KEYS", command=toggle_keyboard, **keys_style).pack(side="left")
root.update_idletasks()
root.geometry(f"+8+{root.winfo_screenheight() - root.winfo_reqheight() - 8}")


def stay_visible():
    root.lift()
    root.attributes("-topmost", True)
    root.after(3000, stay_visible)


stay_visible()
root.mainloop()
PYEOF
sudo chmod +x /opt/jarvis-kiosk/hub-bar.py

sudo tee /opt/jarvis-kiosk/jarvis-keyboard.py >/dev/null <<'PYEOF'
#!/usr/bin/env python3
"""Touch-first Jarvis keyboard that types directly into Chromium."""
import os
import signal
import subprocess
import tkinter as tk

BG = "#020911"
PANEL = "#071522"
KEY = "#0c2232"
KEY_ACTIVE = "#16445b"
CYAN = "#7fe9ff"
MUTED = "#7898aa"
PURPLE = "#9f83ff"
GREEN = "#75e6a0"
PID_FILE = "/tmp/jarvis-keyboard.pid"

root = tk.Tk()
root.title("Jarvis Input")
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg=BG, highlightbackground="#24c9ee", highlightthickness=2)
root.withdraw()

target_window = None
shifted = False
letter_buttons = []


def chromium_window():
    try:
        windows = subprocess.check_output(
            ["xdotool", "search", "--onlyvisible", "--class", "chromium"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
        return windows[-1] if windows else None
    except Exception:
        return None


def send_key(key):
    global shifted
    window = target_window or chromium_window()
    if not window:
        return
    if len(key) == 1:
        value = key.upper() if shifted and key.isalpha() else key
        subprocess.Popen([
            "xdotool", "type", "--clearmodifiers", "--delay", "0",
            "--window", str(window), value,
        ])
        if shifted:
            toggle_shift()
    else:
        subprocess.Popen([
            "xdotool", "key", "--clearmodifiers", "--window", str(window), key,
        ])


def type_text(value):
    window = target_window or chromium_window()
    if window:
        subprocess.Popen([
            "xdotool", "type", "--clearmodifiers", "--delay", "0",
            "--window", str(window), value,
        ])


def toggle_shift():
    global shifted
    shifted = not shifted
    for button, letter in letter_buttons:
        button.configure(text=letter.upper() if shifted else letter.lower())
    shift_button.configure(bg=PURPLE if shifted else KEY)


def hide():
    root.withdraw()


def toggle():
    global target_window
    if root.state() == "withdrawn":
        target_window = chromium_window()
        root.deiconify()
        root.lift()
    else:
        hide()


def button(parent, label, command, column, width=1, accent=None):
    widget = tk.Button(
        parent, text=label, command=command, bg=accent or KEY, fg=CYAN,
        activebackground=KEY_ACTIVE, activeforeground="#ffffff", bd=0,
        highlightthickness=0, font=("DejaVu Sans", 19, "bold"),
        takefocus=False,
    )
    widget.grid(row=0, column=column, columnspan=width, sticky="nsew", padx=4, pady=4)
    return widget


header = tk.Frame(root, bg=PANEL, height=58)
header.pack(fill="x")
tk.Label(
    header, text="JARVIS // SECURE INPUT", bg=PANEL, fg=CYAN,
    font=("DejaVu Sans Mono", 14, "bold"), padx=18, pady=12,
).pack(side="left")
tk.Label(
    header, text="LOCAL KEYBOARD", bg=PANEL, fg=MUTED,
    font=("DejaVu Sans Mono", 10), padx=12,
).pack(side="left")
tk.Button(
    header, text="CLOSE  ×", command=hide, bg="#142536", fg=CYAN,
    activebackground="#28445a", activeforeground="white", bd=0,
    font=("DejaVu Sans Mono", 12, "bold"), padx=20, pady=12,
).pack(side="right")

body = tk.Frame(root, bg=BG, padx=12, pady=10)
body.pack(fill="both", expand=True)

rows = [
    [(char, char) for char in "1234567890"] + [("⌫", "BackSpace")],
    [(char, char) for char in "!@#$%^&*()+="] ,
    [(char, char) for char in "qwertyuiop"] + [("↵", "Return")],
    [(char, char) for char in "asdfghjkl"] + [("-", "-"), ("_", "_")],
    [(char, char) for char in "zxcvbnm"] + [("@", "@"), (".", "."), ("/", "/")],
    [
        ("~", "~"), ("`", "`"), ("<", "<"), (">", ">"),
        ("[", "["), ("]", "]"), ("{", "{"), ("}", "}"),
        ("\\", "\\"), ("|", "|"), (":", ":"), (";", ";"),
        ("'", "'"), ('\"', '\"'), ("?", "?"),
    ],
]

for row_index, keys in enumerate(rows):
    row = tk.Frame(body, bg=BG)
    row.pack(fill="both", expand=True)
    for column in range(len(keys)):
        row.grid_columnconfigure(column, weight=1, uniform=f"row{row_index}")
    row.grid_rowconfigure(0, weight=1)
    for column, (label, key) in enumerate(keys):
        widget = button(row, label, lambda value=key: send_key(value), column)
        if label.isalpha() and len(label) == 1:
            letter_buttons.append((widget, label))

controls = tk.Frame(body, bg=BG)
controls.pack(fill="both", expand=True)
for column, weight in enumerate((2, 2, 7, 2, 2)):
    controls.grid_columnconfigure(column, weight=weight)
controls.grid_rowconfigure(0, weight=1)
shift_button = button(controls, "⇧ SHIFT", toggle_shift, 0)
button(controls, ".COM", lambda: type_text(".com"), 1, accent="#16233d")
button(controls, "SPACE", lambda: send_key("space"), 2, accent="#102c3d")
button(controls, "←", lambda: send_key("Left"), 3)
button(controls, "→", lambda: send_key("Right"), 4)

screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
width = int(screen_width * 0.92)
height = min(int(screen_height * 0.42), 760)
root.geometry(f"{width}x{height}+{(screen_width - width) // 2}+{screen_height - height - 18}")

with open(PID_FILE, "w", encoding="utf-8") as pid_file:
    pid_file.write(str(os.getpid()))


def signal_toggle(_signum, _frame):
    root.after(0, toggle)


signal.signal(signal.SIGUSR1, signal_toggle)
root.mainloop()
PYEOF
sudo chmod +x /opt/jarvis-kiosk/jarvis-keyboard.py

[ ! -f "$PROFILE" ] || sudo cp -a "$PROFILE" "${PROFILE}.before-x11-${stamp}"
[ ! -f "$XINIT" ] || sudo cp -a "$XINIT" "${XINIT}.before-x11-${stamp}"

echo "==> Removing Weston tty1 auto-start..."
sudo sed -i '/# Auto-start the Jarvis kiosk (Weston\/Wayland) on tty1/,+3d' \
  "$PROFILE" 2>/dev/null || true
sudo sed -i '/# Auto-start the Jarvis kiosk on tty1/,+3d' \
  "$PROFILE" 2>/dev/null || true

echo "==> Writing deterministic fullscreen X11 session..."
sudo tee "$XINIT" >/dev/null <<EOF
#!/bin/sh
if [ -z "\$DBUS_SESSION_BUS_ADDRESS" ]; then
  eval "\$(dbus-launch --sh-syntax --exit-with-session)"
fi

xset s off
xset -dpms
xset s noblank
# A dedicated wall console must never present an OS lock screen. Chromium's
# media session separately reports playback, but these guards also cover sites
# whose players do not publish MPRIS metadata.
pkill -x xss-lock >/dev/null 2>&1 || true
pkill -x light-locker >/dev/null 2>&1 || true
pkill -x gnome-screensaver >/dev/null 2>&1 || true
gsettings set org.gnome.desktop.screensaver lock-enabled false >/dev/null 2>&1 || true
gsettings set org.gnome.desktop.session idle-delay 0 >/dev/null 2>&1 || true
unclutter -idle 5 &
openbox-session &

for _ in \$(seq 1 30); do
  pgrep -x openbox >/dev/null 2>&1 && break
  sleep 0.2
done

export GDK_SCALE=2
export GDK_DPI_SCALE=1
/opt/jarvis-kiosk/jarvis-keyboard.py >/dev/null 2>&1 &
export JARVIS_DASH_URL="${DASH_URL}"
/opt/jarvis-kiosk/hub-bar.py >/dev/null 2>&1 &

FIRST_URL="${KIOSK_URL}"
while true; do
  # Avoid persisting a boot-time connection error in Chromium's kiosk profile.
  for _ in \$(seq 1 90); do
    code=\$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 \
      http://localhost:8123/ 2>/dev/null || true)
    case "\$code" in 2*|3*|401|403) break ;; esac
    sleep 2
  done
  screen_size=\$(xrandr --current 2>/dev/null | awk '/\\*/ {print \$1; exit}')
  screen_width=\${screen_size%x*}
  screen_height=\${screen_size#*x}
  [ -n "\$screen_width" ] || screen_width=3000
  [ -n "\$screen_height" ] || screen_height=2000
  systemd-inhibit --what=idle:sleep --mode=block --who="Jarvis kiosk" \
    --why="Kiosk display and media playback are active" \
    chromium-browser --kiosk --start-fullscreen --noerrdialogs --disable-infobars \\
    --window-position=0,0 --window-size="\${screen_width},\${screen_height}" \\
    --ozone-platform=x11 --no-first-run --no-default-browser-check \\
    --disable-session-crashed-bubble --check-for-update-interval=31536000 \\
    --force-renderer-accessibility --remote-debugging-port=9222 \\
    --force-device-scale-factor=${KIOSK_SCALE} "\${FIRST_URL}"
  FIRST_URL="${DASH_URL}"
  sleep 2
done
EOF
sudo chmod +x "$XINIT"
sudo chown "${KIOSK_USER}:" "$XINIT"

sudo tee -a "$PROFILE" >/dev/null <<'EOF'
# Auto-start the Jarvis kiosk on tty1
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  exec startx
fi
EOF
sudo chown "${KIOSK_USER}:" "$PROFILE"

sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${KIOSK_USER} --noclear %I \$TERM
EOF
sudo systemctl daemon-reload

echo "==> X11 kiosk installed and validated."
if [ "${1:-}" != "--no-restart" ]; then
  echo "==> Restarting tty1..."
  sudo systemctl restart getty@tty1.service
fi
