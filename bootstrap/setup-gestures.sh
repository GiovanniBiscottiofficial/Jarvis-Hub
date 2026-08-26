#!/usr/bin/env bash
# Hand-gesture control for the X1 kiosk: swipe at the webcam to drive the
# screen (next/previous Short, skip video, go back). MediaPipe face and hand
# tracking runs locally in its own container and never stores camera frames.
# Run AFTER setup-satellite.sh (webcam RTSP) and the X11 kiosk setup:
#   bash bootstrap/setup-gestures.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

detect_webcam() {
  for dev in /dev/video*; do
    [ -e "$dev" ] || continue
    if udevadm info -q property -n "$dev" 2>/dev/null | grep -q "ID_USB_DRIVER=uvcvideo" \
       && v4l2-ctl -d "$dev" --list-formats-ext 2>/dev/null | grep -q "Video Capture"; then
      echo "$dev"
      return 0
    fi
  done
  return 1
}

command -v v4l2-ctl >/dev/null || {
  echo "!! v4l2-ctl is required; run bootstrap/setup-satellite.sh first." >&2
  exit 1
}
CAMERA_DEVICE="${X1_CAMERA_DEVICE:-}"
if [ -z "$CAMERA_DEVICE" ]; then
  CAMERA_DEVICE="$(detect_webcam || true)"
fi
if [ -z "$CAMERA_DEVICE" ] || [ ! -r "$CAMERA_DEVICE" ]; then
  echo "!! No readable X1 UVC camera was found." >&2
  exit 1
fi

if ! curl -fsS --max-time 3 http://127.0.0.1:9222/json/version >/dev/null; then
  echo "!! Chromium DevTools is not reachable on port 9222." >&2
  echo "   Re-run bootstrap/revert-kiosk-to-x11.sh, then try again." >&2
  exit 1
fi

if [[ ! -f .env ]] || ! grep -Eq '^LIFEOS_API_TOKEN=.+$' .env; then
  echo "!! LIFEOS_API_TOKEN is not set in .env." >&2
  echo "   Gestures will control the kiosk, but perception will not appear in LifeOS." >&2
fi

echo "==> Preparing the local kiosk perception channel..."
sudo install -d -m 0755 /run/jarvis

echo "==> Building + starting perception on ${CAMERA_DEVICE} (first build ~2 min)..."
X1_CAMERA_DEVICE="$CAMERA_DEVICE" GESTURE_SOURCE="$CAMERA_DEVICE" \
  docker compose --profile gestures up -d --build
sleep 4
docker logs --tail 12 gestures || true

echo ""
echo "=========================================================="
echo " Gesture control running. Stand 2-6 ft from the webcam,"
echo " good lighting, and swipe deliberately:"
echo "   swipe UP      -> next Short (screen slides up)"
echo "   swipe DOWN    -> previous Short"
echo "   swipe FORWARD -> skip / next video   (hand to YOUR right)"
echo "   swipe BACK    -> back to last screen (hand to YOUR left)"
echo "   PINCH + hold   -> play / pause"
echo "   TWO-FINGER swipe left/right -> seek -/+ 10 seconds"
echo "   TWO-FINGER swipe up/down    -> volume up/down"
echo "   Audio never uses a static thumb/fist pose; this prevents accidental"
echo "   volume changes from ordinary movements such as touching your head."
echo ""
echo " Watch it react:   docker logs -f gestures"
echo " Kiosk indicator:   /run/jarvis/perception.json"
echo " Gesture actions use Chromium DevTools on loopback port 9222;"
echo " no Wayland/X11 socket or privileged input access is required."
echo " Face or hand presence improves room awareness without identifying anyone."
echo " LifeOS receives presence/gesture metadata only. Camera frames stay local"
echo " and are never written to the LifeOS database."
echo " Too touchy? Set GESTURE_X_TRAVEL/Y_TRAVEL (fraction of the"
echo " frame a swipe must cross, default 0.20/0.18) or"
echo " GESTURE_COOLDOWN_S/GESTURE_POSE_HOLD_S, then rerun:"
echo "   docker compose --profile gestures up -d"
echo " Turn it off:  docker stop gestures"
echo "=========================================================="
