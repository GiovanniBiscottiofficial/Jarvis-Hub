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

# Boots into the animated Jarvis splash, which hands over to the Wall view.
KIOSK_URL="${KIOSK_URL:-http://localhost:8123/local/jarvis-splash.html?next=/jarvis-hub/wall}"
KIOSK_USER="${KIOSK_USER:-$USER}"
# HiDPI zoom: the X1 Tablet's 3000x2000 panel needs 2x+ to be readable.
# Tune with e.g.  KIOSK_SCALE=2.5 bash bootstrap/setup-kiosk.sh
KIOSK_SCALE="${KIOSK_SCALE:-2.25}"

echo "==> Jarvis kiosk setup (URL: $KIOSK_URL, user: $KIOSK_USER, scale: ${KIOSK_SCALE}x)"

echo "==> Installing minimal X session + Chromium..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  xorg xserver-xorg openbox x11-xserver-utils unclutter chromium-browser \
  onboard dbus-x11 at-spi2-core python3-tk

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

Home: closes Chromium; the kiosk session relaunches it on the dashboard.
Keyboard: toggles the onboard on-screen keyboard over D-Bus.
"""
import subprocess
import tkinter as tk

BG = "#0b1220"
FG = "#38e1ff"


def go_home() -> None:
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
unclutter -idle 5 &  # hide the mouse cursor when idle
openbox-session &
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
gsettings set org.onboard.window.landscape x 100 || true
gsettings set org.onboard.window.landscape y 1100 || true
gsettings set org.onboard.window.landscape width 2800 || true
gsettings set org.onboard.window.landscape height 800 || true
onboard --startup-delay=3 &
# Floating Home + Keyboard buttons (bottom-left corner)
/opt/jarvis-kiosk/hub-bar.py &
# Chromium runs in a loop: the ⌂ Home button closes it, and it comes
# straight back on the Jarvis dashboard (via the boot splash).
while true; do
  chromium-browser --kiosk --noerrdialogs --disable-infobars \\
    --disable-session-crashed-bubble --check-for-update-interval=31536000 \\
    --force-renderer-accessibility \\
    --force-device-scale-factor=${KIOSK_SCALE} \\
    "${KIOSK_URL}"
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
echo "  - Bottom-left corner: ⌂ returns to the dashboard from any"
echo "    app; ⌨ shows/hides the on-screen keyboard."
echo "  - Log into HA once in that Chromium ('remember me') so it"
echo "    stays signed in."
echo "  - Different page? KIOSK_URL=http://localhost:8090 bash bootstrap/setup-kiosk.sh"
echo "    (default shows the Jarvis boot splash, then the Wall view)"
echo "  - Too big/small? KIOSK_SCALE=2.5 bash bootstrap/setup-kiosk.sh (then reboot)"
echo "  - Escape to a terminal any time: Ctrl+Alt+F2."
echo "=========================================================="
