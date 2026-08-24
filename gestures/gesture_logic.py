"""Pure perception helpers shared by the worker and its simulation tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import hypot


STATIC_POSE_ACTIONS = {
    "pinch": "play_pause",
    "fist": "mute",
    "thumb_up": "volume_up",
    "thumb_down": "volume_down",
}


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
    """Classify one dominant, deliberate palm trajectory in normalized coordinates."""
    if len(trail) < 5:
        return None
    duration = trail[-1][0] - trail[0][0]
    if duration <= 0:
        return None
    dx = trail[-1][1] - trail[0][1]
    dy = trail[-1][2] - trail[0][2]
    distance = hypot(dx, dy)
    if distance / duration < minimum_speed:
        return None
    if abs(dy) >= y_travel and abs(dy) > abs(dx) * 1.5:
        return "up" if dy < 0 else "down"
    if abs(dx) >= x_travel and abs(dx) > abs(dy) * 1.5:
        return "forward" if dx > 0 else "back"
    return None
