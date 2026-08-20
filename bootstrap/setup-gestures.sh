#!/usr/bin/env bash
# Hand-gesture control for the X1 kiosk: swipe at the webcam to drive the
# screen (next/previous Short, skip video, go back). Uses MediaPipe hand
# tracking on the go2rtc webcam stream, so it shares the camera with HA.
# Run AFTER setup-satellite.sh (which provides the webcam RTSP stream)
# and setup-kiosk.sh:  bash bootstrap/setup-gestures.sh
set -euo pipefail

GESTURE_USER="${GESTURE_USER:-$USER}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Jarvis gesture control setup (user: $GESTURE_USER)"

echo "==> Installing xdotool + Python env (mediapipe download is ~100MB)..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends xdotool python3-venv
sudo mkdir -p /opt/jarvis-gestures
sudo python3 -m venv /opt/jarvis-gestures/venv
sudo /opt/jarvis-gestures/venv/bin/pip install --upgrade pip
sudo /opt/jarvis-gestures/venv/bin/pip install mediapipe opencv-python-headless

sudo install -m 755 "${REPO_DIR}/gestures/gesture_service.py" /opt/jarvis-gestures/

sudo tee /etc/systemd/system/jarvis-gestures.service >/dev/null <<EOF
[Unit]
Description=Jarvis hand-gesture control (webcam swipes drive the kiosk)
After=graphical.target go2rtc.service

[Service]
User=${GESTURE_USER}
Environment=DISPLAY=:0
Environment=GESTURE_SOURCE=rtsp://127.0.0.1:8556/x1_webcam
# Tuning knobs (uncomment to adjust):
#Environment=GESTURE_COOLDOWN_S=1.4
#Environment=GESTURE_X_TRAVEL=0.30
#Environment=GESTURE_Y_TRAVEL=0.28
ExecStart=/opt/jarvis-gestures/venv/bin/python /opt/jarvis-gestures/gesture_service.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now jarvis-gestures.service

echo ""
echo "=========================================================="
echo " Gesture control installed. Stand 2-6 ft from the webcam,"
echo " good lighting, and swipe deliberately:"
echo "   swipe UP      -> next Short (screen slides up)"
echo "   swipe DOWN    -> previous Short"
echo "   swipe FORWARD -> skip / next video   (hand to YOUR right)"
echo "   swipe BACK    -> back to last screen (hand to YOUR left)"
echo ""
echo " Watch it react:  journalctl -u jarvis-gestures -f"
echo " Too touchy? Raise GESTURE_X_TRAVEL/Y_TRAVEL in"
echo " /etc/systemd/system/jarvis-gestures.service, then:"
echo "   sudo systemctl daemon-reload && sudo systemctl restart jarvis-gestures"
echo "=========================================================="
