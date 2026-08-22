#!/usr/bin/env bash
# Replace the unstable Weston/Wayland kiosk with an X11/Openbox kiosk.
# Preserves Chromium's profile, HA login, and all Jarvis/Home Assistant data.
set -euo pipefail

KIOSK_USER="${KIOSK_USER:-$USER}"
KIOSK_SCALE="${KIOSK_SCALE:-2.25}"
KIOSK_URL="${KIOSK_URL:-http://localhost:8123/jarvis-hub/wall-plus?kiosk}"
KIOSK_HOME=$(eval echo "~${KIOSK_USER}")
PROFILE="${KIOSK_HOME}/.bash_profile"
XINIT="${KIOSK_HOME}/.xinitrc"
stamp=$(date +%Y%m%d-%H%M%S)

echo "==> Installing X11/Openbox kiosk packages..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  xorg xserver-xorg openbox x11-xserver-utils chromium-browser \
  unclutter dbus-x11 onboard

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
unclutter -idle 5 &
openbox-session &

for _ in \$(seq 1 30); do
  pgrep -x openbox >/dev/null 2>&1 && break
  sleep 0.2
done

export GDK_SCALE=2
export GDK_DPI_SCALE=1
onboard --startup-delay=3 >/dev/null 2>&1 &

FIRST_URL="${KIOSK_URL}"
while true; do
  chromium-browser --kiosk --start-fullscreen --noerrdialogs --disable-infobars \\
    --window-position=0,0 --window-size=3000,2000 \\
    --ozone-platform=x11 --no-first-run --no-default-browser-check \\
    --disable-session-crashed-bubble --check-for-update-interval=31536000 \\
    --force-renderer-accessibility --remote-debugging-port=9222 \\
    --force-device-scale-factor=${KIOSK_SCALE} "\${FIRST_URL}"
  FIRST_URL="http://localhost:8123/jarvis-hub/wall-plus?kiosk"
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
