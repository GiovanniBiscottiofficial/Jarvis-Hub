#!/usr/bin/env bash
# Hand-gesture control for the X1 kiosk: swipe at the webcam to drive the
# screen (next/previous Short, skip video, go back). MediaPipe hand tracking
# runs in its own container (it needs Python <=3.12) watching the go2rtc
# webcam stream, so it shares the camera with HA/Frigate.
# Run AFTER setup-satellite.sh (webcam RTSP) and the X11 kiosk setup:
#   bash bootstrap/setup-gestures.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if ! systemctl is-active --quiet go2rtc; then
  echo "!! go2rtc isn't running — run 'bash bootstrap/setup-satellite.sh' first." >&2
  exit 1
fi

echo "==> Building + starting the gesture container (first build ~2 min)..."
docker compose --profile gestures up -d --build

echo ""
echo "=========================================================="
echo " Gesture control running. Stand 2-6 ft from the webcam,"
echo " good lighting, and swipe deliberately:"
echo "   swipe UP      -> next Short (screen slides up)"
echo "   swipe DOWN    -> previous Short"
echo "   swipe FORWARD -> skip / next video   (hand to YOUR right)"
echo "   swipe BACK    -> back to last screen (hand to YOUR left)"
echo ""
echo " Watch it react:   docker logs -f gestures"
echo " Gesture actions use Chromium DevTools on loopback port 9222;"
echo " no Wayland/X11 socket or privileged input access is required."
echo " Too touchy? Set GESTURE_X_TRAVEL/Y_TRAVEL (fraction of the"
echo " frame a swipe must cross, default 0.30/0.28) or"
echo " GESTURE_COOLDOWN_S in docker-compose.yml, then rerun:"
echo "   docker compose --profile gestures up -d"
echo " Turn it off:  docker stop gestures"
echo "=========================================================="
