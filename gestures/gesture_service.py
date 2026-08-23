#!/usr/bin/env python3
"""Jarvis hand-gesture control for the X1 kiosk.

Watches the webcam (via go2rtc's RTSP restream, so it shares the camera with
Frigate/HA) with MediaPipe hand tracking and turns deliberate swipes into
kiosk input:

    swipe UP      -> screen slides up (scroll down)  = next YouTube Short
    swipe DOWN    -> screen slides down (scroll up)  = previous Short
    swipe FORWARD -> Down arrow                      = skip / next video
    swipe BACK    -> Alt+Left                        = back to the last screen

The frame is mirrored (selfie view), so "forward" = your hand moving to YOUR
right, "back" = to your left. A swipe must cross about a third of the frame
within ~0.6 s; after each action there is a cooldown so one wave can't fire
twice.
"""

import os
import json
import time
from collections import deque
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import cv2
import mediapipe as mp
import websocket

SOURCE = os.environ.get("GESTURE_SOURCE", "rtsp://127.0.0.1:8556/x1_webcam")
DEVTOOLS_URL = os.environ.get("GESTURE_DEVTOOLS_URL", "http://127.0.0.1:9222")
PERCEPTION_STATUS_PATH = os.environ.get(
    "GESTURE_STATUS_PATH", "/run/jarvis/perception.json"
)
# Fraction of frame the hand must travel to count as a swipe
X_TRAVEL = float(os.environ.get("GESTURE_X_TRAVEL", "0.20"))
Y_TRAVEL = float(os.environ.get("GESTURE_Y_TRAVEL", "0.18"))
WINDOW_S = float(os.environ.get("GESTURE_WINDOW_S", "0.9"))
COOLDOWN_S = float(os.environ.get("GESTURE_COOLDOWN_S", "1.2"))
STATUS_INTERVAL_S = float(os.environ.get("GESTURE_STATUS_INTERVAL_S", "15"))
LIFEOS_URL = os.environ.get("LIFEOS_URL", "http://127.0.0.1:8090").rstrip("/")
LIFEOS_TOKEN = os.environ.get("LIFEOS_API_TOKEN", "")
PRESENCE_CLEAR_S = float(os.environ.get("GESTURE_PRESENCE_CLEAR_S", "8"))
PRESENCE_HEARTBEAT_S = float(os.environ.get("GESTURE_PRESENCE_HEARTBEAT_S", "300"))

_perception_status = {
    "camera_available": False,
    "hand_present": False,
    "last_gesture": None,
    "app": None,
    "gesture_timestamp": None,
    "updated_at": None,
}


def write_perception_status(**changes: object) -> None:
    """Atomically expose local metadata for the kiosk toolbar; never frames."""
    _perception_status.update(changes)
    _perception_status["updated_at"] = time.time()
    status_dir = os.path.dirname(PERCEPTION_STATUS_PATH)
    temporary_path = f"{PERCEPTION_STATUS_PATH}.tmp"
    try:
        os.makedirs(status_dir, mode=0o755, exist_ok=True)
        with open(temporary_path, "w", encoding="utf-8") as status_file:
            json.dump(_perception_status, status_file, separators=(",", ":"))
            status_file.flush()
            os.fsync(status_file.fileno())
        os.replace(temporary_path, PERCEPTION_STATUS_PATH)
        os.chmod(PERCEPTION_STATUS_PATH, 0o644)
    except OSError as error:
        print(f"perception status write failed: {error}", flush=True)


def publish_event(
    event_type: str,
    *,
    entity_id: str | None = None,
    state: str | None = None,
    previous_state: str | None = None,
    attributes: dict | None = None,
    confidence: float = 1.0,
) -> None:
    """Publish metadata only. Camera frames never leave the perception worker."""
    if not LIFEOS_TOKEN:
        return
    body = json.dumps(
        {
            "source": "x1_vision",
            "event_type": event_type,
            "entity_id": entity_id,
            "state": state,
            "previous_state": previous_state,
            "attributes": {"local_only": True, **(attributes or {})},
            "confidence": confidence,
        }
    ).encode()
    request = Request(
        f"{LIFEOS_URL}/api/events",
        data=body,
        headers={
            "Authorization": f"Bearer {LIFEOS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            response.read()
    except Exception as error:
        print(f"LifeOS perception publish failed: {error}", flush=True)


def active_page() -> dict:
    with urlopen(f"{DEVTOOLS_URL}/json/list", timeout=3) as response:
        targets = json.load(response)
    pages = [target for target in targets if target.get("type") == "page"]
    if not pages:
        raise RuntimeError("Chromium has no controllable page")
    return pages[0]


def cdp(method: str, params: dict | None = None, socket_url: str | None = None) -> dict:
    socket = websocket.create_connection(
        socket_url or active_page()["webSocketDebuggerUrl"],
        timeout=3,
        origin=DEVTOOLS_URL,
    )
    try:
        socket.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        response = json.loads(socket.recv())
        if "error" in response:
            raise RuntimeError(response["error"].get("message", "DevTools command failed"))
        return response.get("result", {})
    finally:
        socket.close()


def dispatch_key(socket_url: str, key: str, code: str, virtual_key: int) -> None:
    for event_type in ("keyDown", "keyUp"):
        cdp(
            "Input.dispatchKeyEvent",
            {
                "type": event_type,
                "key": key,
                "code": code,
                "windowsVirtualKeyCode": virtual_key,
                "nativeVirtualKeyCode": virtual_key,
            },
            socket_url,
        )


NEXT_CONTROL = r"""
(() => {
  const selectors = [
    'button[data-testid="control-button-skip-forward"]',
    '.ytp-next-button',
    'button[aria-label*="Next"]',
    'button[aria-label*="next"]',
    'button[title*="Next"]',
    'button[title*="next"]',
    '[role="button"][aria-label*="Next"]',
    '[role="button"][aria-label*="Skip"]'
  ];
  for (const selector of selectors) {
    const control = [...document.querySelectorAll(selector)].find((element) => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0;
    });
    if (control) { control.click(); return selector; }
  }
  return '';
})()
"""


def fire(gesture: str) -> str:
    page = active_page()
    socket_url = page["webSocketDebuggerUrl"]
    parsed = urlparse(page.get("url", ""))
    host = parsed.hostname or "local"
    app = (
        "youtube"
        if "youtube.com" in host
        else "spotify"
        if "spotify.com" in host
        else "plex"
        if "plex.tv" in host or parsed.port == 32400
        else "browser"
    )
    if gesture in {"up", "down"}:
        if app == "youtube" and parsed.path.startswith("/shorts"):
            key = "ArrowDown" if gesture == "up" else "ArrowUp"
            virtual_key = 40 if gesture == "up" else 38
            dispatch_key(socket_url, key, key, virtual_key)
        else:
            direction = 1 if gesture == "up" else -1
            cdp(
                "Runtime.evaluate",
                {
                    "expression": (
                        "window.scrollBy({top:window.innerHeight*"
                        f"{direction}*0.92,behavior:'smooth'}})"
                    )
                },
                socket_url,
            )
    elif gesture == "forward":
        result = cdp(
            "Runtime.evaluate",
            {"expression": NEXT_CONTROL, "returnByValue": True},
            socket_url,
        )
        clicked = result.get("result", {}).get("value", "")
        if not clicked:
            dispatch_key(socket_url, "MediaTrackNext", "MediaTrackNext", 176)
            if app == "youtube":
                dispatch_key(socket_url, "ArrowDown", "ArrowDown", 40)
    else:
        cdp(
            "Runtime.evaluate",
            {"expression": "window.history.back()"},
            socket_url,
        )
    print(f"gesture: {gesture} -> {app}", flush=True)
    return app


def open_capture() -> cv2.VideoCapture:
    src = SOURCE
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def main() -> None:
    print(
        f"gesture service: source={SOURCE} devtools={DEVTOOLS_URL} "
        f"travel={X_TRAVEL:.2f}/{Y_TRAVEL:.2f} window={WINDOW_S:.1f}s",
        flush=True,
    )
    hands = mp.solutions.hands.Hands(
        max_num_hands=1,
        model_complexity=0,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.45,
    )
    trail: deque[tuple[float, float, float]] = deque()  # (t, x, y)
    last_fire = 0.0
    cap = open_capture()
    skip = 0
    frames = 0
    hand_frames = 0
    last_status = time.monotonic()
    camera_online = False
    presence_state: bool | None = None
    last_presence_seen = 0.0
    last_presence_publish = 0.0
    status_hand_visible = False
    last_status_hand_seen = 0.0
    write_perception_status(camera_available=False, hand_present=False)
    while True:
        ok, frame = cap.read()
        if not ok:
            print("video source dropped; reconnecting...", flush=True)
            write_perception_status(camera_available=False, hand_present=False)
            if camera_online:
                publish_event(
                    "vision.camera_health",
                    entity_id="binary_sensor.x1_camera",
                    state="off",
                    previous_state="on",
                    attributes={"reason": "stream_unavailable"},
                )
                camera_online = False
                status_hand_visible = False
            if presence_state:
                publish_event(
                    "vision.presence_changed",
                    entity_id="binary_sensor.x1_visual_presence",
                    state="off",
                    previous_state="on",
                    attributes={"reason": "camera_unavailable"},
                    confidence=0.0,
                )
                presence_state = False
            cap.release()
            time.sleep(3)
            cap = open_capture()
            trail.clear()
            continue
        if not camera_online:
            publish_event(
                "vision.camera_health",
                entity_id="binary_sensor.x1_camera",
                state="on",
                previous_state="off",
                attributes={"stream": "x1_webcam"},
            )
            camera_online = True
            write_perception_status(camera_available=True, hand_present=False)
        skip = (skip + 1) % 2
        frames += 1
        if skip:  # every other frame is plenty and halves CPU
            continue
        frame = cv2.flip(frame, 1)  # mirror: selfie-consistent directions
        small = cv2.resize(frame, (640, 360))
        result = hands.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        now = time.monotonic()
        if now - last_status >= STATUS_INTERVAL_S:
            print(
                f"vision: frames={frames} hand_frames={hand_frames} "
                f"camera={'ok' if frames else 'waiting'}",
                flush=True,
            )
            frames = 0
            hand_frames = 0
            last_status = now
            write_perception_status(
                camera_available=camera_online,
                hand_present=status_hand_visible,
            )
        hand_visible = bool(result.multi_hand_landmarks)
        if hand_visible:
            last_status_hand_seen = now
        if hand_visible and not status_hand_visible:
            status_hand_visible = True
            write_perception_status(
                camera_available=camera_online,
                hand_present=True,
            )
        elif status_hand_visible and now - last_status_hand_seen >= 0.75:
            status_hand_visible = False
            write_perception_status(
                camera_available=camera_online,
                hand_present=False,
            )
        if hand_visible:
            last_presence_seen = now
            if presence_state is not True or now - last_presence_publish >= PRESENCE_HEARTBEAT_S:
                publish_event(
                    "vision.presence_changed",
                    entity_id="binary_sensor.x1_visual_presence",
                    state="on",
                    previous_state="off" if presence_state is not True else "on",
                    attributes={"signal": "hand_landmarks", "frames_stored": False},
                    confidence=0.86,
                )
                presence_state = True
                last_presence_publish = now
        elif presence_state is True and now - last_presence_seen >= PRESENCE_CLEAR_S:
            publish_event(
                "vision.presence_changed",
                entity_id="binary_sensor.x1_visual_presence",
                state="off",
                previous_state="on",
                attributes={"reason": "presence_timeout", "frames_stored": False},
                confidence=0.75,
            )
            presence_state = False
            last_presence_publish = now
        elif presence_state is False and now - last_presence_publish >= PRESENCE_HEARTBEAT_S:
            publish_event(
                "vision.presence_heartbeat",
                entity_id="binary_sensor.x1_visual_presence",
                state="off",
                previous_state="off",
                attributes={"frames_stored": False},
                confidence=0.75,
            )
            last_presence_publish = now

        if not hand_visible:
            trail.clear()
            continue
        hand_frames += 1
        landmarks = result.multi_hand_landmarks[0].landmark
        palm = [landmarks[index] for index in (0, 5, 9, 13, 17)]
        palm_x = sum(point.x for point in palm) / len(palm)
        palm_y = sum(point.y for point in palm) / len(palm)
        trail.append((now, palm_x, palm_y))
        while trail and now - trail[0][0] > WINDOW_S:
            trail.popleft()
        if now - last_fire < COOLDOWN_S or len(trail) < 5:
            continue
        dx = trail[-1][1] - trail[0][1]
        dy = trail[-1][2] - trail[0][2]
        gesture = None
        if abs(dy) >= Y_TRAVEL and abs(dy) > abs(dx) * 1.5:
            gesture = "up" if dy < 0 else "down"
        elif abs(dx) >= X_TRAVEL and abs(dx) > abs(dy) * 1.5:
            gesture = "forward" if dx > 0 else "back"
        if gesture:
            try:
                app = fire(gesture)
                write_perception_status(
                    camera_available=True,
                    hand_present=True,
                    last_gesture=gesture,
                    app=app,
                    gesture_timestamp=time.time(),
                )
                publish_event(
                    "vision.gesture",
                    attributes={
                        "gesture": gesture,
                        "app": app,
                        "frames_stored": False,
                        "action_execution": False,
                    },
                    confidence=0.82,
                )
                last_fire = now
            except Exception as error:
                print(f"gesture dispatch failed: {error}", flush=True)
            trail.clear()


if __name__ == "__main__":
    main()
