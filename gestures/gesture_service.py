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
import subprocess
import time
from collections import deque

import cv2
import mediapipe as mp

SOURCE = os.environ.get("GESTURE_SOURCE", "rtsp://127.0.0.1:8556/x1_webcam")
DISPLAY = os.environ.get("DISPLAY", ":0")
# Fraction of frame the hand must travel to count as a swipe
X_TRAVEL = float(os.environ.get("GESTURE_X_TRAVEL", "0.30"))
Y_TRAVEL = float(os.environ.get("GESTURE_Y_TRAVEL", "0.28"))
WINDOW_S = float(os.environ.get("GESTURE_WINDOW_S", "0.6"))
COOLDOWN_S = float(os.environ.get("GESTURE_COOLDOWN_S", "1.4"))

ACTIONS = {
    "up": ["xdotool", "click", "--repeat", "4", "5"],       # scroll: next Short
    "down": ["xdotool", "click", "--repeat", "4", "4"],     # scroll: previous
    "forward": ["xdotool", "key", "--clearmodifiers", "Down"],      # skip/next
    "back": ["xdotool", "key", "--clearmodifiers", "alt+Left"],     # go back
}


def fire(gesture: str) -> None:
    env = dict(os.environ, DISPLAY=DISPLAY)
    subprocess.Popen(ACTIONS[gesture], env=env)
    print(f"gesture: {gesture}", flush=True)


def open_capture() -> cv2.VideoCapture:
    src = SOURCE
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def main() -> None:
    hands = mp.solutions.hands.Hands(
        max_num_hands=1,
        model_complexity=0,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )
    trail: deque[tuple[float, float, float]] = deque()  # (t, x, y)
    last_fire = 0.0
    cap = open_capture()
    skip = 0
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
        if skip:  # every other frame is plenty and halves CPU
            continue
        frame = cv2.flip(frame, 1)  # mirror: selfie-consistent directions
        small = cv2.resize(frame, (480, 270))
        result = hands.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        now = time.monotonic()
        if not result.multi_hand_landmarks:
            trail.clear()
            continue
        wrist = result.multi_hand_landmarks[0].landmark[0]
        trail.append((now, wrist.x, wrist.y))
        while trail and now - trail[0][0] > WINDOW_S:
            trail.popleft()
        if now - last_fire < COOLDOWN_S or len(trail) < 4:
            continue
        dx = trail[-1][1] - trail[0][1]
        dy = trail[-1][2] - trail[0][2]
        gesture = None
        if abs(dy) >= Y_TRAVEL and abs(dy) > abs(dx) * 1.5:
            gesture = "up" if dy < 0 else "down"
        elif abs(dx) >= X_TRAVEL and abs(dx) > abs(dy) * 1.5:
            gesture = "forward" if dx > 0 else "back"
        if gesture:
            fire(gesture)
            last_fire = now
            trail.clear()


if __name__ == "__main__":
    main()
