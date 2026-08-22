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
from urllib.request import urlopen

import cv2
import mediapipe as mp
import websocket

SOURCE = os.environ.get("GESTURE_SOURCE", "rtsp://127.0.0.1:8556/x1_webcam")
DEVTOOLS_URL = os.environ.get("GESTURE_DEVTOOLS_URL", "http://127.0.0.1:9222")
# Fraction of frame the hand must travel to count as a swipe
X_TRAVEL = float(os.environ.get("GESTURE_X_TRAVEL", "0.20"))
Y_TRAVEL = float(os.environ.get("GESTURE_Y_TRAVEL", "0.18"))
WINDOW_S = float(os.environ.get("GESTURE_WINDOW_S", "0.9"))
COOLDOWN_S = float(os.environ.get("GESTURE_COOLDOWN_S", "1.2"))
STATUS_INTERVAL_S = float(os.environ.get("GESTURE_STATUS_INTERVAL_S", "15"))

def active_page_socket() -> str:
    with urlopen(f"{DEVTOOLS_URL}/json/list", timeout=3) as response:
        targets = json.load(response)
    pages = [target for target in targets if target.get("type") == "page"]
    if not pages:
        raise RuntimeError("Chromium has no controllable page")
    return pages[0]["webSocketDebuggerUrl"]


def cdp(method: str, params: dict | None = None) -> None:
    socket = websocket.create_connection(
        active_page_socket(), timeout=3, origin=DEVTOOLS_URL
    )
    try:
        socket.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        response = json.loads(socket.recv())
        if "error" in response:
            raise RuntimeError(response["error"].get("message", "DevTools command failed"))
    finally:
        socket.close()


def fire(gesture: str) -> None:
    if gesture in {"up", "down"}:
        direction = 1 if gesture == "up" else -1
        cdp(
            "Runtime.evaluate",
            {
                "expression": (
                    "window.scrollBy({top:window.innerHeight*"
                    f"{direction}*0.92,behavior:'smooth'}})"
                )
            },
        )
    elif gesture == "forward":
        for event_type in ("keyDown", "keyUp"):
            cdp(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "key": "ArrowDown",
                    "code": "ArrowDown",
                    "windowsVirtualKeyCode": 40,
                    "nativeVirtualKeyCode": 40,
                },
            )
    else:
        cdp("Runtime.evaluate", {"expression": "window.history.back()"})
    print(f"gesture: {gesture} -> Chromium", flush=True)


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
    while True:
        ok, frame = cap.read()
        if not ok:
            print("video source dropped; reconnecting...", flush=True)
            cap.release()
            time.sleep(3)
            cap = open_capture()
            trail.clear()
            continue
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
        if not result.multi_hand_landmarks:
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
                fire(gesture)
                last_fire = now
            except Exception as error:
                print(f"gesture dispatch failed: {error}", flush=True)
            trail.clear()


if __name__ == "__main__":
    main()
