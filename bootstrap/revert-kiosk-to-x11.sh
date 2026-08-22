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
  unclutter dbus-x11 onboard curl python3-tk

echo "==> Installing the floating Home and keyboard controls..."
sudo mkdir -p /opt/jarvis-kiosk
sudo tee /opt/jarvis-kiosk/hub-bar.py >/dev/null <<'PYEOF'
#!/usr/bin/env python3
"""Always-on-top controls for returning to Jarvis and opening the keyboard."""
import json
import os
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
    subprocess.Popen([
        "dbus-send", "--type=method_call", "--dest=org.onboard.Onboard",
        "/org/onboard/Onboard/Keyboard",
        "org.onboard.Onboard.Keyboard.ToggleVisible",
    ])


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
tk.Button(root, text="\u2328", command=toggle_keyboard, **style).pack(side="left")
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
onboard --startup-delay=3 >/dev/null 2>&1 &
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
