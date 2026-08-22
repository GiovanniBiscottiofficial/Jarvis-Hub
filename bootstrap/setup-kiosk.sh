#!/usr/bin/env bash
# Turn the X1's own screen into a Google-Home-style hub display:
# boots straight into the Jarvis dashboard, fullscreen, no desktop.
# Run AFTER setup-x1.sh:  bash bootstrap/setup-kiosk.sh
# Works on Ubuntu Server (installs a minimal graphical session) or Desktop.
#
# Two always-on-top touch buttons live in the bottom-left corner:
#   ⌂  Home     — returns to the Jarvis dashboard from anywhere
#                 (Netflix, YouTube, any quick-launch app)
#   ⌨  Keyboard — shows/hides the on-screen keyboard
set -euo pipefail

# First boot shows the animated Jarvis splash, which hands over to the Wall
# view. The ⌂ Home button and the idle screen navigate straight to the
# dashboard (no splash) — the splash is boot-only.
# Wall+ uses ?kiosk (kiosk-mode.js hides the HA header/sidebar).
DASH_URL="${DASH_URL:-http://localhost:8123/jarvis-hub/wall-plus?kiosk}"
IDLE_URL="${IDLE_URL:-http://localhost:8123/local/jarvis-idle.html}"
KIOSK_URL="${KIOSK_URL:-http://localhost:8123/local/jarvis-splash.html?next=/jarvis-hub/wall-plus%3Fkiosk}"
# Minutes of no touch before the ambient idle screen takes over (0 = never)
IDLE_MINUTES="${IDLE_MINUTES:-7}"
KIOSK_USER="${KIOSK_USER:-$USER}"
# HiDPI zoom: the X1 Tablet's 3000x2000 panel needs 2x+ to be readable.
# Tune with e.g.  KIOSK_SCALE=2.5 bash bootstrap/setup-kiosk.sh
KIOSK_SCALE="${KIOSK_SCALE:-2.25}"

echo "==> Jarvis kiosk setup (URL: $KIOSK_URL, user: $KIOSK_USER, scale: ${KIOSK_SCALE}x)"

echo "==> Installing minimal X session + Chromium..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  xorg xserver-xorg openbox x11-xserver-utils unclutter chromium-browser \
  onboard dbus-x11 at-spi2-core python3-tk xprintidle curl wmctrl playerctl xinput

echo "==> Auto-login on tty1..."
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${KIOSK_USER} --noclear %I \$TERM
EOF

echo "==> Hub bar (floating Home + Keyboard touch buttons)..."
sudo mkdir -p /opt/jarvis-kiosk
sudo tee /opt/jarvis-kiosk/hub-bar.py >/dev/null <<'PYEOF'
#!/usr/bin/env python3
"""Floating always-on-top touch bar for the Jarvis kiosk.

Home: navigates the running Chromium tab straight to the dashboard over the
DevTools protocol — instant, no splash, no relaunch. Falls back to killing
Chromium (the session loop relaunches it) only if DevTools is unreachable.
Keyboard: toggles the onboard on-screen keyboard over D-Bus.
"""
import json
import os
import subprocess
import urllib.request

import tkinter as tk

BG = "#0b1220"
FG = "#38e1ff"
DEVTOOLS = "http://127.0.0.1:9222"
DASH_URL = os.environ.get("JARVIS_DASH_URL", "http://localhost:8123/jarvis-hub/wall-plus?kiosk")


def _devtools(path: str, method: str = "GET") -> object:
    req = urllib.request.Request(DEVTOOLS + path, method=method)
    with urllib.request.urlopen(req, timeout=3) as resp:
        body = resp.read()
    return json.loads(body) if body.strip() else None


def go_home() -> None:
    try:
        from urllib.parse import quote
        pages = [t for t in _devtools("/json/list") if t.get("type") == "page"]
        # open the dashboard in a fresh tab (PUT on new Chromium, GET on old)
        try:
            new = _devtools("/json/new?" + quote(DASH_URL, safe=""), method="PUT")
        except Exception:
            new = _devtools("/json/new?" + quote(DASH_URL, safe=""))
        new_id = (new or {}).get("id")
        # close every other tab so kiosk Chromium shows only the dashboard
        for page in pages:
            if page.get("id") and page["id"] != new_id:
                try:
                    _devtools("/json/close/" + page["id"])
                except Exception:
                    pass
    except Exception:
        subprocess.Popen(["pkill", "-f", "chromium"])


def toggle_keyboard() -> None:
    subprocess.Popen([
        "dbus-send", "--type=method_call",
        "--dest=org.onboard.Onboard",
        "/org/onboard/Onboard/Keyboard",
        "org.onboard.Onboard.Keyboard.ToggleVisible",
    ])


root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg=BG)

style = {
    "bg": BG, "fg": FG, "activebackground": "#122036",
    "activeforeground": FG, "bd": 0, "highlightthickness": 0,
    "font": ("DejaVu Sans", 40), "width": 2, "height": 1,
}
tk.Button(root, text="\u2302", command=go_home, **style).pack(side="left")
tk.Button(root, text="\u2328", command=toggle_keyboard, **style).pack(side="left")

root.update_idletasks()
x = 8
y = root.winfo_screenheight() - root.winfo_reqheight() - 8
root.geometry(f"+{x}+{y}")


def stay_on_top() -> None:
    root.lift()
    root.attributes("-topmost", True)
    root.after(3000, stay_on_top)


stay_on_top()
root.mainloop()
PYEOF
sudo chmod +x /opt/jarvis-kiosk/hub-bar.py

echo "==> Idle watcher (ambient clock screen after inactivity)..."
sudo tee /opt/jarvis-kiosk/idle-watch.sh >/dev/null <<'SHEOF'
#!/usr/bin/env bash
# Drift to the ambient idle screen after N minutes without touch input.
# Tapping the idle screen navigates itself back to the dashboard, which
# also resets the X idle counter — no wake logic needed here.
IDLE_MINUTES="${1:-7}"
IDLE_URL="${2:-http://localhost:8123/local/jarvis-idle.html}"
DEVTOOLS="http://127.0.0.1:9222"
[ "$IDLE_MINUTES" = "0" ] && exit 0
THRESH_MS=$((IDLE_MINUTES * 60000))
while true; do
  sleep 20
  idle=$(xprintidle 2>/dev/null) || continue
  [ "$idle" -lt "$THRESH_MS" ] && continue
  # already ambient (or splash still up)? do nothing
  current=$(curl -fsS --max-time 3 "$DEVTOOLS/json/list" 2>/dev/null) || continue
  echo "$current" | grep -q "jarvis-idle.html" && continue
  echo "$current" | grep -q "jarvis-splash.html" && continue
  # HTML5 media exposed by Chromium through MPRIS counts as active use. Do not
  # replace a playing video, music stream, or podcast with the ambient screen.
  playerctl --all-players status 2>/dev/null | grep -qi '^playing$' && continue
  # open the idle page in a new tab, then close the others
  enc_url=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$IDLE_URL")
  new_id=$(curl -fsS --max-time 3 -X PUT "$DEVTOOLS/json/new?$enc_url" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  [ -n "$new_id" ] || new_id=$(curl -fsS --max-time 3 "$DEVTOOLS/json/new?$enc_url" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  [ -n "$new_id" ] || continue
  echo "$current" | python3 -c "
import json, sys
for t in json.load(sys.stdin):
    if t.get('type') == 'page' and t.get('id') and t['id'] != '$new_id':
        print(t['id'])
" | while read -r tid; do
    curl -fsS --max-time 3 "$DEVTOOLS/json/close/$tid" >/dev/null 2>&1
  done
done
SHEOF
sudo chmod +x /opt/jarvis-kiosk/idle-watch.sh

echo "==> Kiosk session (openbox + fullscreen Chromium)..."
KIOSK_HOME=$(eval echo "~${KIOSK_USER}")
sudo tee "${KIOSK_HOME}/.xinitrc" >/dev/null <<EOF
#!/bin/sh
# session bus (needed by the on-screen keyboard)
if [ -z "\$DBUS_SESSION_BUS_ADDRESS" ]; then
  eval "\$(dbus-launch --sh-syntax --exit-with-session)"
fi
xset s off          # never blank the screen
xset -dpms
xset s noblank
pkill -x xss-lock >/dev/null 2>&1 || true
pkill -x light-locker >/dev/null 2>&1 || true
pkill -x gnome-screensaver >/dev/null 2>&1 || true
gsettings set org.gnome.desktop.screensaver lock-enabled false >/dev/null 2>&1 || true
gsettings set org.gnome.desktop.session idle-delay 0 >/dev/null 2>&1 || true
unclutter -idle 5 &  # hide the mouse cursor when idle
openbox-session &
# Wait for Openbox to claim the display. Without this, Chromium can sometimes
# start as a small unmanaged window instead of a true fullscreen kiosk.
for _ in \$(seq 1 30); do
  wmctrl -m >/dev/null 2>&1 && break
  sleep 0.2
done
touch_output=\$(xrandr --current | awk '/^eDP[^ ]* connected/ {print \$1; exit}')
if [ -z "\$touch_output" ]; then
  touch_output=\$(xrandr --current | awk '/ connected/ {print \$1; exit}')
fi
if [ -n "\$touch_output" ]; then
  xinput list --name-only | while IFS= read -r input_name; do
    normalized=\$(printf '%s' "\$input_name" | tr '[:upper:]' '[:lower:]')
    case "\$normalized" in
      *touchscreen*|*"touch screen"*|*"finger touch"*)
        xinput map-to-output "\$input_name" "\$touch_output" >/dev/null 2>&1 || true
        ;;
    esac
  done
fi
export JARVIS_DASH_URL="${DASH_URL}"
# HiDPI: scale the keyboard and other GTK bits
export GDK_SCALE=2
export GDK_DPI_SCALE=1
# On-screen touch keyboard, hidden until the ⌨ button shows it.
# force-to-top is required: without it the keyboard renders BEHIND the
# fullscreen kiosk Chromium and only the tiny icon palette is visible.
gsettings set org.onboard layout Compact || true
gsettings set org.onboard use-system-defaults false || true
gsettings set org.onboard.icon-palette in-use false || true
gsettings set org.onboard.auto-show enabled false || true
gsettings set org.onboard.window force-to-top true || true
gsettings set org.onboard.window docking-enabled false || true
# Geometry keys are multiplied by GDK_SCALE (2x): these land the keyboard
# at 120,1240 sized 2880x680 — fully on the 3000x2000 panel.
gsettings set org.onboard.window.landscape x 60 || true
gsettings set org.onboard.window.landscape y 620 || true
gsettings set org.onboard.window.landscape width 1440 || true
gsettings set org.onboard.window.landscape height 340 || true
onboard --startup-delay=3 &
# Floating Home + Keyboard buttons (bottom-left corner)
/opt/jarvis-kiosk/hub-bar.py &
# Ambient idle screen: after a few minutes of no touch, drift to the
# dim clock screen; any tap on it returns to the dashboard.
/opt/jarvis-kiosk/idle-watch.sh "${IDLE_MINUTES}" "${IDLE_URL}" &
# Chromium relaunch loop. The splash shows ONLY on the first launch of the
# session (real boot); if Chromium is ever closed/crashes it comes back
# directly on the dashboard — and the ⌂ Home button never relaunches it,
# it just navigates the running tab.
FIRST_URL="${KIOSK_URL}"
while true; do
  # Do not let Chromium persist a boot-time connection error in crash restore.
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
    --autoplay-policy=no-user-gesture-required \\
    --touch-events=enabled \\
    --window-position=0,0 --window-size="\${screen_width},\${screen_height}" \\
    --no-first-run --no-default-browser-check \\
    --disable-session-crashed-bubble --check-for-update-interval=31536000 \\
    --force-renderer-accessibility \\
    --remote-debugging-port=9222 \\
    --force-device-scale-factor=${KIOSK_SCALE} \\
    "\${FIRST_URL}"
  FIRST_URL="${DASH_URL}"
  sleep 1
done
EOF
sudo chown "${KIOSK_USER}:" "${KIOSK_HOME}/.xinitrc"

PROFILE="${KIOSK_HOME}/.bash_profile"
if ! grep -q 'startx' "$PROFILE" 2>/dev/null; then
  sudo tee -a "$PROFILE" >/dev/null <<'EOF'
# Auto-start the Jarvis kiosk on tty1
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  exec startx
fi
EOF
  sudo chown "${KIOSK_USER}:" "$PROFILE"
fi

echo ""
echo "=========================================================="
echo " Kiosk installed. Reboot and the X1 boots straight into"
echo " the Jarvis dashboard, fullscreen."
echo ""
echo " Tips:"
echo "  - Bottom-left corner: ⌂ jumps straight to the dashboard from"
echo "    any app (no splash — that's boot-only now); ⌨ shows/hides"
echo "    the on-screen keyboard."
echo "  - After ${IDLE_MINUTES} min of no touch the ambient clock screen"
echo "    appears; tap anywhere to get the dashboard back."
echo "    Change it: IDLE_MINUTES=15 bash bootstrap/setup-kiosk.sh (0 = off)"
echo "  - Log into HA once in that Chromium ('remember me') so it"
echo "    stays signed in."
echo "  - Different page? KIOSK_URL=http://localhost:8090 bash bootstrap/setup-kiosk.sh"
echo "    (default shows the Jarvis boot splash, then the Wall view)"
echo "  - Too big/small? KIOSK_SCALE=2.5 bash bootstrap/setup-kiosk.sh (then reboot)"
echo "  - Boxed or white error screen? bash bootstrap/repair-kiosk.sh"
echo "  - Escape to a terminal any time: Ctrl+Alt+F2."
echo "=========================================================="
