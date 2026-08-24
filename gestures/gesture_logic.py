"""Pure perception helpers shared by the worker and its simulation tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import hypot
from urllib.parse import urlparse


STATIC_POSE_ACTIONS = {
    "pinch": "play_pause",
    "fist": "mute",
    "thumb_up": "volume_up",
    "thumb_down": "volume_down",
}


def find_frame_by_port(frame_tree: dict, port: int) -> dict | None:
    """Return the first frame using *port*, including nested child frames."""
    frame = frame_tree.get("frame", {})
    try:
        if urlparse(frame.get("url", "")).port == port:
            return frame
    except ValueError:
        pass
    for child in frame_tree.get("childFrames", ()):
        match = find_frame_by_port(child, port)
        if match:
            return match
    return None


@dataclass
class PoseLatch:
    """Require a stable pose and a full release before returning another action."""

    candidate: str | None = None
    started_at: float = 0.0
    armed: bool = True

    def update(
        self,
        pose: str,
        now: float,
        hold_seconds: float,
        can_fire: bool = True,
    ) -> str | None:
        action = STATIC_POSE_ACTIONS.get(pose)
        if not action:
            self.candidate = None
            self.armed = True
            return None
        if self.candidate != pose:
            self.candidate = pose
            self.started_at = now
            self.armed = True
            return None
        if self.armed and can_fire and now - self.started_at >= hold_seconds:
            self.armed = False
            return action
        return None


def _extended_fingers(points: Sequence[tuple[float, float]]) -> int:
    wrist = points[0]
    extended = 0
    for tip_index, pip_index in ((8, 6), (12, 10), (16, 14), (20, 18)):
        tip = points[tip_index]
        pip = points[pip_index]
        if hypot(tip[0] - wrist[0], tip[1] - wrist[1]) >= hypot(
            pip[0] - wrist[0], pip[1] - wrist[1]
        ) * 1.12:
            extended += 1
    return extended


def open_hand(points: Sequence[tuple[float, float]], minimum_fingers: int = 3) -> bool:
    """Return true when at least three non-thumb fingertips extend from the wrist."""
    if len(points) < 21:
        return False
    return _extended_fingers(points) >= minimum_fingers


def hand_pose(points: Sequence[tuple[float, float]]) -> str:
    """Classify a deliberately small set of conservative static hand poses."""
    if len(points) < 21:
        return "unknown"
    wrist = points[0]
    palm_size = max(0.05, hypot(points[9][0] - wrist[0], points[9][1] - wrist[1]))
    pinch_distance = hypot(points[4][0] - points[8][0], points[4][1] - points[8][1])
    extended = _extended_fingers(points)
    if extended >= 1 and pinch_distance / palm_size <= 0.42:
        return "pinch"
    if extended >= 3:
        return "open"
    index_extended = hypot(points[8][0] - wrist[0], points[8][1] - wrist[1]) >= hypot(
        points[6][0] - wrist[0], points[6][1] - wrist[1]
    ) * 1.12
    middle_extended = hypot(points[12][0] - wrist[0], points[12][1] - wrist[1]) >= hypot(
        points[10][0] - wrist[0], points[10][1] - wrist[1]
    ) * 1.12
    if extended == 2 and index_extended and middle_extended:
        return "two_finger"
    thumb_dx = abs(points[4][0] - wrist[0])
    if extended == 0 and thumb_dx <= palm_size * 0.9:
        if points[4][1] <= wrist[1] - palm_size * 0.65:
            return "thumb_up"
        if points[4][1] >= wrist[1] + palm_size * 0.35:
            return "thumb_down"
        return "fist"
    return "other"


def classify_swipe(
    trail: Sequence[tuple[float, float, float]],
    *,
    x_travel: float,
    y_travel: float,
    minimum_speed: float,
) -> str | None:
    """Classify the strongest recent motion segment, ignoring stationary dwell."""
    if len(trail) < 5:
        return None
    end_time, end_x, end_y = trail[-1]
    best: tuple[float, str] | None = None
    for start_time, start_x, start_y in trail[:-3]:
        duration = end_time - start_time
        if duration < 0.12 or duration > 0.78:
            continue
        dx = end_x - start_x
        dy = end_y - start_y
        speed = hypot(dx, dy) / duration
        if speed < minimum_speed:
            continue
        gesture = None
        travel = 0.0
        if abs(dy) >= y_travel and abs(dy) > abs(dx) * 1.5:
            gesture = "up" if dy < 0 else "down"
            travel = abs(dy) / y_travel
        elif abs(dx) >= x_travel and abs(dx) > abs(dy) * 1.5:
            gesture = "forward" if dx > 0 else "back"
            travel = abs(dx) / x_travel
        if gesture:
            score = travel + min(speed, 2.0) * 0.1
            if best is None or score > best[0]:
                best = (score, gesture)
    return best[1] if best else None
