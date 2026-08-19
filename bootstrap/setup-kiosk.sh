#!/usr/bin/env bash
# Turn the X1's own screen into a Google-Home-style hub display:
# boots straight into the Jarvis dashboard, fullscreen, no desktop.
# Run AFTER setup-x1.sh:  bash bootstrap/setup-kiosk.sh
# Works on Ubuntu Server (installs a minimal graphical session) or Desktop.
set -euo pipefail

KIOSK_URL="${KIOSK_URL:-http://localhost:8123/jarvis-hub/wall}"
KIOSK_USER="${KIOSK_USER:-$USER}"
# HiDPI zoom: the X1 Tablet's 3000x2000 panel needs 2x+ to be readable.
# Tune with e.g.  KIOSK_SCALE=2.5 bash bootstrap/setup-kiosk.sh
KIOSK_SCALE="${KIOSK_SCALE:-2.25}"

echo "==> Jarvis kiosk setup (URL: $KIOSK_URL, user: $KIOSK_USER, scale: ${KIOSK_SCALE}x)"

echo "==> Installing minimal X session + Chromium..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  xorg xserver-xorg openbox x11-xserver-utils unclutter chromium-browser \
  onboard dbus-x11 at-spi2-core

echo "==> Auto-login on tty1..."
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${KIOSK_USER} --noclear %I \$TERM
EOF

echo "==> Kiosk session (openbox + fullscreen Chromium)..."
KIOSK_HOME=$(eval echo "~${KIOSK_USER}")
sudo tee "${KIOSK_HOME}/.xinitrc" >/dev/null <<EOF
#!/bin/sh
# session bus (needed by the on-screen keyboard settings)
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
# On-screen touch keyboard: pops up when you tap a text field;
# when hidden, a small floating icon brings it back.
gsettings set org.onboard layout Compact || true
gsettings set org.onboard.auto-show enabled true || true
gsettings set org.onboard.window docking-enabled true || true
gsettings set org.onboard.icon-palette in-use true || true
onboard &
chromium-browser --kiosk --noerrdialogs --disable-infobars \\
  --disable-session-crashed-bubble --check-for-update-interval=31536000 \\
  --force-renderer-accessibility \\
  --force-device-scale-factor=${KIOSK_SCALE} \\
  "${KIOSK_URL}"
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
echo "  - Tap any text box and the on-screen keyboard pops up;"
echo "    the little floating icon brings it back if hidden."
echo "  - Log into HA once in that Chromium ('remember me') so it"
echo "    stays signed in."
echo "  - Different page? KIOSK_URL=http://localhost:8090 bash bootstrap/setup-kiosk.sh"
echo "  - Too big/small? KIOSK_SCALE=2.5 bash bootstrap/setup-kiosk.sh (then reboot)"
echo "  - Escape to a terminal any time: Ctrl+Alt+F2."
echo "=========================================================="
