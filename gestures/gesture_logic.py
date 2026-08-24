"""Pure perception helpers shared by the worker and its simulation tests."""

from __future__ import annotations

from collections.abc import Sequence
from math import hypot


def open_hand(points: Sequence[tuple[float, float]], minimum_fingers: int = 3) -> bool:
    """Return true when at least three non-thumb fingertips extend from the wrist."""
    if len(points) < 21:
        return False
    wrist = points[0]
    extended = 0
    for tip_index, pip_index in ((8, 6), (12, 10), (16, 14), (20, 18)):
        tip = points[tip_index]
        pip = points[pip_index]
        tip_distance = hypot(tip[0] - wrist[0], tip[1] - wrist[1])
        pip_distance = hypot(pip[0] - wrist[0], pip[1] - wrist[1])
        if tip_distance >= pip_distance * 1.12:
            extended += 1
    return extended >= minimum_fingers


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
